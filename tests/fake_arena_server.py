"""Faux serveur Arena (WebSocket) pour tester le mode réseau sans Resolume.

Comme Arena (spike 2) : à la connexion, composition complète puis sources_update / effects_update ; ensuite
il enregistre les commandes reçues et peut pousser des parameter_update. `kill()` coupe brutalement (TCP
interrompu, comme un câble débranché ou Arena qui plante) ; `start()` relance sur le même port.
"""
from __future__ import annotations

import asyncio
import json
import socket

import websockets


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeArenaServer:
    def __init__(self, composition: dict, port: int | None = None) -> None:
        self.composition = composition
        self.port = port or free_port()
        self.received: list[dict] = []
        self.sessions = 0
        self._conns: set = set()
        self._server = None

    async def start(self) -> None:
        self._server = await websockets.serve(self._handler, "127.0.0.1", self.port)

    async def _handler(self, ws) -> None:
        self.sessions += 1
        self._conns.add(ws)
        try:
            await ws.send(json.dumps(self.composition))
            await ws.send(json.dumps({"type": "sources_update", "value": {}}))
            await ws.send(json.dumps({"type": "effects_update", "value": {}}))
            async for raw in ws:
                self.received.append(json.loads(raw))
        except websockets.ConnectionClosed:
            pass
        finally:
            self._conns.discard(ws)

    async def update(self, pid: int, value) -> None:
        for ws in list(self._conns):
            await ws.send(json.dumps({"type": "parameter_update", "id": pid, "value": value}))

    def commands(self, action: str) -> list[dict]:
        return [m for m in self.received if m.get("action") == action]

    async def kill(self) -> None:
        """Coupure brutale : connexions TCP interrompues sans fermeture WebSocket, serveur arrêté."""
        for ws in list(self._conns):
            ws.transport.abort()
        self._server.close()
        await self._server.wait_closed()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


async def wait_for(pred, timeout: float = 5.0, step: float = 0.02) -> bool:
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if pred():
            return True
        await asyncio.sleep(step)
    return pred()
