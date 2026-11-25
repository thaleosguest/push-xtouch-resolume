"""Client WebSocket Arena : connexion, reconnexion automatique, souscriptions, actions.

Formats vérifiés au spike 2 (JS d'exemple servi par Arena + essais du 25/11/2026) :
  souscrire  {"action":"subscribe","parameter":"/parameter/by-id/<id>"}
  régler     {"action":"set","parameter":"/parameter/by-id/<id>","value":v}   (ParamChoice : la valeur texte)
  déclencher {"action":"trigger","parameter":"<chemin>","value":true|false}   (clip/colonne connect : appui/relâché)
             {"action":"trigger","parameter":"<chemin>"}                      (deck/couche select)
  En retour, seul "parameter_update" fait foi : "parameter_set" renvoie l'ANCIENNE valeur.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets

from .state import StateModel

log = logging.getLogger(__name__)

VALUE_TYPES = ("parameter_update", "parameter_subscribed", "parameter_get")


class ArenaClient:
    def __init__(self, state: StateModel, host: str = "127.0.0.1", port: int = 8080) -> None:
        self.state = state
        self.url = f"ws://{host}:{port}/api/v1"
        self.connected = False
        self._out: asyncio.Queue[str] | None = None
        self._subscribed: set[int] = set()
        self._stop = asyncio.Event()
        state.subscribe(self._on_state)

    def _on_state(self, kind: str, pid: int | None) -> None:
        if kind == "track" and self.connected and pid is not None and pid not in self._subscribed:
            self._send({"action": "subscribe", "parameter": f"/parameter/by-id/{pid}"})
            self._subscribed.add(pid)

    # ─── actions (appelables depuis la boucle asyncio, sans attendre) ───
    def _send(self, msg: dict) -> None:
        if not self.connected or self._out is None:
            log.debug("arena hors ligne, action ignorée %s", msg)
            return
        self._out.put_nowait(json.dumps(msg))

    def set_param(self, pid: int, value: Any) -> None:
        self._send({"action": "set", "parameter": f"/parameter/by-id/{pid}", "value": value})

    def trigger(self, path: str, value: bool | None = None) -> None:
        msg: dict = {"action": "trigger", "parameter": path}
        if value is not None:
            msg["value"] = value
        self._send(msg)

    def fire_event(self, pid: int) -> None:
        """ParamEvent (tap tempo, resync…) : comme le bouton de l'exemple Arena, vrai puis faux."""
        self.set_param(pid, True)
        self.set_param(pid, False)

    # ─── boucle de connexion ────────────────────────────────────────────
    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.url, max_size=None, ping_interval=5, ping_timeout=5) as ws:
                    delay = 1.0
                    await self._session(ws)
            except (OSError, websockets.WebSocketException, asyncio.TimeoutError) as e:
                if self.connected:
                    log.warning("arena déconnectée : %s", e)
                else:
                    log.debug("arena injoignable : %s", e)
            finally:
                if self.connected:
                    self.connected = False
                    self.state._emit("connection")
            if self._stop.is_set():
                break
            try:
                await asyncio.wait_for(self._stop.wait(), delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, 5.0)

    async def _session(self, ws) -> None:
        self._out = asyncio.Queue()
        self._subscribed = set()
        self.connected = True
        log.info("arena connectée url=%s", self.url)
        self.state._emit("connection")
        writer = asyncio.create_task(self._writer(ws))
        try:
            async for raw in ws:
                self._handle(json.loads(raw))
                if self._stop.is_set():
                    break
        finally:
            writer.cancel()

    async def _writer(self, ws) -> None:
        assert self._out is not None
        while True:
            await ws.send(await self._out.get())

    def _handle(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind is None and "layers" in msg:          # composition complète (connexion ou changement de structure)
            self.state.load(msg)
            self._subscribe_all()
        elif kind in VALUE_TYPES and isinstance(msg.get("id"), int) and "value" in msg:
            self.state.apply(msg["id"], msg["value"])
        elif kind in ("sources_update", "effects_update", "thumbnail_update", "parameter_set", "parameter_unsubscribed"):
            pass
        else:
            log.debug("message arena non géré %s", str(msg)[:200])

    def _subscribe_all(self) -> None:
        new = [pid for pid in self.state.params if pid not in self._subscribed]
        for pid in new:
            self._send({"action": "subscribe", "parameter": f"/parameter/by-id/{pid}"})
        self._subscribed.update(new)
        log.info("souscriptions envoyées n=%d total=%d", len(new), len(self._subscribed))
