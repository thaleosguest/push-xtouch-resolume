"""Écoute simultanée de tous les ports d'entrée MIDI dont le nom correspond au motif (outil de découverte).

Usage : python spikes/midi_listen.py "X-Touch" [--seconds 30]
"""
from __future__ import annotations

import argparse
import re
import time

import mido

from common import OUT_DIR


def main(pattern: str, seconds: float) -> None:
    rx = re.compile(pattern, re.I)
    ports = []
    for name in mido.get_input_names():
        if rx.search(name):
            try:
                ports.append(mido.open_input(name))
                print("ouvert :", name)
            except Exception as e:  # port déjà pris par une autre appli
                print("OCCUPÉ :", name, "|", e)
    counts = {p.name: 0 for p in ports}
    end = time.time() + seconds
    t0 = time.perf_counter()
    log = open(OUT_DIR / "midi_listen.log", "w", encoding="utf-8")
    print(f"Écoute {seconds:.0f} s… (journal complet : spikes/out/midi_listen.log)")
    while time.time() < end:
        for p in ports:
            for m in p.iter_pending():
                if m.type in ("clock", "active_sensing"):
                    continue
                counts[p.name] += 1
                line = f"{time.perf_counter() - t0:8.3f} [{p.name}] {m}"
                log.write(line + "\n")
                if m.type != "pitchwheel":  # les faders inondent la console : seulement dans le journal
                    print(" ", line)
        time.sleep(0.001)
    log.close()
    print("Messages reçus par port :", counts)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern")
    ap.add_argument("--seconds", type=float, default=30)
    a = ap.parse_args()
    main(a.pattern, a.seconds)
