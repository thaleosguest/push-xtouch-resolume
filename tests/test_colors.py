from pxr.colors import XT, rgb_to_xtouch
from pxr.config import Mapping

from conftest import Rig


def test_colorid_leds_are_distinct_on_xtouch():
    m = Mapping().colors.colorid_led
    got = {k: rgb_to_xtouch(v) for k, v in m.items()}
    assert got == {"2": XT["rouge"], "3": XT["cyan"], "4": XT["vert"], "5": XT["bleu"], "6": XT["violet"]}


def test_first_layer_fallbacks_are_distinct_on_xtouch():
    fb = Mapping().colors.layer_fallback
    assert len({rgb_to_xtouch(c) for c in fb[:3]}) == 3


def test_clip_colorid_overrides_layer_colour():
    r = Rig()
    layer = r.state.layers[0]
    clip = layer.clips[1]
    c = r.engine.map.colors
    assert r.engine.clip_color(clip, layer) == c.layer_fallback[0]      # colorid "1" = pas de couleur -> couche
    r.state.apply(clip.colorid.id, "6")
    assert r.engine.clip_color(clip, layer) == c.colorid_led["6"]
    r.state.apply(layer.colorid.id, "5")
    assert r.engine.layer_color(layer) == c.colorid_led["5"]
