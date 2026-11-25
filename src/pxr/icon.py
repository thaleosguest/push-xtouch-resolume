"""Icône de l'application (dessinée avec Pillow) : pads de couleur + fader, pastille d'état en bas à droite."""
from __future__ import annotations

from PIL import Image, ImageDraw

STATE_COLORS = {"ok": (40, 210, 90), "partial": (255, 160, 0), "down": (230, 50, 50), None: None}
PADS = [(255, 70, 0), (0, 190, 255), (255, 220, 0), (190, 40, 255), (60, 255, 190), (255, 40, 60)]


def make_icon(state: str | None = None, size: int = 64) -> Image.Image:
    s = size / 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([1 * s, 1 * s, 63 * s, 63 * s], radius=12 * s, fill=(28, 30, 34, 255))
    for k, c in enumerate(PADS):               # 2 rangées de 3 pads
        x, y = 8 + (k % 3) * 13, 10 + (k // 3) * 13
        d.rounded_rectangle([x * s, y * s, (x + 10) * s, (y + 10) * s], radius=2 * s, fill=c + (255,))
    d.rounded_rectangle([47 * s, 9 * s, 51 * s, 55 * s], radius=2 * s, fill=(90, 94, 100, 255))   # rail
    d.rounded_rectangle([43 * s, 30 * s, 55 * s, 38 * s], radius=2 * s, fill=(235, 235, 235, 255))  # fader
    d.rounded_rectangle([8 * s, 38 * s, 36 * s, 55 * s], radius=3 * s, fill=(60, 64, 70, 255))     # écran
    d.line([12 * s, 47 * s, 32 * s, 47 * s], fill=(255, 140, 0, 255), width=max(1, round(3 * s)))
    col = STATE_COLORS.get(state)
    if col:
        r = 11 * s
        d.ellipse([size - 2 * r - 1, size - 2 * r - 1, size - 1, size - 1], fill=col + (255,),
                  outline=(20, 20, 20, 255), width=max(1, round(2 * s)))
    return img


def save_ico(path: str) -> None:
    make_icon(None, 256).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
