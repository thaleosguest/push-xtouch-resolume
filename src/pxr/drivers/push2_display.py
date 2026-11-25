"""Écran du Push 2 : USB bulk via pyusb + libusb (pilote WinUSB déjà installé par Windows, spike 1).

Manuel Ableton rev 1.1 : VID 0x2982 / PID 0x1967, interface 0, endpoint 0x01 ; en-tête FF CC AA 88 + 12 x 00 ;
960 x 160, pixels 16 bits little-endian (B bits 15-11, G 10-5, R 4-0), lignes de 1920 o + 128 o de remplissage,
XOR 0xFFE7F3E7 ; écran noir si aucune trame pendant 2 s. Image vérifiée visuellement le 25/11/2026.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

VID, PID, EP_OUT = 0x2982, 0x1967, 0x01
W, H = 960, 160
HEADER = bytes([0xFF, 0xCC, 0xAA, 0x88] + [0x00] * 12)
_XOR = np.tile(np.array([0xE7, 0xF3, 0xE7, 0xFF], dtype=np.uint8), 2048 // 4)


def encode(img: Image.Image) -> bytes:
    a = np.asarray(img.convert("RGB"), dtype=np.uint16)
    px = ((a[..., 2] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 0] >> 3)
    buf = np.zeros((H, 2048), dtype=np.uint8)
    buf[:, :1920] = px.astype("<u2").view(np.uint8).reshape(H, 1920)
    buf ^= _XOR
    return buf.tobytes()


@dataclass
class DisplayModel:
    upper: list[str] = field(default_factory=lambda: [""] * 8)        # libellés des boutons du haut
    upper_on: list[bool] = field(default_factory=lambda: [False] * 8)
    slots: list[tuple[str, str, float | None]] = field(default_factory=lambda: [("", "", None)] * 8)
    lower: list[str] = field(default_factory=lambda: [""] * 8)
    lower_on: list[bool] = field(default_factory=lambda: [False] * 8)
    title: str = ""
    status: str = ""
    accent: tuple[int, int, int] = (255, 140, 0)
    alert: str = ""            # message plein écran (ex. « ARENA OFFLINE ») : remplace tout le reste
    alert_detail: str = ""


def _font(size: int):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


class Renderer:
    def __init__(self) -> None:
        self.f_small, self.f_med, self.f_big, self.f_huge = _font(15), _font(18), _font(24), _font(60)

    def _fit(self, d: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
        if d.textlength(text, font=font) <= width:
            return text
        while text and d.textlength(text + "…", font=font) > width:
            text = text[:-1]
        return text + "…"

    def render(self, m: DisplayModel) -> Image.Image:
        img = Image.new("RGB", (W, H), (0, 0, 0))
        d = ImageDraw.Draw(img)
        if m.alert:
            offline = "OFFLINE" in m.alert
            d.rectangle([0, 0, W, H], fill=(120, 0, 0) if offline else (90, 60, 0))
            tw = d.textlength(m.alert, font=self.f_huge)
            d.text(((W - tw) / 2, 28), m.alert, font=self.f_huge, fill=(255, 255, 255))
            detail = self._fit(d, m.alert_detail, self.f_med, W - 40)
            dw = d.textlength(detail, font=self.f_med)
            d.text(((W - dw) / 2, 112), detail, font=self.f_med, fill=(255, 210, 200))
            return img
        for i in range(8):
            x = i * 120
            # boutons du haut
            if m.upper[i]:
                if m.upper_on[i]:
                    d.rectangle([x + 2, 0, x + 117, 22], fill=m.accent)
                d.text((x + 6, 2), self._fit(d, m.upper[i], self.f_small, 108), font=self.f_small,
                       fill=(0, 0, 0) if m.upper_on[i] else (200, 200, 200))
            # encodeur
            label, value, norm = m.slots[i]
            if label:
                d.text((x + 6, 32), self._fit(d, label, self.f_med, 108), font=self.f_med, fill=(255, 255, 255))
                d.text((x + 6, 56), self._fit(d, value, self.f_big, 108), font=self.f_big, fill=m.accent)
                if norm is not None:
                    d.rectangle([x + 6, 90, x + 113, 97], outline=(90, 90, 90))
                    d.rectangle([x + 6, 90, x + 6 + int(107 * max(0.0, min(1.0, norm))), 97], fill=m.accent)
            # boutons du bas
            if m.lower[i]:
                if m.lower_on[i]:
                    d.rectangle([x + 2, 108, x + 117, 130], fill=(80, 80, 80))
                d.text((x + 6, 110), self._fit(d, m.lower[i], self.f_small, 108), font=self.f_small, fill=(220, 220, 220))
        d.line([0, 136, W, 136], fill=(60, 60, 60))
        d.text((6, 139), self._fit(d, m.title, self.f_med, 600), font=self.f_med, fill=(255, 255, 255))
        sw = d.textlength(m.status, font=self.f_med)
        d.text((W - 6 - sw, 139), m.status, font=self.f_med, fill=m.accent)
        return img


class Push2Display:
    """Thread d'affichage : rend le dernier DisplayModel à `fps` et se reconnecte tout seul."""

    def __init__(self, fps: float = 20) -> None:
        self.model = DisplayModel(title="push-xtouch-resolume", status="démarrage…")
        self.fps = fps
        self.renderer = Renderer()
        self._run = threading.Event()
        self._th: threading.Thread | None = None
        self._dev = None
        self.connected = False

    def start(self) -> None:
        self._run.set()
        self._th = threading.Thread(target=self._loop, name="push2-display", daemon=True)
        self._th.start()

    def stop(self) -> None:
        self._run.clear()
        if self._th:
            self._th.join(2)

    def _open(self) -> bool:
        import libusb_package
        import usb.core
        import usb.util
        dev = usb.core.find(idVendor=VID, idProduct=PID, backend=libusb_package.get_libusb1_backend())
        if dev is None:
            return False
        try:
            dev.set_configuration()
        except usb.core.USBError:
            pass  # déjà configuré par Windows
        usb.util.claim_interface(dev, 0)
        self._dev = dev
        return True

    def _close(self) -> None:
        if self._dev is not None:
            import usb.util
            try:
                usb.util.release_interface(self._dev, 0)
                usb.util.dispose_resources(self._dev)
            except Exception:
                pass
        self._dev = None

    def _write(self, img: Image.Image) -> None:
        self._dev.write(EP_OUT, HEADER, timeout=1000)
        self._dev.write(EP_OUT, encode(img), timeout=1000)

    def _loop(self) -> None:
        last_frame, last_model = b"", None
        while self._run.is_set():
            if self._dev is None:
                try:
                    ok = self._open()
                except Exception as e:
                    ok = False
                    log.debug("écran Push 2 : ouverture impossible %s", e)
                if not ok:
                    if self.connected:
                        log.warning("écran Push 2 déconnecté")
                    self.connected = False
                    time.sleep(2)
                    continue
                log.info("écran Push 2 connecté")
                self.connected, last_model = True, None
            t = time.perf_counter()
            try:
                model = self.model
                if model is not last_model:   # même modèle : on renvoie la trame encodée (garde l'écran allumé)
                    last_frame, last_model = encode(self.renderer.render(model)), model
                self._dev.write(EP_OUT, HEADER, timeout=1000)
                self._dev.write(EP_OUT, last_frame, timeout=1000)
            except Exception as e:
                log.warning("écran Push 2 : envoi impossible (%s), reconnexion", e)
                self._close()
                continue
            time.sleep(max(0.0, 1.0 / self.fps - (time.perf_counter() - t)))
        if self._dev is not None:
            try:
                self._write(Image.new("RGB", (W, H)))
            except Exception:
                pass
            self._close()
