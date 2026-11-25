"""Outils partagés par les spikes (recherche de ports MIDI par motif regex)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import mido

OUT_DIR = Path(__file__).parent / "out"
OUT_DIR.mkdir(exist_ok=True)


def find_port(names: list[str], pattern: str) -> str:
    rx = re.compile(pattern)
    hits = [n for n in names if rx.search(n)]
    if not hits:
        sys.exit(f"Aucun port ne correspond à {pattern!r}. Ports disponibles : {names}")
    return hits[0]


def open_io(in_pattern: str, out_pattern: str):
    """Ouvre (entrée, sortie) MIDI dont le nom correspond aux motifs."""
    i = find_port(mido.get_input_names(), in_pattern)
    o = find_port(mido.get_output_names(), out_pattern)
    print(f"MIDI in  = {i!r}\nMIDI out = {o!r}")
    return mido.open_input(i), mido.open_output(o)


def hexs(data) -> str:
    return " ".join(f"{b:02X}" for b in data)
