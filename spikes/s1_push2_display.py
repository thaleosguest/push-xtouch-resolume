"""Spike 1 — Écran Push 2 sous Windows via pyusb + libusb (pilote WinUSB déjà en place).

Référence : Ableton Push 2 MIDI and Display Interface Manual (rev 1.1), section Display.
  VID 0x2982 / PID 0x1967, interface 0, bulk OUT endpoint 0x01
  En-tête de trame (16 o) : FF CC AA 88 00 00 00 00 00 00 00 00 00 00 00 00
  960 x 160 px, 16 bits little-endian : bits 15-11 = B, 10-5 = G, 4-0 = R
  Ligne = 1920 o de pixels + 128 o de remplissage = 2048 o ; 160 lignes = 327 680 o
  Masquage XOR 0xFFE7F3E7 : octets envoyés XOR E7, F3, E7, FF, répété
  L'écran passe au noir si aucune trame n'arrive pendant 2 s.

Usage : python spikes/s1_push2_display.py [--seconds 10] [--fps 30]
"""
from __future__ import annotations

import argparse
import time

import libusb_package
import numpy as np
import usb.core
import usb.util
from PIL import Image, ImageDraw, ImageFont

VID, PID = 0x2982, 0x1967
EP_OUT = 0x01
W, H = 960, 160
HEADER = bytes([0xFF, 0xCC, 0xAA, 0x88] + [0x00] * 12)
XOR = np.tile(np.array([0xE7, 0xF3, 0xE7, 0xFF], dtype=np.uint8), 2048 // 4)


def encode(img: Image.Image) -> bytes:
    """Image RGB 960x160 -> 327 680 octets prêts à envoyer."""
    a = np.asarray(img.convert("RGB"), dtype=np.uint16)
    r, g, b = a[..., 0] >> 3, a[..., 1] >> 2, a[..., 2] >> 3
    px = (b << 11) | (g << 5) | r                       # (160, 960) uint16
    buf = np.zeros((H, 2048), dtype=np.uint8)
    buf[:, :1920] = px.astype("<u2").view(np.uint8).reshape(H, 1920)
    buf ^= XOR
    return buf.tobytes()


def test_frame(t: float, fps_meas: float) -> Image.Image:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    bars = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255), (255, 0, 255), (255, 255, 255), (128, 128, 128)]
    for i, c in enumerate(bars):
        d.rectangle([i * 120, 0, i * 120 + 119, 40], fill=c)
    try:
        font = ImageFont.truetype("arial.ttf", 34)
    except OSError:
        font = ImageFont.load_default()
    d.text((20, 60), "push-xtouch-resolume  —  spike 1", fill=(255, 255, 255), font=font)
    d.text((20, 110), f"t = {t:5.1f} s   {fps_meas:4.1f} fps", fill=(255, 160, 0), font=font)
    x = int((t * 200) % W)                                 # barre mobile : vérifie la fluidité
    d.rectangle([x, 150, x + 40, 159], fill=(0, 200, 255))
    return img


def main(seconds: float, fps: float) -> None:
    backend = libusb_package.get_libusb1_backend()
    dev = usb.core.find(idVendor=VID, idProduct=PID, backend=backend)
    if dev is None:
        raise SystemExit("Push 2 introuvable via libusb")
    print("Trouvé :", dev.product, "| fabricant :", dev.manufacturer)
    try:
        dev.set_configuration()
    except usb.core.USBError as e:
        print("set_configuration ignoré :", e)  # déjà configuré par Windows
    usb.util.claim_interface(dev, 0)
    print("Interface 0 réclamée")

    period = 1.0 / fps
    t0 = time.perf_counter()
    n, last, fps_meas, worst = 0, t0, 0.0, 0.0
    while (t := time.perf_counter() - t0) < seconds:
        data = encode(test_frame(t, fps_meas))
        ts = time.perf_counter()
        dev.write(EP_OUT, HEADER, timeout=1000)
        dev.write(EP_OUT, data, timeout=1000)
        worst = max(worst, time.perf_counter() - ts)
        n += 1
        now = time.perf_counter()
        if now - last >= 1:
            fps_meas, n, last = n / (now - last), 0, now
        time.sleep(max(0.0, period - (time.perf_counter() - ts)))
    # Écran noir propre en sortie
    dev.write(EP_OUT, HEADER, timeout=1000)
    dev.write(EP_OUT, encode(Image.new("RGB", (W, H))), timeout=1000)
    usb.util.release_interface(dev, 0)
    usb.util.dispose_resources(dev)
    print(f"OK — dernier débit mesuré {fps_meas:.1f} fps, pire envoi d'une trame {worst * 1000:.1f} ms")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--fps", type=float, default=30)
    a = ap.parse_args()
    main(a.seconds, a.fps)
