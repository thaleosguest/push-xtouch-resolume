"""Spike 5 — Correspondance colorid Arena -> couleur.

--apply   : met les clips 1 à 6 de la couche choisie sur colorid "1".."6" (valeurs d'origine sauvées)
--restore : remet les valeurs d'origine
Ensuite, regarder dans Arena la couleur de chaque clip.
"""
from __future__ import annotations

import argparse
import asyncio
import json

import websockets

from common import OUT_DIR

SAVE = OUT_DIR / "s5_original_colorids.json"


async def main(apply: bool, layer: int, host: str, port: int) -> None:
    async with websockets.connect(f"ws://{host}:{port}/api/v1", max_size=None) as ws:
        comp = json.loads(await ws.recv())
        clips = comp["layers"][layer - 1]["clips"][:6]
        options = clips[0]["colorid"]["options"]
        print("options colorid :", options)
        if apply:
            SAVE.write_text(json.dumps({str(c["colorid"]["id"]): c["colorid"]["value"] for c in clips}), encoding="utf-8")
            for i, c in enumerate(clips):
                v = options[i % len(options)]
                await ws.send(json.dumps({"action": "set", "parameter": f"/parameter/by-id/{c['colorid']['id']}", "value": v}))
                print(f"  couche {layer} clip {i + 1} ({c['name']['value'] or 'vide'}) -> colorid {v}")
        else:
            for pid, v in json.loads(SAVE.read_text(encoding="utf-8")).items():
                await ws.send(json.dumps({"action": "set", "parameter": f"/parameter/by-id/{pid}", "value": v}))
            print("couleurs d'origine remises")
        await asyncio.sleep(0.5)
        # relecture pour vérifier
        await ws.send(json.dumps({"action": "get", "parameter": f"/parameter/by-id/{clips[1]['colorid']['id']}"}))
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), 3))
            if m.get("type") == "parameter_get":
                print("relu clip 2 :", m.get("value"))
                break


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--apply", action="store_true")
    g.add_argument("--restore", action="store_true")
    ap.add_argument("--layer", type=int, default=1)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    a = ap.parse_args()
    asyncio.run(main(a.apply, a.layer, a.host, a.port))
