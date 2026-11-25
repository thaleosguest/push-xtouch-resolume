"""ArenaClient contre un faux serveur WebSocket local qui imite Arena (messages vus au spike 2)."""
import asyncio
import json

import websockets

from pxr.arena import ArenaClient
from pxr.fake_arena import demo_composition
from pxr.state import StateModel


def test_client_subscribes_updates_and_reconnects():
    async def scenario():
        comp = demo_composition()
        received: list[dict] = []
        sessions = 0
        clip_conn = comp["layers"][0]["clips"][0]["connected"]["id"]

        async def handler(ws):
            nonlocal sessions
            sessions += 1
            await ws.send(json.dumps(comp))
            await ws.send(json.dumps({"type": "sources_update", "value": {}}))
            await ws.send(json.dumps({"type": "effects_update", "value": {}}))
            async def reader():  # comme Arena : on lit tout ce que le client envoie
                async for raw in ws:
                    received.append(json.loads(raw))

            rd = asyncio.create_task(reader())
            if sessions == 1:
                # premier client : on attend les souscriptions, on envoie une mise à jour, puis on coupe
                while len(received) < 50:
                    await asyncio.sleep(0.01)
                await ws.send(json.dumps({"type": "parameter_set", "id": clip_conn, "value": "Disconnected"}))
                await ws.send(json.dumps({"type": "parameter_update", "id": clip_conn, "value": "Connected",
                                          "path": "/composition/layers/1/clips/1/connected"}))
                await asyncio.sleep(0.2)
                await ws.close()
            await rd

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            state = StateModel()
            client = ArenaClient(state, "127.0.0.1", port)
            task = asyncio.create_task(client.run())
            for _ in range(100):
                await asyncio.sleep(0.05)
                if state.layers and state.layers[0].clips[0].playing:
                    break
            assert state.layers[0].clips[0].playing                 # parameter_update appliqué, parameter_set ignoré
            assert all(m["action"] == "subscribe" for m in received[:50])
            assert {"action": "subscribe", "parameter": f"/parameter/by-id/{clip_conn}"} in received
            for _ in range(100):                                    # reconnexion automatique après la coupure
                await asyncio.sleep(0.05)
                if sessions == 2 and client.connected:
                    break
            assert sessions == 2 and client.connected
            n = len(received)
            client.trigger("/composition/clips/by-id/1/connect", True)
            client.set_param(42, 0.5)
            await asyncio.sleep(0.2)
            assert received[-2:] == [
                {"action": "trigger", "parameter": "/composition/clips/by-id/1/connect", "value": True},
                {"action": "set", "parameter": "/parameter/by-id/42", "value": 0.5}]
            assert len(received) > n
            client.stop()
            task.cancel()

    asyncio.run(scenario())
