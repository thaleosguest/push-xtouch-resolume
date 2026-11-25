"""Spike 2 — Arena WebSocket : connexion, état initial, souscriptions, journal des événements.

Usage :
    python spikes/s2_arena_ws.py [--host 127.0.0.1] [--port 8080] [--seconds 60]

Pendant l'exécution, déclenche des clips / colonnes / decks à la main dans Arena,
bouge une opacité de couche, le master, le BPM : tout est journalisé dans
spikes/out/s2_events.jsonl. Formats vérifiés dans l'exemple JS servi par Arena
(/api/docs/example) : {"action": "subscribe", "parameter": "/parameter/by-id/<id>"},
réponses {"type": "parameter_update", "id": ..., "value": ..., "path": ...}.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

import websockets

from common import OUT_DIR


def collect_params(comp: dict) -> dict[int, str]:
    """id de paramètre -> libellé lisible, pour les paramètres à surveiller."""
    p: dict[int, str] = {}

    def add(param, label):
        if isinstance(param, dict) and "id" in param:
            p[param["id"]] = label

    add(comp.get("master"), "composition/master")
    tc = comp.get("tempocontroller", {})
    add(tc.get("tempo"), "tempo")
    add(tc.get("play_state"), "tempo/play_state")
    add(comp.get("crossfader", {}).get("phase"), "crossfader/phase")
    for d, deck in enumerate(comp.get("decks", []), 1):
        add(deck.get("selected"), f"deck{d}/selected")
    for c, col in enumerate(comp.get("columns", []), 1):
        add(col.get("connected"), f"col{c}/connected")
    for li, layer in enumerate(comp.get("layers", []), 1):
        add(layer.get("master"), f"L{li}/master")
        add(layer.get("video", {}).get("opacity"), f"L{li}/video/opacity")
        add(layer.get("bypassed"), f"L{li}/bypassed")
        add(layer.get("solo"), f"L{li}/solo")
        add(layer.get("selected"), f"L{li}/selected")
        for ci, clip in enumerate(layer.get("clips", []), 1):
            add(clip.get("connected"), f"L{li}C{ci}/connected")
            add(clip.get("name"), f"L{li}C{ci}/name")
            add(clip.get("colorid"), f"L{li}C{ci}/colorid")
    for e, eff in enumerate(comp.get("video", {}).get("effects", []), 1):
        add(eff.get("bypassed"), f"compFX{e}:{eff.get('name')}/bypassed")
    return p


def summarize(comp: dict) -> str:
    out = [f"Composition « {comp['name']['value']} » : {len(comp['layers'])} couches, "
           f"{len(comp['columns'])} colonnes, {len(comp['decks'])} decks"]
    for d in comp["decks"]:
        out.append(f"  deck {d['name']['value']!r} selected={d['selected']['value']} colorid={d['colorid']['value']}")
    for li, layer in enumerate(comp["layers"], 1):
        out.append(f"  L{li} {layer['name']['value']!r} colorid={layer['colorid']['value']} "
                   f"opacity={layer['video']['opacity']['value']} master={layer['master']['value']}")
        for ci, clip in enumerate(layer["clips"], 1):
            out.append(f"     C{ci} {clip['name']['value']!r:28} {clip['connected']['value']:22} colorid={clip['colorid']['value']}")
    out.append("  FX composition : " + ", ".join(e["name"] for e in comp["video"].get("effects", [])))
    return "\n".join(out)


async def main(host: str, port: int, seconds: float) -> None:
    url = f"ws://{host}:{port}/api/v1"
    log = open(OUT_DIR / "s2_events.jsonl", "w", encoding="utf-8")
    t0 = time.perf_counter()
    async with websockets.connect(url, max_size=None) as ws:
        print(f"Connecté à {url}")
        comp = None
        labels: dict[int, str] = {}
        initial_types = []
        # Les 3 messages initiaux : composition, sources_update, effects_update.
        for _ in range(3):
            msg = json.loads(await asyncio.wait_for(ws.recv(), 5))
            kind = msg.get("type") or ("composition" if "layers" in msg else "?")
            initial_types.append(kind)
            (OUT_DIR / f"s2_initial_{kind}.json").write_text(json.dumps(msg, indent=1), encoding="utf-8")
            if kind == "composition":
                comp = msg
        print("Messages initiaux :", initial_types)
        if comp is None:
            raise SystemExit("Pas de composition reçue")
        print(summarize(comp))
        labels = collect_params(comp)
        for pid in labels:
            await ws.send(json.dumps({"action": "subscribe", "parameter": f"/parameter/by-id/{pid}"}))
        print(f"\n{len(labels)} souscriptions envoyées. Joue avec Arena pendant {seconds:.0f} s…\n")

        deadline = time.perf_counter() + seconds
        counts: dict[str, int] = {}
        while (remaining := deadline - time.perf_counter()) > 0:
            try:
                raw = await asyncio.wait_for(ws.recv(), remaining)
            except asyncio.TimeoutError:
                break
            msg = json.loads(raw)
            t = time.perf_counter() - t0
            kind = msg.get("type") or ("composition" if "layers" in msg else "?")
            counts[kind] = counts.get(kind, 0) + 1
            log.write(json.dumps({"t": round(t, 3), **msg}) + "\n")
            if kind == "parameter_subscribed":
                continue
            if kind == "composition":
                print(f"[{t:7.2f}] composition re-envoyée (changement de structure)")
                labels.update(collect_params(msg))
                continue
            label = labels.get(msg.get("id"), msg.get("path", "?"))
            print(f"[{t:7.2f}] {kind:18} {label:32} = {json.dumps(msg.get('value'))[:80]}")
        print("\nTotaux par type de message :", counts)
    log.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--seconds", type=float, default=60)
    a = ap.parse_args()
    asyncio.run(main(a.host, a.port, a.seconds))
