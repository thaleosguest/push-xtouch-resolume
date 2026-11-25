"""Moteur de mapping : entrées des contrôleurs -> actions Arena, état Arena -> retours visuels.

Arena reste la source de vérité : les retours ne montrent que l'état reçu d'Arena. Seules exceptions :
les faders tenus (le driver MCU ne les bouge pas) et l'accumulation des crans d'encodeur en attente d'envoi.
Le moteur ne parle jamais au réseau : il passe par ArenaActions (ArenaClient, ou FakeArena en test / --dry-run).
Arena hors ligne : les contrôleurs affichent « ARENA OFFLINE » et les commandes ne partent pas.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Protocol

from .colors import RGB, rgb_to_xtouch, scale
from .config import Mapping
from .drivers import ControlEvent
from .drivers.mcu import McuDriver
from .drivers.push2 import OFF, WHITE_DIM, WHITE_ON, Push2Driver
from .drivers.push2_display import DisplayModel
from .state import Clip, Layer, Param, StateModel

log = logging.getLogger(__name__)


class ArenaActions(Protocol):
    connected: bool

    def set_param(self, pid: int, value: Any) -> None: ...
    def trigger(self, path: str, value: bool | None = None) -> None: ...
    def fire_event(self, pid: int) -> None: ...


class McuUnit:
    def __init__(self, driver: McuDriver, strip_offset: int = 0) -> None:
        self.driver, self.strip_offset = driver, strip_offset


class CommandThrottle:
    """Valeurs continues (faders, encodeurs) : seule la dernière valeur de chaque paramètre est gardée, et un même
    paramètre part au plus `rate_hz` fois par seconde. Une valeur retenue part dès que son créneau s'ouvre."""

    def __init__(self, rate_hz: float = 60.0) -> None:
        self.min_dt = 1.0 / rate_hz
        self.pending: dict[int, Any] = {}
        self.last_sent: dict[int, float] = {}

    def put(self, pid: int, value: Any) -> None:
        self.pending[pid] = value

    def get(self, pid: int, default: Any) -> Any:
        return self.pending.get(pid, default)

    def due(self, now: float) -> list[tuple[int, Any]]:
        out = []
        for pid in list(self.pending):
            if now - self.last_sent.get(pid, float("-inf")) >= self.min_dt:
                out.append((pid, self.pending.pop(pid)))
                self.last_sent[pid] = now
        return out

    def clear(self) -> None:
        self.pending.clear()


class Engine:
    def __init__(self, state: StateModel, arena: ArenaActions, mapping: Mapping,
                 push: Push2Driver | None = None, display: Any = None, mcus: list[McuUnit] | None = None,
                 command_rate_hz: float = 60.0) -> None:
        self.state, self.arena, self.map = state, arena, mapping
        self.push, self.display = push, display
        self.mcus = {u.driver.source: u for u in (mcus or [])}
        self.throttle = CommandThrottle(command_rate_hz)
        self.now = time.monotonic
        self.page = mapping.push2.default_page
        self.bank = 0            # 1ʳᵉ colonne affichée sur les pads (0-based)
        self.layer_off = 0       # 1ʳᵉ couche sur la rangée du bas
        self.fx_off = 0          # 1er effet composition sur les boutons du haut (page FX)
        self.fx_sel = 0          # effet composition réglé par les encodeurs
        self.mcu_off = 0         # 1ʳᵉ couche sur la tranche 1 de la X-Touch
        self.held_pads: dict[tuple[int, int], str] = {}   # pad tenu -> chemin à relâcher
        for path in {mapping.push2.layer_param, mapping.mcu.fader_param, mapping.mcu.encoder_param}:
            state.track_layer_path(path)

    # ─── outils ──────────────────────────────────────────────────────────
    def layer_color(self, layer: Layer) -> RGB:
        """Couleur de LED de la couche : son colorid Arena s'il en a un, sinon couleur de repli."""
        c = self.map.colors
        if layer.colorid is not None and str(layer.colorid.value) in c.colorid_led:
            return c.colorid_led[str(layer.colorid.value)]
        return c.layer_fallback[(layer.index - 1) % len(c.layer_fallback)]

    def clip_color(self, clip: Clip, layer: Layer) -> RGB:
        """Couleur de LED du clip : son colorid s'il en a un, sinon celle de sa couche."""
        c = self.map.colors
        if clip.colorid is not None and str(clip.colorid.value) in c.colorid_led:
            return c.colorid_led[str(clip.colorid.value)]
        return self.layer_color(layer)

    def _norm_of(self, p: Param) -> float:
        v = self.throttle.get(p.id, p.value)
        if p.is_range:
            return min(1.0, max(0.0, (float(v) - p.min) / (p.max - p.min)))
        return 0.0

    def set_norm(self, p: Param | None, x: float) -> None:
        if p is not None and p.is_range:
            self.throttle.put(p.id, p.denorm(x))

    def nudge(self, p: Param | None, delta: float) -> None:
        if p is not None and p.is_range:
            self.set_norm(p, self._norm_of(p) + delta)

    def toggle(self, p: Param | None) -> None:
        if p is not None:
            self.arena.set_param(p.id, not bool(p.value))

    def flush_actions(self) -> None:
        """Envoie les valeurs continues dont le créneau est ouvert (appelé ~à command_rate_hz)."""
        if not self.online:
            self.throttle.clear()        # pas de rejeu de vieux mouvements à la reconnexion
            return
        for pid, v in self.throttle.due(self.now()):
            self.arena.set_param(pid, v)

    @property
    def online(self) -> bool:
        return bool(getattr(self.arena, "connected", True)) and self.state.loaded

    def layer_at(self, i: int) -> Layer | None:
        return self.state.layers[i] if 0 <= i < len(self.state.layers) else None

    @property
    def n_layer_rows(self) -> int:
        return max(0, min(len(self.state.layers) - self.layer_off, self.map.push2.max_layer_rows, 8))

    @property
    def column_row(self) -> int | None:
        n = self.n_layer_rows
        return n if self.map.push2.column_row and n < 8 else None

    def on_structure(self) -> None:
        """Composition rechargée : on borne les décalages."""
        s = self.state
        self.bank = min(self.bank, max(0, len(s.columns) - 1)) // 8 * 8
        self.layer_off = min(self.layer_off, max(0, len(s.layers) - 1))
        self.mcu_off = min(self.mcu_off, max(0, len(s.layers) - 1))
        self.fx_sel = min(self.fx_sel, max(0, len(s.effects) - 1))

    def _play_toggle(self) -> None:
        p = self.state.play_state
        if p is not None:
            self.arena.set_param(p.id, "Pause" if p.value == "Play" else "Play")

    def _tap(self) -> None:
        if self.state.tempo_tap is not None:
            self.arena.fire_event(self.state.tempo_tap.id)

    # ─── entrées ─────────────────────────────────────────────────────────
    def handle(self, ev: ControlEvent) -> None:
        if not self.online:
            self.held_pads.clear()
            return
        if ev.source == "push2":
            self._push_event(ev)
        elif ev.source in self.mcus:
            self._mcu_event(ev, self.mcus[ev.source])

    def _push_event(self, ev: ControlEvent) -> None:
        m, s = self.map.push2, self.state
        if ev.kind == "pad":
            key = (ev.y, ev.x)
            if ev.value == 0:
                path = self.held_pads.pop(key, None)
                if path:
                    self.arena.trigger(path, False)
                return
            path = None
            if ev.y < self.n_layer_rows:
                layer = self.layer_at(self.layer_off + ev.y)
                col = self.bank + ev.x
                if layer and col < len(layer.clips):
                    path = f"/composition/clips/by-id/{layer.clips[col].id}/connect"
            elif ev.y == self.column_row:
                col = self.bank + ev.x
                if col < len(s.columns):
                    path = f"/composition/columns/by-id/{s.columns[col].id}/connect"
            if path:
                self.arena.trigger(path, True)
                self.held_pads[key] = path
            return

        if ev.kind == "encoder" and ev.name.startswith("enc"):
            i = int(ev.name[3:]) - 1
            if self.page == "layers":
                if i == m.master_slot:
                    self.nudge(s.master, ev.value * m.encoder_step)
                else:
                    layer = self.layer_at(self.layer_off + i)
                    if layer:
                        self.nudge(s.layer_param(layer, m.layer_param), ev.value * m.encoder_step)
            else:
                eff = s.effects[self.fx_sel] if self.fx_sel < len(s.effects) else None
                if eff and i < len(eff.params):
                    self.nudge(eff.params[i][1], ev.value * m.encoder_step)
            return
        if ev.kind == "encoder" and ev.name == "master":
            self.nudge(s.master, ev.value * m.encoder_step)
            return
        if ev.kind == "touchstrip" and m.touchstrip_crossfader and s.crossfader is not None:
            self.set_norm(s.crossfader, ev.value)
            return
        if ev.kind != "button" or not ev.value:
            return

        name = ev.name
        if name == m.bank_prev:
            self.bank = max(0, self.bank - 8)
        elif name == m.bank_next:
            if self.bank + 8 < len(s.columns):
                self.bank += 8
        elif name == m.layers_up:
            if self.layer_off + self.n_layer_rows < len(s.layers):
                self.layer_off += 1
        elif name == m.layers_down:
            self.layer_off = max(0, self.layer_off - 1)
        elif name in m.deck_buttons:
            i = m.deck_buttons.index(name)
            if i < len(s.decks):
                self.arena.trigger(f"/composition/decks/by-id/{s.decks[i].id}/select")
        elif name in m.page_buttons.values():
            self.page = next(k for k, v in m.page_buttons.items() if v == name)  # type: ignore[assignment]
        elif name == m.play_button:
            self._play_toggle()
        elif name == m.tap_button:
            self._tap()
        elif self.page == "fx" and name == m.fx_prev:
            self.fx_off = max(0, self.fx_off - 8)
        elif self.page == "fx" and name == m.fx_next:
            if self.fx_off + 8 < len(s.effects):
                self.fx_off += 8
        elif name.startswith("upper") or name.startswith("lower"):
            i = int(name[5:]) - 1
            if self.page == "layers":
                layer = self.layer_at(self.layer_off + i)
                if layer and name.startswith("upper"):
                    self.arena.trigger(f"/composition/layers/by-id/{layer.id}/select")
                elif layer:
                    self.toggle(layer.bypassed)
            else:
                e = self.fx_off + i
                if e < len(s.effects):
                    if name.startswith("upper"):
                        self.fx_sel = e
                    else:
                        self.toggle(s.effects[e].bypassed)

    def _mcu_event(self, ev: ControlEvent, unit: McuUnit) -> None:
        m, s = self.map.mcu, self.state
        if ev.kind == "fader":
            if ev.name == "master":
                self.set_norm(s.master, ev.value)
            else:
                layer = self.layer_at(self.mcu_off + unit.strip_offset + ev.x)
                if layer:
                    self.set_norm(s.layer_param(layer, m.fader_param), ev.value)
            return
        if ev.kind == "encoder" and ev.name.startswith("enc"):
            layer = self.layer_at(self.mcu_off + unit.strip_offset + ev.x)
            if layer:
                self.nudge(s.layer_param(layer, m.encoder_param), ev.value * m.encoder_step)
            return
        if ev.kind != "button" or not ev.value:
            return
        name = ev.name
        for prefix, what in (("select", "select"), ("solo", m.solo_param), ("mute", m.mute_param)):
            if name.startswith(prefix) and name[len(prefix):].isdigit():
                layer = self.layer_at(self.mcu_off + unit.strip_offset + int(name[len(prefix):]) - 1)
                if layer is None or what is None:
                    return
                if what == "select":
                    if m.select_button:
                        self.arena.trigger(f"/composition/layers/by-id/{layer.id}/select")
                else:
                    self.toggle(s.layer_param(layer, what))
                return
        n = len(s.layers)
        if name == m.bank_prev:
            self.mcu_off = max(0, self.mcu_off - 8)
        elif name == m.bank_next:
            if self.mcu_off + 8 < n:
                self.mcu_off += 8
        elif name == m.channel_prev:
            self.mcu_off = max(0, self.mcu_off - 1)
        elif name == m.channel_next:
            if self.mcu_off + 1 < n:
                self.mcu_off += 1
        elif name == m.play_button:
            self._play_toggle()
        elif name == m.stop_button and s.play_state is not None:
            self.arena.set_param(s.play_state.id, "Stop")
        elif name == m.tap_button:
            self._tap()

    # ─── rendu ───────────────────────────────────────────────────────────
    def offline_text(self) -> tuple[str, str]:
        """(titre, détail) affichés quand Arena n'est pas joignable."""
        a = self.arena
        where = f"{getattr(a, 'host', '?')}:{getattr(a, 'port', '?')}"
        conn = getattr(a, "conn_state", None)
        if conn is not None and conn.name == "CONNECTING":
            title = "CONNEXION À ARENA…"
        else:
            title = "ARENA OFFLINE"
        err = getattr(a, "last_error", None)
        return title, f"{where}" + (f" — {err}" if err else "") + " — reconnexion automatique"

    def render(self) -> None:
        if self.push is not None:
            self._render_push()
        if self.display is not None:
            self.display.model = self._display_model()
        for unit in self.mcus.values():
            if self.online:
                self._render_mcu(unit)
            else:
                self._render_mcu_offline(unit)

    def _render_mcu_offline(self, unit: McuUnit) -> None:
        """LCD : message sur les 8 tranches, en rouge ; LED et couronnes éteintes ; moteurs laissés en place."""
        d = unit.driver
        title, detail = self.offline_text()
        top = ("ARENA OFFLINE" if title == "ARENA OFFLINE" else "CONNEXION ARENA...").center(56)
        bottom = detail.split(" — ")[0].center(56)
        for i in range(8):
            d.set_strip(i, top[7 * i:7 * i + 7], bottom[7 * i:7 * i + 7], 1)
            d.set_ring(i, None)
            d.set_fader(i, None)
            for prefix in ("select", "solo", "mute", "rec"):
                d.set_led(f"{prefix}{i + 1}", False)
        d.set_fader(8, None)
        for name in (self.map.mcu.play_button, self.map.mcu.stop_button, self.map.mcu.tap_button):
            if name:
                d.set_led(name, False)

    def _render_push(self) -> None:
        p, m, c, s = self.push, self.map.push2, self.map.colors, self.state
        p.clear()
        if not self.online:
            return
        dim = c.dim_factor
        anim = m.playing_animation
        for row in range(self.n_layer_rows):
            layer = self.layer_at(self.layer_off + row)
            for col in range(8):
                ci = self.bank + col
                clip = layer.clips[ci] if layer and ci < len(layer.clips) else None
                if clip is None or clip.empty:
                    continue
                rgb = self.clip_color(clip, layer)
                if clip.playing:   # plein, pulse vers l'atténué au tempo
                    p.set_pad(row, col, rgb, anim, scale(rgb, dim))
                else:
                    p.set_pad(row, col, scale(rgb, dim))
        cr = self.column_row
        if cr is not None:
            for col in range(8):
                ci = self.bank + col
                if ci >= len(s.columns):
                    continue
                if s.columns[ci].connected.value == "Connected":
                    p.set_pad(cr, col, c.column_active)
                elif s.columns[ci].connected.value != "Empty":
                    p.set_pad(cr, col, scale(c.column_active, dim))
        for i, name in enumerate(m.deck_buttons):
            if i < len(s.decks):
                on = bool(s.decks[i].selected.value)
                p.set_button_rgb(name, c.deck_active if on else scale(c.deck_active, dim))
        for page, name in m.page_buttons.items():
            p.set_button(name, WHITE_ON if self.page == page else WHITE_DIM)
        p.set_button(m.bank_prev, WHITE_ON if self.bank > 0 else OFF)
        p.set_button(m.bank_next, WHITE_ON if self.bank + 8 < len(s.columns) else OFF)
        p.set_button(m.layers_down, WHITE_ON if self.layer_off > 0 else OFF)
        p.set_button(m.layers_up, WHITE_ON if self.layer_off + self.n_layer_rows < len(s.layers) else OFF)
        if m.play_button:
            # 126 = vert dans la palette RVB et quasi plein dans la palette blanche (type de LED non vérifié)
            p.set_button(m.play_button, 126 if s.play_state is not None and s.play_state.value == "Play" else WHITE_DIM)
        if m.tap_button:
            p.set_button(m.tap_button, WHITE_DIM)
        for i in range(8):
            up, low = f"upper{i + 1}", f"lower{i + 1}"
            if self.page == "layers":
                layer = self.layer_at(self.layer_off + i)
                if layer:
                    rgb = self.layer_color(layer)
                    sel = layer.selected is not None and bool(layer.selected.value)
                    p.set_button_rgb(up, rgb if sel else scale(rgb, dim))
                    byp = layer.bypassed is not None and bool(layer.bypassed.value)
                    p.set_button_rgb(low, (255, 0, 0) if byp else scale((255, 255, 255), dim))
            else:
                e = self.fx_off + i
                if e < len(s.effects):
                    eff = s.effects[e]
                    p.set_button_rgb(up, (255, 140, 0) if e == self.fx_sel else scale((255, 140, 0), dim))
                    byp = eff.bypassed is not None and bool(eff.bypassed.value)
                    p.set_button_rgb(low, (255, 0, 0) if byp else scale((255, 255, 255), dim))
        if self.page == "fx":
            p.set_button(m.fx_prev, WHITE_ON if self.fx_off > 0 else OFF)
            p.set_button(m.fx_next, WHITE_ON if self.fx_off + 8 < len(s.effects) else OFF)

    def _display_model(self) -> DisplayModel:
        s, m = self.state, self.map.push2
        dm = DisplayModel()
        connected = getattr(self.arena, "connected", False)
        if not self.online:
            dm.alert, dm.alert_detail = self.offline_text()
            return dm
        deck = s.selected_deck()
        ncol = len(s.columns)
        bpm = f"{float(s.tempo.value):.1f} BPM" if s.tempo is not None else ""
        play = "" if s.play_state is None or s.play_state.value == "Play" else f" ({s.play_state.value})"
        dm.status = (bpm + play) if connected else "Arena déconnectée"
        cols = f"col {self.bank + 1}-{min(self.bank + 8, ncol)}/{ncol}"
        page_name = "Couches" if self.page == "layers" else "FX composition"
        dm.title = f"{page_name}  ·  {deck.name.value if deck else '?'}  ·  {cols}"
        if self.page == "layers":
            slots = []
            for i in range(8):
                layer = self.layer_at(self.layer_off + i)
                if i == m.master_slot:
                    mp = s.master
                    slots.append(("Master", f"{mp.norm() * 100:.0f} %" if mp else "", mp.norm() if mp else None))
                    if layer:
                        dm.upper[i] = layer.display_name
                    continue
                if layer:
                    prm = s.layer_param(layer, m.layer_param)
                    slots.append((layer.display_name, f"{prm.norm() * 100:.0f} %" if prm else "-", prm.norm() if prm else None))
                    dm.upper[i] = layer.display_name
                    dm.upper_on[i] = layer.selected is not None and bool(layer.selected.value)
                    pc = layer.playing_clip
                    dm.lower[i] = pc.name.value if pc else "—"
                    dm.lower_on[i] = layer.bypassed is not None and bool(layer.bypassed.value)
                else:
                    slots.append(("", "", None))
            dm.slots = slots
        else:
            for i in range(8):
                e = self.fx_off + i
                if e < len(s.effects):
                    eff = s.effects[e]
                    dm.upper[i] = eff.name
                    dm.upper_on[i] = e == self.fx_sel
                    byp = eff.bypassed is not None and bool(eff.bypassed.value)
                    dm.lower[i] = "Bypass" if byp else "On"
                    dm.lower_on[i] = byp
            eff = s.effects[self.fx_sel] if self.fx_sel < len(s.effects) else None
            dm.slots = [(n, f"{prm.norm() * 100:.0f} %", prm.norm()) for n, prm in (eff.params[:8] if eff else [])]
            dm.slots += [("", "", None)] * (8 - len(dm.slots))
        return dm

    def _render_mcu(self, unit: McuUnit) -> None:
        d, m, s = unit.driver, self.map.mcu, self.state
        if not s.loaded:
            return
        for i in range(8):
            layer = self.layer_at(self.mcu_off + unit.strip_offset + i)
            if layer is None:
                d.set_strip(i, "", "", 0)
                d.set_fader(i, 0.0)
                d.set_ring(i, None)
                for prefix in ("select", "solo", "mute"):
                    d.set_led(f"{prefix}{i + 1}", False)
                continue
            pc = layer.playing_clip
            d.set_strip(i, layer.display_name, pc.name.value if pc else "", rgb_to_xtouch(self.layer_color(layer)))
            fp = s.layer_param(layer, m.fader_param)
            d.set_fader(i, fp.norm() if fp else None)
            ep = s.layer_param(layer, m.encoder_param)
            d.set_ring(i, ep.norm() if ep else None)
            d.set_led(f"select{i + 1}", layer.selected is not None and bool(layer.selected.value))
            for prefix, path in (("solo", m.solo_param), ("mute", m.mute_param)):
                prm = s.layer_param(layer, path) if path else None
                d.set_led(f"{prefix}{i + 1}", prm is not None and bool(prm.value))
        if d.has_master and s.master is not None:
            d.set_fader(8, s.master.norm())
        if m.play_button:
            d.set_led(m.play_button, s.play_state is not None and s.play_state.value == "Play")
