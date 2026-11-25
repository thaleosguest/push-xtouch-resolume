"""Driver Push 2, partie MIDI : pads, boutons, encodeurs, touch strip, LED (palette + animations), horloge.

Référence : Ableton Push 2 MIDI and Display Interface Manual rev 1.1. « vérifié » = vu au spike 4 sur le
Push 2 de test (25/11/2026) ; le reste vient du manuel.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

import mido

from . import ControlEvent

# CC des boutons (table du manuel). Vérifiés : scènes 36-43, 87-90.
BUTTON_CC: dict[int, str] = {
    3: "tap_tempo", 9: "metronome", 28: "master", 29: "stop_clip", 30: "setup", 31: "layout", 35: "convert",
    44: "left", 45: "right", 46: "up", 47: "down", 48: "select", 49: "shift", 50: "note", 51: "session",
    52: "add_device", 53: "add_track", 54: "octave_down", 55: "octave_up", 56: "repeat", 57: "accent",
    58: "scale", 59: "user", 60: "mute", 61: "solo", 62: "page_left", 63: "page_right", 85: "play",
    86: "record", 87: "new", 88: "duplicate", 89: "automate", 90: "fixed_length", 110: "device",
    111: "browse", 112: "mix", 113: "clip", 116: "quantize", 117: "double_loop", 118: "delete", 119: "undo",
}
BUTTON_CC.update({20 + i: f"lower{i + 1}" for i in range(8)})       # sous l'écran
BUTTON_CC.update({102 + i: f"upper{i + 1}" for i in range(8)})      # au-dessus de l'écran
BUTTON_CC.update({43 - i: f"scene{i + 1}" for i in range(8)})       # scene1 = en haut (CC 43), vérifié
CC_OF_BUTTON = {v: k for k, v in BUTTON_CC.items()}

# Boutons à LED RVB (valeur = index de palette). Les autres sont blancs (valeur = niveau de la palette blanche).
RGB_BUTTONS = {f"lower{i}" for i in range(1, 9)} | {f"upper{i}" for i in range(1, 9)} | {f"scene{i}" for i in range(1, 9)}

# Encodeurs relatifs (complément à deux sur 7 bits). Vérifiés : 71-73, 15.
ENCODER_CC = {71 + i: f"enc{i + 1}" for i in range(8)} | {79: "master", 14: "tempo", 15: "swing"}
# Notes de toucher. Vérifiés : 0, 1, 2 (encodeurs 1-3), 9 (swing), 12 (touch strip). 8 et 10 : déduits.
TOUCH_NOTE = {i: f"enc{i + 1}" for i in range(8)} | {8: "master", 9: "swing", 10: "tempo", 12: "touchstrip"}

# Animations : canal MIDI du message LED (manuel, vérifié visuellement au spike 4)
ANIM_CHANNEL = {"none": 0, "pulse": 9, "blink": 14}   # pulsation / clignotement à la noire

WHITE_ON, WHITE_DIM, OFF = 127, 16, 0
SYSEX_HDR = [0x00, 0x21, 0x1D, 0x01, 0x01]

# Entrées de palette jamais réécrites : 0 = noir ; 16 / 48 / 127 = niveaux de la palette blanche utilisés par
# les boutons blancs ; 122-127 = entrées documentées par le manuel.
RESERVED_INDEXES = frozenset({0, 16, 48, 122, 123, 124, 125, 126, 127})

RGB = tuple[int, int, int]


def palette_entry_sysex(i: int, rgb: RGB, w: int | None = None) -> mido.Message:
    """0x03 Set LED Color Palette Entry : i, puis r, g, b, w en (7 bits de poids faible, 1 bit de poids fort)."""
    r, g, b = rgb
    if w is None:
        w = round(0.3 * r + 0.59 * g + 0.11 * b)
    data = [0x03, i]
    for v in (r, g, b, w):
        data += [v & 0x7F, (v >> 7) & 0x01]
    return mido.Message("sysex", data=SYSEX_HDR + data)


class PaletteAllocator:
    """Couleurs RVB exactes sur les LED : chaque couleur nouvelle est écrite dans une entrée libre de la palette
    (SysEx 0x03). Spike 6 : les entrées sombres de la palette par défaut restent trop lumineuses, alors qu'une
    entrée écrite à 16 % (niveau 40) donne un « atténué » net."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.free = [i for i in range(1, 128) if i not in RESERVED_INDEXES]
        self.by_rgb: dict[RGB, int] = {}
        self.rgb_of: dict[int, RGB] = {}
        self.writes: list[tuple[int, RGB]] = []

    def index(self, rgb: RGB, in_use: set[int]) -> int:
        rgb = tuple(max(0, min(255, int(c))) for c in rgb)  # type: ignore[assignment]
        if max(rgb) == 0:
            return 0
        if rgb in self.by_rgb:
            i = self.by_rgb.pop(rgb)
            self.by_rgb[rgb] = i            # plus récent en dernier (ordre d'insertion = LRU)
            return i
        if self.free:
            i = self.free.pop(0)
        else:                                # palette pleine : on recycle la couleur la plus ancienne non affichée
            old = next((c for c, j in self.by_rgb.items() if j not in in_use), None)
            if old is None:
                from ..colors import push2_index
                return push2_index(rgb)      # > 118 couleurs à l'écran : couleur par défaut la plus proche
            i = self.by_rgb.pop(old)
        self.by_rgb[rgb] = i
        self.rgb_of[i] = rgb
        self.writes.append((i, rgb))
        return i


def pad_note(row: int, col: int) -> int:
    """row 0 = rangée du bas, col 0 = à gauche (vérifié au spike 4)."""
    return 36 + 8 * row + col


class MidiClock:
    """Horloge MIDI (24 ppq) vers le Push : les animations de LED ne tournent qu'avec un Start puis des F8."""

    def __init__(self, send: Callable[[mido.Message], None]) -> None:
        self.send = send
        self.bpm = 120.0
        self._run = threading.Event()
        self._th: threading.Thread | None = None

    def start(self) -> None:
        if self._th and self._th.is_alive():
            return
        self._run.set()
        self._th = threading.Thread(target=self._loop, name="push2-clock", daemon=True)
        self._th.start()

    def stop(self) -> None:
        self._run.clear()
        if self._th:
            self._th.join(1)

    def _loop(self) -> None:
        self.send(mido.Message("start"))
        nxt = time.perf_counter()
        while self._run.is_set():
            self.send(mido.Message("clock"))
            nxt += 60.0 / (max(20.0, min(500.0, self.bpm)) * 24)
            delay = nxt - time.perf_counter()
            if delay < -0.1:  # retard (veille…) : on recale au lieu de rattraper en rafale
                nxt = time.perf_counter()
            elif delay > 0:
                time.sleep(delay)


class Push2Driver:
    source = "push2"

    def __init__(self, send: Callable[[mido.Message], None], emit: Callable[[ControlEvent], None]) -> None:
        self.send, self.emit = send, emit
        self.clock = MidiClock(send)
        self.palette = PaletteAllocator()
        self._pads: dict[tuple[int, int], tuple[RGB, int, RGB]] = {}          # désiré : (RVB, canal anim, RVB 2)
        self._sent_pads: dict[tuple[int, int], tuple[int, int, int]] = {}     # envoyé : (index, canal, index 2)
        self._buttons: dict[str, tuple[str, object]] = {}                    # ("rgb", RVB) | ("white", niveau)
        self._sent_buttons: dict[str, int] = {}

    # ─── connexion ───────────────────────────────────────────────────────
    def on_open(self) -> None:
        """(Re)connexion : le Push a pu redémarrer avec sa palette d'usine -> on réécrit tout."""
        self._sent_pads.clear()
        self._sent_buttons.clear()
        self.palette.reset()
        self.clock.start()

    def on_close(self) -> None:
        self.clock.stop()

    # ─── entrées ─────────────────────────────────────────────────────────
    def handle_midi(self, m: mido.Message) -> None:
        for ev in self.parse(m):
            self.emit(ev)

    def parse(self, m: mido.Message) -> list[ControlEvent]:
        s = self.source
        if m.type in ("note_on", "note_off"):
            vel = m.velocity if m.type == "note_on" else 0
            if 36 <= m.note <= 99:
                r, c = divmod(m.note - 36, 8)
                return [ControlEvent(s, "pad", "pad", x=c, y=r, value=vel)]
            if m.note in TOUCH_NOTE:
                return [ControlEvent(s, "touch", TOUCH_NOTE[m.note], value=1 if vel else 0)]
        elif m.type == "control_change":
            if m.control in ENCODER_CC:
                delta = m.value - 128 if m.value >= 64 else m.value
                return [ControlEvent(s, "encoder", ENCODER_CC[m.control], value=delta)]
            if m.control in BUTTON_CC:
                return [ControlEvent(s, "button", BUTTON_CC[m.control], value=1 if m.value else 0)]
        elif m.type == "pitchwheel":
            return [ControlEvent(s, "touchstrip", "touchstrip", value=(m.pitch + 8192) / 16383)]
        return []

    # ─── rendu (état désiré) ─────────────────────────────────────────────
    def set_pad(self, row: int, col: int, rgb: RGB, anim: str = "none", rgb2: RGB = (0, 0, 0)) -> None:
        """Pad en couleur RVB exacte ; anim = "pulse"/"blink" alterne avec rgb2 au tempo de l'horloge."""
        ch = ANIM_CHANNEL[anim]
        self._pads[(row, col)] = (rgb, ch, rgb2 if ch else (0, 0, 0))

    def set_button_rgb(self, name: str, rgb: RGB) -> None:
        """Bouton à LED RVB (boutons autour de l'écran, scènes)."""
        if name in CC_OF_BUTTON:
            self._buttons[name] = ("rgb", rgb)

    def set_button(self, name: str, level: int) -> None:
        """Bouton blanc : index de la palette blanche (0 éteint, 16 faible, 127 plein)."""
        if name in CC_OF_BUTTON:
            self._buttons[name] = ("white", level)

    def clear(self) -> None:
        for r in range(8):
            for c in range(8):
                self.set_pad(r, c, (0, 0, 0))
        for name in CC_OF_BUTTON:
            self._buttons[name] = ("white", OFF)

    def restore_palette(self) -> None:
        """À l'arrêt : remet les entrées réécrites à leur valeur d'usine (lue au spike 4)."""
        from ..colors import push2_palette_entries
        default = push2_palette_entries()
        for i in list(self.palette.rgb_of):
            r, g, b, w = default[i]
            self.send(palette_entry_sysex(i, (r, g, b), w))
        if self.palette.rgb_of:
            self.send(mido.Message("sysex", data=SYSEX_HDR + [0x05]))
        self.palette.reset()

    def flush(self) -> None:
        in_use = {i for st in self._sent_pads.values() for i in (st[0], st[2])} | set(self._sent_buttons.values())
        pads = {}
        for key, (rgb, ch, rgb2) in self._pads.items():
            st = (self.palette.index(rgb, in_use), ch, self.palette.index(rgb2, in_use) if ch else 0)
            in_use.update((st[0], st[2]))
            pads[key] = st
        buttons = {}
        for name, (kind, v) in self._buttons.items():
            buttons[name] = self.palette.index(v, in_use) if kind == "rgb" else int(v)  # type: ignore[arg-type]
            in_use.add(buttons[name])
        if self.palette.writes:                      # couleurs nouvelles : d'abord la palette, puis les LED
            for i, rgb in self.palette.writes:
                self.send(palette_entry_sysex(i, rgb))
            self.send(mido.Message("sysex", data=SYSEX_HDR + [0x05]))   # Reapply Color Palette
            self.palette.writes.clear()
        for key, st in pads.items():
            if self._sent_pads.get(key) != st:
                note = pad_note(*key)
                self.send(mido.Message("note_on", note=note, velocity=st[0], channel=0))
                if st[1]:
                    self.send(mido.Message("note_on", note=note, velocity=st[2], channel=st[1]))
                self._sent_pads[key] = st
        for name, v in buttons.items():
            if self._sent_buttons.get(name) != v:
                self.send(mido.Message("control_change", control=CC_OF_BUTTON[name], value=v))
                self._sent_buttons[name] = v
