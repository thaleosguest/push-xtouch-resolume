import mido
import numpy as np
from PIL import Image

from pxr.colors import push2_index, rgb_to_xtouch, XT
from pxr.drivers.mcu import McuDriver
from pxr.drivers.push2 import Push2Driver
from pxr.drivers.push2_display import DisplayModel, Renderer, encode

from conftest import Wire


def test_push2_parse():
    d = Push2Driver(Wire(), lambda e: None)
    ev, = d.parse(mido.Message("note_on", note=36, velocity=40))
    assert (ev.kind, ev.x, ev.y, ev.value) == ("pad", 0, 0, 40)
    ev, = d.parse(mido.Message("note_on", note=99, velocity=1))
    assert (ev.x, ev.y) == (7, 7)
    ev, = d.parse(mido.Message("control_change", control=71, value=125))
    assert (ev.kind, ev.name, ev.value) == ("encoder", "enc1", -3)
    ev, = d.parse(mido.Message("control_change", control=43, value=127))
    assert (ev.kind, ev.name, ev.value) == ("button", "scene1", 1)
    ev, = d.parse(mido.Message("note_on", note=1, velocity=127))
    assert (ev.kind, ev.name) == ("touch", "enc2")
    ev, = d.parse(mido.Message("pitchwheel", pitch=8191))
    assert ev.kind == "touchstrip" and ev.value == 1.0


def entries(msgs):
    """Messages SysEx 0x03 -> {index: (r, g, b)}."""
    out = {}
    for m in msgs:
        if m.type == "sysex" and m.data[5] == 0x03:
            d = m.data[6:]
            out[d[0]] = tuple(d[1 + 2 * k] | (d[2 + 2 * k] << 7) for k in range(3))
    return out


def test_push2_palette_writes_then_notes_and_only_changes():
    w = Wire()
    d = Push2Driver(w, lambda e: None)
    d.set_pad(0, 0, (255, 120, 0))
    d.set_pad(1, 0, (0, 190, 255), "pulse", (0, 30, 40))
    d.flush()
    msgs = w.take()
    pal = entries(msgs)
    assert sorted(pal.values()) == sorted([(255, 120, 0), (0, 190, 255), (0, 30, 40)])
    assert not set(pal) & {0, 16, 48, 122, 123, 124, 125, 126, 127}              # entrées réservées intactes
    kinds = [m.type for m in msgs]
    assert kinds.index("note_on") > max(i for i, k in enumerate(kinds) if k == "sysex")   # palette avant les LED
    assert any(m.type == "sysex" and list(m.data) == [0, 0x21, 0x1D, 1, 1, 0x05] for m in msgs)  # reapply
    idx = {v: k for k, v in pal.items()}
    notes = [(m.channel, m.note, m.velocity) for m in msgs if m.type == "note_on"]
    assert notes == [(0, 36, idx[(255, 120, 0)]), (0, 44, idx[(0, 190, 255)]), (9, 44, idx[(0, 30, 40)])]
    d.flush()
    assert w.take() == []                                                        # rien n'a changé : rien n'est envoyé
    d.set_pad(0, 1, (255, 120, 0))                                               # couleur connue : pas de réécriture
    d.flush()
    msgs = w.take()
    assert entries(msgs) == {} and [(m.note, m.velocity) for m in msgs] == [(37, idx[(255, 120, 0)])]
    d.on_open()                                                                  # reconnexion : palette et LED renvoyées
    d.clock.stop()
    d.flush()
    msgs = w.take()
    assert len(entries(msgs)) == 3 and len([m for m in msgs if m.type == "note_on"]) == 4


def test_restore_palette_writes_factory_values():
    w = Wire()
    d = Push2Driver(w, lambda e: None)
    d.set_pad(0, 0, (1, 2, 3))
    d.flush()
    i = d._sent_pads[(0, 0)][0]
    w.take()
    d.restore_palette()
    from pxr.colors import push2_palette_entries
    assert entries(w.take()) == {i: push2_palette_entries()[i][:3]}
    assert d.palette.rgb_of == {}


def test_palette_recycles_only_unused_entries():
    w = Wire()
    d = Push2Driver(w, lambda e: None)
    d.set_pad(0, 0, (1, 2, 3))
    d.flush()
    shown = d._sent_pads[(0, 0)][0]
    for k in range(200):                                                         # bien plus que les 118 entrées libres
        d.set_pad(1, 0, (k, 255 - k, 7))
        d.flush()
    assert d._sent_pads[(0, 0)][0] == shown and d.palette.rgb_of[shown] == (1, 2, 3)
    w.take()


def test_mcu_lcd_and_colors():
    w = Wire()
    d = McuDriver("x", w, lambda e: None)
    d.set_strip(1, "Layerééé 2", "Clip", 4)
    d.flush()
    msgs = w.take()
    top = msgs[0]
    assert list(top.data[:6]) == [0x00, 0x00, 0x66, 0x14, 0x12, 0]
    assert bytes(top.data[6:6 + 14]).decode() == "       Layeree"        # accents retirés, 7 caractères
    colors = next(m for m in msgs if m.data[4] == 0x72)
    assert list(colors.data[5:]) == [7, 4, 7, 7, 7, 7, 7, 7]


def test_display_encode_layout():
    img = Image.new("RGB", (960, 160), (255, 0, 0))
    raw = np.frombuffer(encode(img), dtype=np.uint8).reshape(160, 2048)
    xor = np.tile(np.array([0xE7, 0xF3, 0xE7, 0xFF], dtype=np.uint8), 512)
    px = (raw ^ xor)[:, :1920].copy().view("<u2")
    assert px.shape == (160, 960) and (px == 0x001F).all()                       # rouge = bits 4-0
    assert ((raw ^ xor)[:, 1920:] == 0).all()                                      # remplissage
    Renderer().render(DisplayModel(title="t", status="s"))                         # le rendu ne plante pas


def test_colors():
    assert push2_index((255, 0, 0)) == 127 and push2_index((0, 0, 0)) == 0
    assert push2_index((0, 255, 0)) == 126
    assert rgb_to_xtouch((255, 0, 0)) == XT["rouge"] and rgb_to_xtouch((0, 0, 255)) == XT["bleu"]
    assert rgb_to_xtouch((200, 200, 200)) == XT["blanc"]
