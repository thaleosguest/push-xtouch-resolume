"""Arena simulée (mode --dry-run et tests) : même interface qu'ArenaClient, état tiré d'une composition JSON.

Simulation volontairement simple : un clip déclenché passe « Connected » et coupe les autres clips de sa
couche ; une colonne déclenche le clip de chaque couche ; un deck sélectionné désélectionne les autres.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from importlib import resources
from typing import Any

from .state import StateModel

log = logging.getLogger(__name__)


def demo_composition() -> dict:
    return json.loads(resources.files("pxr.data").joinpath("demo_composition.json").read_text(encoding="utf-8"))


class FakeArena:
    def __init__(self, state: StateModel, composition: dict | None = None) -> None:
        self.state = state
        self.connected = True
        self.composition = composition or demo_composition()
        self.sent: list[tuple] = []      # journal des actions (tests)
        self._stop = asyncio.Event()

    def load(self) -> None:
        self.state.load(self.composition)

    async def run(self) -> None:
        self.load()
        self.state._emit("connection")
        await self._stop.wait()

    def stop(self) -> None:
        self._stop.set()

    # ─── actions ────────────────────────────────────────────────────────
    def set_param(self, pid: int, value: Any) -> None:
        self.sent.append(("set", pid, value))
        p = self.state.params.get(pid)
        if p is not None and p.is_range:
            value = min(p.max, max(p.min, float(value)))
        if p is not None and p.valuetype == "ParamEvent":
            return
        self.state.apply(pid, value)

    def fire_event(self, pid: int) -> None:
        self.set_param(pid, True)
        self.set_param(pid, False)

    def trigger(self, path: str, value: bool | None = None) -> None:
        self.sent.append(("trigger", path, value))
        if value is False:
            return
        s = self.state
        if m := re.fullmatch(r"/composition/clips/by-id/(\d+)/connect", path):
            self._connect_clip(int(m[1]))
        elif m := re.fullmatch(r"/composition/columns/by-id/(\d+)/connect", path):
            col = next((c for c in s.columns if c.id == int(m[1])), None)
            if col:
                for layer in s.layers:
                    if col.index <= len(layer.clips) and not layer.clips[col.index - 1].empty:
                        self._connect_clip(layer.clips[col.index - 1].id)
                for c in s.columns:
                    s.apply(c.connected.id, "Connected" if c is col else "Disconnected")
        elif m := re.fullmatch(r"/composition/decks/by-id/(\d+)/select", path):
            for d in s.decks:
                s.apply(d.selected.id, d.id == int(m[1]))
        elif m := re.fullmatch(r"/composition/layers/by-id/(\d+)/select", path):
            for layer in s.layers:
                if layer.selected:
                    s.apply(layer.selected.id, layer.id == int(m[1]))
        else:
            log.debug("trigger simulé ignoré %s", path)

    def _connect_clip(self, clip_id: int) -> None:
        s = self.state
        for layer in s.layers:
            if any(c.id == clip_id for c in layer.clips):
                for c in layer.clips:
                    if c.id == clip_id:
                        s.apply(c.connected.id, "Connected")
                    elif c.playing:
                        s.apply(c.connected.id, "Disconnected")
