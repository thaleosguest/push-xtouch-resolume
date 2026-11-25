import mido

from pxr.colors import scale
from pxr.config import Mapping
from pxr.drivers.push2 import CC_OF_BUTTON, ENCODER_CC, pad_note

from conftest import Rig

ENC = {v: k for k, v in ENCODER_CC.items()}


def pad(row, col, vel=100):
    return mido.Message("note_on", note=pad_note(row, col), velocity=vel)


def button(name, pressed=True):
    return mido.Message("control_change", control=CC_OF_BUTTON[name], value=127 if pressed else 0)


def sent_pads(wire):
    """Dernier message (canal 0) par pad -> vélocité."""
    out = {}
    for m in wire.take():
        if m.type == "note_on" and m.channel == 0:
            out[divmod(m.note - 36, 8)] = m.velocity
    return out


def test_pad_triggers_clip_press_and_release(rig):
    clip = rig.state.layers[0].clips[2]
    rig.feed(rig.push, pad(0, 2))
    rig.feed(rig.push, pad(0, 2, 0))
    path = f"/composition/clips/by-id/{clip.id}/connect"
    assert ("trigger", path, True) in rig.arena.sent and ("trigger", path, False) in rig.arena.sent
    assert clip.playing


def test_pad_feedback_reflects_arena_state(rig):
    rig.frame()
    pads = sent_pads(rig.push_wire)
    layer = rig.state.layers[0]
    rgb = rig.engine.layer_color(layer)
    dim = scale(rgb, rig.engine.map.colors.dim_factor)
    rgb_of = rig.push.palette.rgb_of
    assert rgb_of[pads[(0, 0)]] == dim                                                   # chargé = atténué
    assert (0, 5) not in pads or pads[(0, 5)] == 0                                       # vide = éteint
    rig.feed(rig.push, pad(0, 1))
    rig.frame()
    msgs = rig.push_wire.take()
    note = pad_note(0, 1)
    full = next(m for m in msgs if m.type == "note_on" and m.note == note and m.channel == 0)
    pulse = next(m for m in msgs if m.type == "note_on" and m.note == note and m.channel == 9)   # pulsation noire
    assert rgb_of[full.velocity] == rgb and rgb_of[pulse.velocity] == dim
    rig.frame()
    assert rig.push_wire.take() == []                                                    # rien n'a changé : rien n'est renvoyé


def test_clip_colorid_shows_on_pad(rig):
    clip = rig.state.layers[0].clips[2]
    rig.state.apply(clip.colorid.id, "3")
    rig.frame()
    pads = sent_pads(rig.push_wire)
    c = rig.engine.map.colors
    assert rig.push.palette.rgb_of[pads[(0, 2)]] == scale(c.colorid_led["3"], c.dim_factor)


def test_column_row_and_bank(rig):
    assert rig.engine.column_row == 3                                                    # 3 couches -> rangée 4
    rig.feed(rig.push, pad(3, 1))
    col = rig.state.columns[1]
    assert ("trigger", f"/composition/columns/by-id/{col.id}/connect", True) in rig.arena.sent
    assert all(L.clips[1].playing for L in rig.state.layers)
    rig.feed(rig.push, button("right"))
    assert rig.engine.bank == 8
    rig.feed(rig.push, button("right"))                                                   # 9 colonnes : pas de 3e banque
    assert rig.engine.bank == 8
    rig.feed(rig.push, pad(0, 0))                                                         # colonne 9 de la couche 1
    assert ("trigger", f"/composition/clips/by-id/{rig.state.layers[0].clips[8].id}/connect", True) in rig.arena.sent
    rig.feed(rig.push, pad(0, 3))                                                         # colonne 12 : n'existe pas
    assert rig.arena.sent[-1][2] is True and str(rig.state.layers[0].clips[8].id) in rig.arena.sent[-1][1]


def test_deck_button(rig):
    rig.feed(rig.push, button("scene3"))
    assert rig.state.selected_deck().name.value == "Generators"


def test_encoder_accumulates_and_flushes_once(rig):
    layer = rig.state.layers[1]
    op = rig.state.layer_param(layer, "video.opacity")
    step = rig.engine.map.push2.encoder_step
    for _ in range(5):
        for ev in rig.push.parse(mido.Message("control_change", control=ENC["enc2"], value=1)):
            rig.engine.handle(ev)
    rig.engine.flush_actions()
    sets = [a for a in rig.arena.sent if a[0] == "set" and a[1] == op.id]
    assert len(sets) == 1 and abs(sets[0][2] - (0.5 + 5 * step)) < 1e-9
    rig.feed(rig.push, mido.Message("control_change", control=ENC["enc2"], value=127))    # -1
    assert abs(op.value - (0.5 + 4 * step)) < 1e-9


def test_master_encoder_and_fx_page(rig):
    rig.feed(rig.push, mido.Message("control_change", control=ENC["enc8"], value=126))   # couche 8 absente : rien
    assert rig.state.master.value == 1.0
    rig.feed(rig.push, mido.Message("control_change", control=ENC["master"], value=126))  # encodeur master du Push
    assert rig.state.master.value < 1.0
    rig.feed(rig.push, button("device"))
    assert rig.engine.page == "fx"
    rig.feed(rig.push, button("upper3"))
    assert rig.engine.fx_sel == 2
    eff = rig.state.effects[2]
    name, prm = eff.params[1]
    before = prm.value
    rig.feed(rig.push, mido.Message("control_change", control=ENC["enc2"], value=3))
    assert prm.value > before
    rig.feed(rig.push, button("lower3"))
    assert eff.bypassed.value is True


def test_play_and_tap(rig):
    rig.feed(rig.push, button("play"))
    assert rig.state.play_state.value == "Pause"
    rig.feed(rig.push, button("tap_tempo"))
    tap = rig.state.tempo_tap.id
    assert ("set", tap, True) in rig.arena.sent and ("set", tap, False) in rig.arena.sent


def test_display_model(rig):
    dm = rig.engine._display_model()
    assert dm.upper[:3] == ["Layer 1", "Layer 2", "Layer 3"]
    assert dm.slots[1] == ("Layer 2", "50 %", 0.5)
    assert dm.slots[7][0] == ""                                                           # pas de doublon du master
    assert "128.0 BPM" in dm.status and "Footage Shop" in dm.title


def test_master_slot_option():
    r = Rig(Mapping.model_validate({"push2": {"master_slot": 7}}))
    r.feed(r.push, mido.Message("control_change", control=ENC["enc8"], value=126))
    assert r.state.master.value < 1.0
    assert r.engine._display_model().slots[7][0] == "Master"


# ─── X-Touch ──────────────────────────────────────────────────────────────

def fader_msg(ch, v01):
    return mido.Message("pitchwheel", channel=ch, pitch=round(v01 * 16383) - 8192)


def test_mcu_fader_sets_layer_opacity_and_echoes(rig):
    rig.feed(rig.mcu, fader_msg(0, 0.25))
    op = rig.state.layer_param(rig.state.layers[0], "video.opacity")
    assert abs(op.value - 0.25) < 1e-3
    echo = rig.mcu_wire.take()
    assert echo and echo[0].type == "pitchwheel" and echo[0].channel == 0                 # écho immédiat


def test_mcu_touched_fader_is_not_driven(rig):
    rig.frame()
    rig.mcu_wire.take()
    rig.feed(rig.mcu, mido.Message("note_on", note=0x69, velocity=127))                   # toucher fader 2
    op = rig.state.layer_param(rig.state.layers[1], "video.opacity")
    rig.state.apply(op.id, 0.9)                                                           # Arena bouge pendant ce temps
    rig.frame()
    assert not [m for m in rig.mcu_wire.take() if m.type == "pitchwheel" and m.channel == 1]
    rig.feed(rig.mcu, mido.Message("note_on", note=0x69, velocity=0))                     # relâché
    rig.clock += 1.0
    rig.frame()
    moves = [m for m in rig.mcu_wire.take() if m.type == "pitchwheel" and m.channel == 1]
    assert moves and abs((moves[-1].pitch + 8192) / 16383 - 0.9) < 1e-3


def test_mcu_buttons_and_strips(rig):
    rig.feed(rig.mcu, mido.Message("note_on", note=0x10, velocity=127))                   # Mute 1 -> bypass
    assert rig.state.layers[0].bypassed.value is True
    rig.feed(rig.mcu, mido.Message("note_on", note=0x09, velocity=127))                   # Solo 2
    assert rig.state.layers[1].solo.value is True
    rig.feed(rig.mcu, mido.Message("note_on", note=0x1A, velocity=127))                   # Select 3
    assert ("trigger", f"/composition/layers/by-id/{rig.state.layers[2].id}/select", None) in rig.arena.sent
    rig.frame()
    msgs = rig.mcu_wire.take()
    lcd_top = next(m for m in msgs if m.type == "sysex" and list(m.data[4:6]) == [0x12, 0])
    assert bytes(lcd_top.data[6:13]).decode() == "Layer 1"
    leds = {m.note: m.velocity for m in msgs if m.type == "note_on"}
    assert leds[0x10] == 127 and leds[0x09] == 127 and leds[0x11] == 0
    colors = next(m for m in msgs if m.type == "sysex" and m.data[4] == 0x72)
    assert list(colors.data[5:8]) != [0, 0, 0] and list(colors.data[8:13]) == [0] * 5     # 3 couches, 5 tranches vides


def test_mcu_master_and_encoder(rig):
    rig.feed(rig.mcu, fader_msg(8, 0.5))
    assert abs(rig.state.master.value - 0.5) < 1e-3
    dur = rig.state.layer_param(rig.state.layers[0], "transition.duration")
    rig.feed(rig.mcu, mido.Message("control_change", control=0x10, value=0x03))           # +3 crans
    assert abs(dur.value - 3 * 0.01 * 10) < 1e-9


def test_mapping_overrides():
    m = Mapping.model_validate({"push2": {"column_row": False, "bank_next": "page_right"}, "mcu": {"fader_param": "master"}})
    r = Rig(m)
    assert r.engine.column_row is None
    r.feed(r.push, button("page_right"))
    assert r.engine.bank == 8
    r.feed(r.mcu, fader_msg(0, 0.3))
    assert abs(r.state.layers[0].master.value - 0.3) < 1e-3
