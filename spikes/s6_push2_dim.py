"""Spike 6 — Atténuation des pads du Push 2 avec une palette écrite par nous (SysEx 0x03 + 0x05).

Manuel rev 1.1 : 0x03 Set LED Color Palette Entry : i, r LSB, r MSB, g LSB, g MSB, b LSB, b MSB, w LSB, w MSB
(chaque composante 8 bits = 7 bits de poids faible + 1 bit de poids fort) ; 0x05 Reapply Color Palette.

--show    : écrit les entrées 1..24 (3 couleurs x 8 niveaux) et allume les rangées 1 à 3 (laisse allumé)
--restore : remet les entrées 1..24 de la palette par défaut lue au spike 4, éteint les pads
"""
from __future__ import annotations

import argparse
import json
import time

import mido

from common import OUT_DIR, open_io

HDR = [0x00, 0x21, 0x1D, 0x01, 0x01]
LEVELS = [255, 128, 64, 40, 24, 14, 8, 4]
COLORS = {"blanc": (255, 255, 255), "bleu": (40, 200, 255), "orange": (255, 90, 0)}


def split(v: int) -> list[int]:
    return [v & 0x7F, (v >> 7) & 0x01]


def set_entry(out, i: int, r: int, g: int, b: int, w: int) -> None:
    out.send(mido.Message("sysex", data=HDR + [0x03, i] + split(r) + split(g) + split(b) + split(w)))


def main(show: bool, port: str) -> None:
    inp, out = open_io(port, port)
    if show:
        for row, (name, (r, g, b)) in enumerate(COLORS.items()):
            for col, lvl in enumerate(LEVELS):
                k = lvl / 255
                set_entry(out, 1 + row * 8 + col, round(r * k), round(g * k), round(b * k), lvl)
        out.send(mido.Message("sysex", data=HDR + [0x05]))
        time.sleep(0.05)
        for row in range(3):
            for col in range(8):
                out.send(mido.Message("note_on", note=36 + 8 * row + col, velocity=1 + row * 8 + col))
        for row in range(3, 8):
            for col in range(8):
                out.send(mido.Message("note_on", note=36 + 8 * row + col, velocity=0))
        print("Rangées (bas -> haut) : blanc, bleu, orange. Colonnes (gauche -> droite) : niveaux", LEVELS)
    else:
        pal = json.loads((OUT_DIR / "push2_palette.json").read_text(encoding="utf-8"))
        for i in range(1, 25):
            e = pal[str(i)]
            set_entry(out, i, e["r"], e["g"], e["b"], e["w"])
        out.send(mido.Message("sysex", data=HDR + [0x05]))
        for n in range(36, 100):
            out.send(mido.Message("note_on", note=n, velocity=0))
        print("Palette par défaut remise (entrées 1-24)")
    inp.close(); out.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--show", action="store_true")
    g.add_argument("--restore", action="store_true")
    ap.add_argument("--port", default=r"^Ableton Push 2 \d+$")
    a = ap.parse_args()
    main(a.show, a.port)
