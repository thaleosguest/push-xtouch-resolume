"""Conversions de couleurs : RVB -> index de palette Push 2, RVB -> couleur de scribble strip X-Touch."""
from __future__ import annotations

import colorsys
import json
from functools import lru_cache
from importlib import resources

RGB = tuple[int, int, int]

# Couleurs X-Touch (validées sur la X-Touch de test le 14/10/2026, repris de xtouch-lv1/surface/notes.py)
XT = {"vide": 0, "rouge": 1, "vert": 2, "jaune": 3, "bleu": 4, "violet": 5, "cyan": 6, "blanc": 7}


@lru_cache(maxsize=1)
def push2_palette_entries() -> dict[int, tuple[int, int, int, int]]:
    """Palette d'usine lue sur le Push 2 de test au spike 4 (SysEx 0x04, firmware 1.0.71) : index -> r, g, b, w."""
    raw = json.loads(resources.files("pxr.data").joinpath("push2_palette.json").read_text(encoding="utf-8"))
    return {int(i): (v["r"], v["g"], v["b"], v["w"]) for i, v in raw.items()}


def push2_palette() -> dict[int, RGB]:
    return {i: e[:3] for i, e in push2_palette_entries().items()}


@lru_cache(maxsize=4096)
def push2_index(rgb: RGB) -> int:
    """Index de palette le plus proche (distance pondérée sur la perception). Noir -> 0."""
    if max(rgb) == 0:
        return 0
    r, g, b = rgb
    best, best_d = 0, float("inf")
    for i, (pr, pg, pb) in push2_palette().items():
        if i == 0:
            continue
        d = 2 * (pr - r) ** 2 + 4 * (pg - g) ** 2 + 3 * (pb - b) ** 2
        if d < best_d:
            best, best_d = i, d
    return best


def scale(rgb: RGB, k: float) -> RGB:
    return tuple(max(0, min(255, int(c * k))) for c in rgb)  # type: ignore[return-value]


def rgb_to_xtouch(rgb: RGB | None) -> int:
    """RVB -> couleur X-Touch la plus proche par la teinte (logique reprise de xtouch-lv1/surface/display.py)."""
    if not rgb:
        return XT["blanc"]
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    if v < 0.05:
        return XT["vide"]
    if s < 0.25 or v < 0.15:
        return XT["blanc"]
    deg = h * 360.0
    if deg < 20 or deg >= 330:
        return XT["rouge"]
    if deg < 75:
        return XT["jaune"]
    if deg < 160:
        return XT["vert"]
    if deg < 200:
        return XT["cyan"]
    if deg < 260:
        return XT["bleu"]
    return XT["violet"]
