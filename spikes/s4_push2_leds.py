"""Spike 4 — Push 2 MIDI : identification, palette LED, animations, journal des entrées.

Référence : Ableton Push 2 MIDI and Display Interface Manual (rev 1.1).
  SysEx  : F0 00 21 1D 01 01 <cmd> <args> F7
  0x04   : Get LED Color Palette Entry  -> réponse 04 i rL rM gL gM bL bM wL wM
  LEDs   : pads = note-on (90 nn idx), boutons = CC (B0 cc idx), canal = animation
           ch0 statique, ch1-5 one-shot, ch6-10 pulsation, ch11-15 clignotement
           (24e, 16e, 8e, noire, blanche). Animations synchronisées sur l'horloge
           MIDI (FA start puis F8 à 24 ppq).

Usage : python spikes/s4_push2_leds.py [--port "^Ableton Push 2"] [--listen 30]
"""
from __future__ import annotations

import argparse
import json
import threading
import time

import mido

from common import OUT_DIR, hexs, open_io

HDR = [0x00, 0x21, 0x1D, 0x01, 0x01]


def sysex(cmd: int, *args: int) -> mido.Message:
    return mido.Message("sysex", data=HDR + [cmd, *args])


def wait_sysex(inp, pred, timeout=1.0):
    end = time.time() + timeout
    while time.time() < end:
        for m in inp.iter_pending():
            if m.type == "sysex" and pred(list(m.data)):
                return list(m.data)
        time.sleep(0.002)
    return None


def pad_note(row: int, col: int) -> int:
    """row 0 = rangée du bas (hypothèse à confirmer par l'écoute ci-dessous)."""
    return 36 + 8 * row + col


class Clock:
    """Envoie FA puis F8 à 24 ppq : nécessaire pour que les animations tournent."""

    def __init__(self, out, bpm=120.0):
        self.out, self.bpm, self.stop = out, bpm, threading.Event()
        self.th = threading.Thread(target=self.run, daemon=True)

    def run(self):
        self.out.send(mido.Message("start"))
        period = 60.0 / (self.bpm * 24)
        nxt = time.perf_counter()
        while not self.stop.is_set():
            self.out.send(mido.Message("clock"))
            nxt += period
            time.sleep(max(0.0, nxt - time.perf_counter()))


def main(port_rx: str, listen: float) -> None:
    inp, out = open_io(port_rx, port_rx)
    report: dict = {}

    # 1. Device inquiry (format MIDI standard)
    out.send(mido.Message("sysex", data=[0x7E, 0x01, 0x06, 0x01]))
    r = wait_sysex(inp, lambda d: d[:4] == [0x7E, 0x01, 0x06, 0x02])
    report["device_inquiry"] = hexs(r) if r else None
    print("Device inquiry :", report["device_inquiry"])

    # 2. Lecture de la palette par défaut (128 entrées)
    palette = {}
    for i in range(128):
        out.send(sysex(0x04, i))
        r = wait_sysex(inp, lambda d, i=i: d[:7] == HDR + [0x04, i], 0.5)
        if r:
            a = r[7:]
            palette[i] = {k: a[n] | (a[n + 1] << 7) for k, n in (("r", 0), ("g", 2), ("b", 4), ("w", 6))}
    (OUT_DIR / "push2_palette.json").write_text(json.dumps(palette, indent=1), encoding="utf-8")
    print(f"Palette lue : {len(palette)}/128 entrées -> spikes/out/push2_palette.json")
    report["palette_entries_read"] = len(palette)

    # 3. Brillance LED actuelle (limitée à 8 sur alim USB selon le manuel)
    out.send(sysex(0x07))
    r = wait_sysex(inp, lambda d: d[:6] == HDR + [0x07])
    report["led_brightness"] = r[6] if r else None
    print("Brillance LED :", report["led_brightness"])

    # 4. Affichage palette : index 0-63 puis 64-127
    for page in (0, 64):
        print(f"Pads = index palette {page}..{page + 63} (pad bas-gauche = {page})")
        for row in range(8):
            for col in range(8):
                out.send(mido.Message("note_on", note=pad_note(row, col), velocity=page + row * 8 + col))
        time.sleep(4)

    # 5. Animations : rangée par rangée, statique + animation sur un canal
    clock = Clock(out, 120)
    clock.th.start()
    rows = [  # (description, canal)
        ("statique (ch0)", 0), ("one-shot noire (ch4)", 4), ("pulsation 8e (ch8)", 8),
        ("pulsation noire (ch9)", 9), ("pulsation blanche (ch10)", 10),
        ("clignotement 8e (ch13)", 13), ("clignotement noire (ch14)", 14), ("clignotement blanche (ch15)", 15),
    ]
    print("Animations @120 BPM, rangée du bas -> haut :")
    for row, (desc, ch) in enumerate(rows):
        print(f"  rangée {row + 1}: {desc}")
        for col in range(8):
            n = pad_note(row, col)
            out.send(mido.Message("note_on", note=n, velocity=124, channel=0))      # gris foncé
            if ch:
                out.send(mido.Message("note_on", note=n, velocity=5 + col * 15, channel=ch))
            else:
                out.send(mido.Message("note_on", note=n, velocity=5 + col * 15, channel=0))
    # Boutons de scène (CC 36-43) en blanc/couleur, flèches en blanc
    for cc in range(36, 44):
        out.send(mido.Message("control_change", control=cc, value=126))
    for cc in (44, 45, 46, 47):
        out.send(mido.Message("control_change", control=cc, value=127))

    # 6. Journal des entrées
    print(f"\nÉCOUTE {listen:.0f} s : appuie pad bas-gauche, pad haut-droit, boutons, encodeurs, touch strip…")
    events = []
    end = time.time() + listen
    while time.time() < end:
        for m in inp.iter_pending():
            if m.type in ("clock", "active_sensing"):
                continue
            events.append(str(m))
            print("  <-", m)
        time.sleep(0.002)
    report["input_events"] = events

    clock.stop.set()
    clock.th.join()
    out.send(mido.Message("stop"))
    for row in range(8):
        for col in range(8):
            out.send(mido.Message("note_on", note=pad_note(row, col), velocity=0))
    for cc in list(range(36, 48)):
        out.send(mido.Message("control_change", control=cc, value=0))
    (OUT_DIR / "s4_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("Rapport -> spikes/out/s4_report.json")
    inp.close(); out.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=r"^Ableton Push 2 \d+$")
    ap.add_argument("--listen", type=float, default=30)
    a = ap.parse_args()
    main(a.port, a.listen)
