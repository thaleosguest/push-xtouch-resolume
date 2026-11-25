from pxr.fake_arena import demo_composition
from pxr.state import StateModel


def test_load_demo_composition():
    s = StateModel()
    s.load(demo_composition())
    assert (len(s.layers), len(s.columns), len(s.decks), len(s.effects)) == (3, 9, 4, 9)
    assert s.layers[0].display_name == "Layer 1"
    assert s.layers[0].clips[0].name.value == "FogAndDust"
    assert s.layers[0].clips[5].empty
    assert s.selected_deck().name.value == "Footage Shop"
    assert s.tempo.value == 128.0
    assert s.effects[0].name == "Shift RGB"
    assert all(p.is_range for _, p in s.effects[0].params)


def test_apply_update_and_notifications():
    s = StateModel()
    seen = []
    s.subscribe(lambda kind, pid: seen.append((kind, pid)))
    s.load(demo_composition())
    clip = s.layers[1].clips[2]
    assert s.layers[1].playing_clip is None
    assert s.apply(clip.connected.id, "Connected")
    assert s.layers[1].playing_clip is clip
    assert not s.apply(clip.connected.id, "Connected")          # valeur inchangée : pas de notification
    assert not s.apply(123456789, 1)                             # id inconnu
    assert seen == [("structure", None), ("param", clip.connected.id)]


def test_reload_keeps_param_objects():
    s = StateModel()
    comp = demo_composition()
    s.load(comp)
    master = s.master
    comp["master"]["value"] = 0.25
    s.load(comp)
    assert s.master is master and master.value == 0.25


def test_extra_layer_path_and_norm():
    s = StateModel()
    s.track_layer_path("transition.duration")
    s.load(demo_composition())
    p = s.layer_param(s.layers[0], "transition.duration")
    assert p is not None and p.is_range and (p.min, p.max) == (0.0, 10.0)
    assert p.denorm(0.5) == 5.0
    op = s.layer_param(s.layers[1], "video.opacity")
    assert op.norm() == 0.5
    assert s.layer_param(s.layers[0], "does.not.exist") is None
