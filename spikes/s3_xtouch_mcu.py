"""Spike 3 — X-Touch en mode MCU : scribble strips + couleurs, faders motorisés, LED, couronnes, journal.

Valeurs reprises de LV1-workspace/xtouch-lv1 (xtouch_probe.py / surface/notes.py), validées sur la X-Touch
de test le 14/10/2026 :
  SysEx       F0 00 00 66 <id> ... F7, id 0x14 = X-Touch (0x15 = Extender)
  couleurs    <hdr> 72 c1..c8      (0 vide, 1 rouge, 2 vert, 3 jaune, 4 bleu, 5 violet, 6 cyan, 7 blanc)
  LCD         <hdr> 12 <pos> <ascii>  (7 car./tranche, ligne 2 à partir de 56)
  faders      pitch bend canal 0-7 (tranches), 8 (master), 14 bits ; toucher = notes 0x68-0x70
  boutons     Rec 0x00, Solo 0x08, Mute 0x10, Select 0x18 (+ tranche) ; LED = même note 127/0
  encodeurs   CC 0x10-0x17 relatifs (bit 6 = sens) ; couronnes CC 0x30-0x37
Règle connue : renvoyer la position du fader pendant qu'on le bouge (écho), sinon il revient en arrière.

Usage : python spikes/s3_xtouch_mcu.py [--port "^X-Touch \\d+$"] [--listen 30]
"""
from __future__ import annotations

import argparse
import json
import time

import mido

from common import OUT_DIR, open_io

DEV = 0x14
HDR = [0x00, 0x00, 0x66, DEV]


def lcd(out, row: int, labels: list[str]) -> None:
    text = "".join(s[:7].ljust(7) for s in (labels + [""] * 8)[:8])
    out.send(mido.Message("sysex", data=HDR + [0x12, 0 if row == 0 else 56] + [min(0x7E, max(0x20, ord(c))) for c in text]))


def colors(out, cols: list[int]) -> None:
    out.send(mido.Message("sysex", data=HDR + [0x72] + [c & 7 for c in cols]))


def fader(out, ch: int, v14: int) -> None:
    out.send(mido.Message("pitchwheel", channel=ch, pitch=v14 - 8192))


def main(port_rx: str, listen: float) -> None:
    inp, out = open_io(port_rx, port_rx)
    report: dict = {}

    lcd(out, 0, [f"Strip {i + 1}" for i in range(8)])
    lcd(out, 1, ["rouge", "vert", "jaune", "bleu", "violet", "cyan", "blanc", "vide"])
    colors(out, [1, 2, 3, 4, 5, 6, 7, 0])
    print("Strips : texte + couleurs (rouge, vert, jaune, bleu, violet, cyan, blanc, vide)")

    # Balayage des 9 faders : 0 -> haut -> milieu, 50 Hz, mesure du temps d'envoi
    print("Balayage moteurs…")
    t = time.perf_counter()
    steps = 50
    for k in list(range(steps + 1)) + list(range(steps, steps // 2 - 1, -1)):
        v = int(16383 * k / steps)
        for ch in range(9):
            fader(out, ch, v)
        time.sleep(0.02)
    report["sweep_s"] = round(time.perf_counter() - t, 2)

    # Couronnes en balayage + LED Select
    for pos in range(1, 12):
        for s in range(8):
            out.send(mido.Message("control_change", control=0x30 + s, value=(0 << 4) | pos))
        time.sleep(0.04)
    for s in range(8):
        out.send(mido.Message("note_on", note=0x18 + s, velocity=127))
    time.sleep(0.5)
    for s in range(8):
        out.send(mido.Message("note_on", note=0x18 + s, velocity=0))

    # Écoute interactive : écho fader, Select/Solo/Mute à bascule, encodeurs -> couronne
    print(f"\nÉCOUTE {listen:.0f} s : bouge des faders (dont master), touche-les, appuie Select/Solo/Mute, "
          "tourne les encodeurs, essaie Play/Stop…")
    toggles: dict[int, bool] = {}
    ring = [6] * 8
    events, touches = [], {}
    end = time.time() + listen
    while time.time() < end:
        for m in inp.iter_pending():
            now = time.perf_counter()
            if m.type == "pitchwheel":
                v = m.pitch + 8192
                fader(out, m.channel, v)                       # écho Mackie
                lcd(out, 1, [""] * m.channel + [f"{v * 100 // 16383:3d}%"]) if m.channel < 8 else None
                events.append(f"fader ch{m.channel} {v}")
                continue
            if m.type == "note_on":
                n, v = m.note, m.velocity
                if 0x68 <= n <= 0x70:
                    touches[n] = touches.get(n, 0) + (1 if v else 0)
                elif v and n < 0x20:                            # Rec/Solo/Mute/Select : bascule de la LED
                    toggles[n] = not toggles.get(n, False)
                    out.send(mido.Message("note_on", note=n, velocity=127 if toggles[n] else 0))
            if m.type == "control_change" and 0x10 <= m.control <= 0x17:
                s = m.control - 0x10
                d = -(m.value & 0x0F) if m.value & 0x40 else (m.value & 0x0F)
                ring[s] = max(1, min(11, ring[s] + d))
                out.send(mido.Message("control_change", control=0x30 + s, value=ring[s]))
            events.append(str(m))
            print("  <-", m)
        time.sleep(0.001)
    report["events"] = events
    report["touch_notes_seen"] = sorted(hex(n) for n in touches)

    # Remise à zéro
    colors(out, [7] * 8)
    lcd(out, 0, [""] * 8); lcd(out, 1, [""] * 8)
    for s in range(8):
        out.send(mido.Message("control_change", control=0x30 + s, value=0))
    for n in range(0x20):
        out.send(mido.Message("note_on", note=n, velocity=0))
    (OUT_DIR / "s3_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("Rapport -> spikes/out/s3_report.json")
    inp.close(); out.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=r"^X-Touch \d+$")
    ap.add_argument("--listen", type=float, default=30)
    a = ap.parse_args()
    main(a.port, a.listen)
