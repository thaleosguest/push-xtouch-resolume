"""Drivers de contrôleurs. Aucun ne connaît Arena : ils émettent des `ControlEvent` et exposent des méthodes de
rendu (état désiré). `flush()` n'envoie que ce qui a changé depuis le dernier envoi (fusion par trame)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ControlEvent:
    source: str        # "push2" ou nom de l'instance MCU ("xtouch", "extender"…)
    kind: str          # pad | button | encoder | touch | fader | touchstrip
    name: str = ""     # nom du bouton / de l'encodeur / du fader
    x: int = 0         # colonne du pad, numéro de tranche…
    y: int = 0         # rangée du pad (0 = bas)
    value: float = 0   # vélocité, appui (1/0), delta d'encodeur, position normalisée 0..1
