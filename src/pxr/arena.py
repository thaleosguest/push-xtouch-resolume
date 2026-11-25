"""Client WebSocket Arena : connexion locale ou réseau, reconnexion automatique, souscriptions, actions.

Formats vérifiés au spike 2 (JS d'exemple servi par Arena + essais du 25/11/2026) :
  souscrire  {"action":"subscribe","parameter":"/parameter/by-id/<id>"}
  régler     {"action":"set","parameter":"/parameter/by-id/<id>","value":v}   (ParamChoice : la valeur texte)
  déclencher {"action":"trigger","parameter":"<chemin>","value":true|false}   (clip/colonne connect : appui/relâché)
             {"action":"trigger","parameter":"<chemin>"}                      (deck/couche select)
  En retour, seul "parameter_update" fait foi : "parameter_set" renvoie l'ANCIENNE valeur.
Commandes ET retour d'état passent par ce WebSocket (choix du 25/11/2026 : un seul port TCP, pas d'OSC).
À chaque (re)connexion, Arena renvoie la composition complète : on recharge l'état et on se réabonne à tout.
"""
from __future__ import annotations

import asyncio
import enum
import json
import logging
from typing import Any, Callable

import websockets

from .config import ArenaCfg
from .state import StateModel

log = logging.getLogger(__name__)

VALUE_TYPES = ("parameter_update", "parameter_subscribed", "parameter_get")


class ConnState(enum.Enum):
    DISCONNECTED = "déconnectée"
    CONNECTING = "connexion…"
    CONNECTED = "connectée"


class ArenaClient:
    def __init__(self, state: StateModel, host: str = "127.0.0.1", port: int = 8080, *,
                 initial_delay: float = 0.5, max_delay: float = 10.0, open_timeout: float = 3.0,
                 ping_interval: float = 2.0, ping_timeout: float = 3.0) -> None:
        self.state = state
        self.host, self.port = host, port
        self.url = f"ws://{host}:{port}/api/v1"
        self.initial_delay, self.max_delay = initial_delay, max_delay
        self.open_timeout, self.ping_interval, self.ping_timeout = open_timeout, ping_interval, ping_timeout
        self.conn_state = ConnState.DISCONNECTED
        self.last_error: str | None = None
        self._state_listeners: list[Callable[[ConnState], None]] = []
        self._out: asyncio.Queue[str] | None = None
        self._subscribed: set[int] = set()
        self._stop = asyncio.Event()
        state.subscribe(self._on_state)

    @classmethod
    def from_cfg(cls, state: StateModel, cfg: ArenaCfg) -> "ArenaClient":
        return cls(state, cfg.host, cfg.port, initial_delay=cfg.reconnect.initial_delay_ms / 1000,
                   max_delay=cfg.reconnect.max_delay_ms / 1000, open_timeout=cfg.connect_timeout_s,
                   ping_interval=cfg.ping_interval_s, ping_timeout=cfg.ping_timeout_s)

    # ─── état de la connexion ───────────────────────────────────────────
    @property
    def connected(self) -> bool:
        return self.conn_state is ConnState.CONNECTED

    def on_state_change(self, fn: Callable[[ConnState], None]) -> None:
        self._state_listeners.append(fn)

    def _set_state(self, s: ConnState) -> None:
        if s is self.conn_state:
            return
        self.conn_state = s
        for fn in self._state_listeners:
            fn(s)
        self.state._emit("connection")

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
        delay = self.initial_delay
        attempt = 0
        while not self._stop.is_set():
            self._set_state(ConnState.CONNECTING)
            attempt += 1
            try:
                async with websockets.connect(self.url, max_size=None, open_timeout=self.open_timeout,
                                              ping_interval=self.ping_interval, ping_timeout=self.ping_timeout,
                                              close_timeout=0.5) as ws:
                    delay, attempt = self.initial_delay, 0
                    await self._session(ws)
                    if not self._stop.is_set():
                        self.last_error = "connexion fermée par Arena"
                        log.warning("arena %s : connexion fermée par Arena", self.url)
            except (OSError, websockets.WebSocketException, asyncio.TimeoutError) as e:
                was_connected = self.connected
                self.last_error = describe_error(e)
                if was_connected:
                    log.warning("arena %s perdue : %s", self.url, self.last_error)
                elif attempt == 1 or attempt % 10 == 0:   # pas une ligne par essai pendant une longue absence
                    log.warning("arena %s injoignable (essai %d) : %s", self.url, attempt, self.last_error)
            finally:
                self._out = None
                self._set_state(ConnState.DISCONNECTED)
            if self._stop.is_set():
                break
            log.debug("nouvel essai dans %.1f s", delay)
            try:
                await asyncio.wait_for(self._stop.wait(), delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, self.max_delay)

    async def _session(self, ws) -> None:
        self._out = asyncio.Queue()
        self._subscribed = set()
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
            if not msg["layers"] and not msg.get("columns"):
                # Vu le 25/11/2026 : Arena envoie une composition vide juste avant de se fermer. Une vraie
                # composition a toujours au moins une couche et une colonne : on l'ignore.
                log.info("composition vide reçue (Arena se ferme ?) : ignorée")
                return
            self.state.load(msg)
            if not self.connected:
                self._set_state(ConnState.CONNECTED)   # connectée = état complet reçu, pas seulement le TCP
                log.info("arena connectée url=%s composition=%r", self.url, self.state.name)
            self._subscribe_all()
        elif kind in VALUE_TYPES and isinstance(msg.get("id"), int) and "value" in msg:
            self.state.apply(msg["id"], msg["value"])
        elif kind in ("sources_update", "effects_update", "thumbnail_update", "parameter_set", "parameter_unsubscribed"):
            pass
        else:
            log.debug("message arena non géré %s", str(msg)[:200])

    def _subscribe_all(self) -> None:
        new = [pid for pid in self.state.params if pid not in self._subscribed]
        assert self._out is not None
        for pid in new:   # envoi direct : on est connecté dès la composition reçue
            self._out.put_nowait(json.dumps({"action": "subscribe", "parameter": f"/parameter/by-id/{pid}"}))
        self._subscribed.update(new)
        log.log(logging.INFO if new else logging.DEBUG, "souscriptions envoyées n=%d total=%d",
                len(new), len(self._subscribed))


def describe_error(e: BaseException) -> str:
    """Message court et parlant pour le journal et les écrans."""
    if isinstance(e, ConnectionRefusedError):
        return "connexion refusée (webserver d'Arena arrêté ou mauvais port)"
    if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
        return "pas de réponse (hôte éteint, câble, ou pare-feu)"
    if isinstance(e, websockets.ConnectionClosed):
        return "connexion coupée"
    if isinstance(e, OSError) and getattr(e, "winerror", None) in (10051, 10065):
        return "réseau ou hôte inaccessible"
    if isinstance(e, OSError) and e.errno in (11001, 11004, -2, -3):
        return "nom d'hôte inconnu"
    return f"{type(e).__name__}: {e}"
