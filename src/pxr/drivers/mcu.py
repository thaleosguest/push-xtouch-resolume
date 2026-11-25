"""Driver Mackie Control (X-Touch, et plus tard X-Touch Extender = 2ᵉ instance avec sysex_id 0x15).

Valeurs reprises de LV1-workspace/xtouch-lv1 (validées le 14/10/2026) et du spike 3 (25/11/2026) :
  faders : pitch bend canaux 0-7 + 8 (master), 14 bits ; toucher = notes 0x68-0x70 (vérifiés)
  écrans : SysEx F0 00 00 66 <id> 12 <pos> <ascii> F7 (ligne 2 à partir de 56) ; couleurs <id> 72 c1..c8 (vérifiés)
  boutons de tranche : Rec 0x00, Solo 0x08, Mute 0x10, Select 0x18 (+ tranche) ; LED = même note 127/0
  encodeurs : CC 0x10-0x17 relatifs (bit 6 = sens) ; couronnes : CC 0x30-0x37 (vérifiés)
Les autres notes suivent la norme Mackie (non vérifiées sur la X-Touch, sauf mention dans xtouch-lv1).
Règle xtouch-lv1 : renvoyer aussitôt la position d'un fader manipulé (écho), sinon il revient en arrière.
"""
from __future__ import annotations

import time
import unicodedata
from typing import Callable

import mido

from . import ControlEvent

NOTE_NAMES: dict[int, str] = {}
for _base, _prefix in ((0x00, "rec"), (0x08, "solo"), (0x10, "mute"), (0x18, "select"), (0x20, "vpot")):
    NOTE_NAMES.update({_base + i: f"{_prefix}{i + 1}" for i in range(8)})
NOTE_NAMES.update({
    0x28: "track", 0x29: "send", 0x2A: "pan", 0x2B: "plugin", 0x2C: "eq", 0x2D: "inst",
    0x2E: "bank_left", 0x2F: "bank_right", 0x30: "channel_left", 0x31: "channel_right",   # vérifiés (xtouch-lv1)
    0x32: "flip", 0x33: "global_view", 0x34: "display", 0x35: "smpte",
    **{0x36 + i: f"f{i + 1}" for i in range(8)},
    0x3E: "midi_tracks", 0x3F: "inputs", 0x40: "audio_tracks", 0x41: "audio_inst", 0x42: "aux", 0x43: "buses",
    0x44: "outputs", 0x45: "user", 0x46: "shift", 0x47: "option", 0x48: "control", 0x49: "alt",
    0x4A: "read", 0x4B: "write", 0x4C: "trim", 0x4D: "touch_mode", 0x4E: "latch", 0x4F: "group",
    0x50: "save", 0x51: "undo", 0x52: "cancel", 0x53: "enter",
    0x54: "marker", 0x55: "nudge", 0x56: "cycle", 0x57: "drop", 0x58: "replace", 0x59: "click", 0x5A: "solo_all",
    0x5B: "rewind", 0x5C: "forward", 0x5D: "stop", 0x5E: "play", 0x5F: "record",
    0x60: "up", 0x61: "down", 0x62: "left", 0x63: "right", 0x64: "zoom", 0x65: "scrub",
})
NOTE_OF = {v: k for k, v in NOTE_NAMES.items()}
TOUCH_FIRST, TOUCH_MASTER = 0x68, 0x70
CC_VPOT, CC_RING, CC_JOG = 0x10, 0x30, 0x3C

FADER_TOL = 48 / 16383       # écart sous lequel on ne bouge pas le moteur (xtouch-lv1)
HOLD_AFTER_MOVE = 0.4        # s : un fader bougé sans toucher détecté reste « tenu » ce temps-là


def ascii7(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")


class McuDriver:
    def __init__(self, name: str, send: Callable[[mido.Message], None], emit: Callable[[ControlEvent], None],
                 sysex_id: int = 0x14, has_master: bool = True) -> None:
        self.source = name
        self.send, self.emit = send, emit
        self.hdr = [0x00, 0x00, 0x66, sysex_id]
        self.has_master = has_master
        self.n_faders = 9 if has_master else 8
        self.touched = [False] * 9
        self.last_move = [0.0] * 9
        self._faders: list[float | None] = [None] * 9
        self._sent_faders: list[float | None] = [None] * 9
        self._lcd = [" " * 56, " " * 56]
        self._sent_lcd: list[str | None] = [None, None]
        self._colors = [7] * 8
        self._sent_colors: list[int] | None = None
        self._leds: dict[int, bool] = {}
        self._sent_leds: dict[int, bool] = {}
        self._rings = [0] * 8
        self._sent_rings: list[int | None] = [None] * 8
        self.now = time.monotonic

    # ─── connexion ───────────────────────────────────────────────────────
    def on_open(self) -> None:
        """Contrôleur (re)connecté : on renvoie tout, sauf les faders tenus."""
        self._sent_lcd = [None, None]
        self._sent_colors = None
        self._sent_leds.clear()
        self._sent_rings = [None] * 8
        self._sent_faders = [None] * 9

    def on_close(self) -> None:
        self.touched = [False] * 9

    def held(self, i: int) -> bool:
        return self.touched[i] or (self.now() - self.last_move[i]) < HOLD_AFTER_MOVE

    # ─── entrées ─────────────────────────────────────────────────────────
    def handle_midi(self, m: mido.Message) -> None:
        for ev in self.parse(m):
            self.emit(ev)

    def parse(self, m: mido.Message) -> list[ControlEvent]:
        s = self.source
        if m.type == "pitchwheel" and m.channel < self.n_faders:
            v14 = m.pitch + 8192
            i = m.channel
            self.last_move[i] = self.now()
            self.send(mido.Message("pitchwheel", channel=i, pitch=m.pitch))   # écho Mackie
            self._sent_faders[i] = v14 / 16383
            return [ControlEvent(s, "fader", "master" if i == 8 else f"fader{i + 1}", x=i, value=v14 / 16383)]
        if m.type in ("note_on", "note_off"):
            pressed = 1 if (m.type == "note_on" and m.velocity) else 0
            if TOUCH_FIRST <= m.note <= TOUCH_MASTER:
                i = m.note - TOUCH_FIRST
                self.touched[i] = bool(pressed)
                if not pressed:
                    self.last_move[i] = self.now()
                return [ControlEvent(s, "touch", "master" if i == 8 else f"fader{i + 1}", x=i, value=pressed)]
            name = NOTE_NAMES.get(m.note, f"note{m.note}")
            return [ControlEvent(s, "button", name, x=m.note & 0x07, value=pressed)]
        if m.type == "control_change":
            if CC_VPOT <= m.control < CC_VPOT + 8:
                d = (m.value & 0x0F) * (-1 if m.value & 0x40 else 1)
                return [ControlEvent(s, "encoder", f"enc{m.control - CC_VPOT + 1}", x=m.control - CC_VPOT, value=d)]
            if m.control == CC_JOG:
                return [ControlEvent(s, "encoder", "jog", value=-(m.value & 0x0F) if m.value & 0x40 else m.value & 0x0F)]
        return []

    # ─── rendu ───────────────────────────────────────────────────────────
    def set_fader(self, i: int, v: float | None) -> None:
        if i < self.n_faders:
            self._faders[i] = None if v is None else min(1.0, max(0.0, v))

    def set_strip(self, i: int, top: str, bottom: str, color: int) -> None:
        for row, text in ((0, top), (1, bottom)):
            t = ascii7(text)[:7].ljust(7)
            line = self._lcd[row]
            self._lcd[row] = line[:7 * i] + t + line[7 * i + 7:]
        self._colors[i] = color & 7

    def set_led(self, name: str, on: bool) -> None:
        if name in NOTE_OF:
            self._leds[NOTE_OF[name]] = bool(on)

    def set_ring(self, i: int, v: float | None) -> None:
        """Couronne en point unique (mode 0) : 1..11 ; None = éteinte."""
        self._rings[i] = 0 if v is None else 1 + round(min(1.0, max(0.0, v)) * 10)

    def clear(self) -> None:
        for i in range(8):
            self.set_strip(i, "", "", 7)
            self.set_ring(i, None)
        for name in NOTE_OF:
            self.set_led(name, False)
        for i in range(self.n_faders):
            self.set_fader(i, 0.0)

    def flush(self) -> None:
        for row in (0, 1):
            if self._sent_lcd[row] != self._lcd[row]:
                data = [min(0x7E, max(0x20, ord(c))) for c in self._lcd[row]]
                self.send(mido.Message("sysex", data=self.hdr + [0x12, 0 if row == 0 else 56] + data))
                self._sent_lcd[row] = self._lcd[row]
        if self._sent_colors != self._colors:
            self.send(mido.Message("sysex", data=self.hdr + [0x72] + list(self._colors)))
            self._sent_colors = list(self._colors)
        for note, on in self._leds.items():
            if self._sent_leds.get(note) != on:
                self.send(mido.Message("note_on", note=note, velocity=127 if on else 0))
                self._sent_leds[note] = on
        for i, v in enumerate(self._rings):
            if self._sent_rings[i] != v:
                self.send(mido.Message("control_change", control=CC_RING + i, value=v))
                self._sent_rings[i] = v
        for i in range(self.n_faders):
            v, sent = self._faders[i], self._sent_faders[i]
            if v is None or self.held(i):
                continue
            if sent is None or abs(sent - v) > FADER_TOL or (v == 0) != (sent == 0):
                self.send(mido.Message("pitchwheel", channel=i, pitch=round(v * 16383) - 8192))
                self._sent_faders[i] = v
