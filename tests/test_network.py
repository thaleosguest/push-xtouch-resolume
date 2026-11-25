"""Mode réseau : scénarios du brief (Arena distante) avec le faux serveur Arena."""
import asyncio
import copy
import json

import mido

from pxr.arena import ArenaClient, ConnState
from pxr.config import ArenaCfg, Mapping, load_config
from pxr.drivers.mcu import McuDriver
from pxr.drivers.push2 import Push2Driver, pad_note
from pxr.engine import CommandThrottle, Engine, McuUnit
from pxr.fake_arena import demo_composition
from pxr.state import StateModel

from conftest import Rig, Wire
from fake_arena_server import FakeArenaServer, wait_for


def make_bridge(port: int):
    """Passerelle complète (client réseau + moteur + drivers sur faux ports MIDI)."""
    state = StateModel()
    client = ArenaClient(state, "127.0.0.1", port, initial_delay=0.05, max_delay=0.2, open_timeout=1.0)
    push_wire, mcu_wire = Wire(), Wire()
    push = Push2Driver(push_wire, lambda e: None)
    mcu = McuDriver("xtouch", mcu_wire, lambda e: None)
    engine = Engine(state, client, Mapping(), push=push, mcus=[McuUnit(mcu)])
    push.emit = mcu.emit = engine.handle
    state.subscribe(lambda kind, pid: engine.on_structure() if kind == "structure" else None)
    return state, client, engine, push, mcu, mcu_wire


def lcd_text(wire) -> str:
    """Dernier texte envoyé sur la ligne du haut des scribble strips."""
    tops = [m for m in wire.sent if m.type == "sysex" and list(m.data[4:6]) == [0x12, 0]]
    return bytes(tops[-1].data[6:]).decode() if tops else ""


# ─── 1. démarrer la passerelle avant Arena ──────────────────────────────────

def test_bridge_started_before_arena_connects_by_itself():
    async def scenario():
        server = FakeArenaServer(demo_composition())
        state, client, engine, push, mcu, mcu_wire = make_bridge(server.port)
        states = []
        client.on_state_change(states.append)
        task = asyncio.create_task(client.run())
        assert await wait_for(lambda: client.last_error, 4)        # Arena pas encore lancé : échec, on attend
        assert not client.connected
        engine.render(); mcu.flush()
        assert "ARENA OFFLINE" in lcd_text(mcu_wire) or "CONNEXION ARENA" in lcd_text(mcu_wire)
        assert engine._display_model().alert                       # message plein écran sur le Push
        await server.start()                                       # Arena démarre
        assert await wait_for(lambda: client.connected, 4)
        assert ConnState.CONNECTING in states and states[-1] is ConnState.CONNECTED
        assert await wait_for(lambda: len(server.commands("subscribe")) == len(state.params), 2)
        engine.render(); mcu.flush()
        assert "Layer 1" in lcd_text(mcu_wire)
        client.stop(); task.cancel(); await server.stop()

    asyncio.run(scenario())


# ─── 2. coupure d'Arena en cours d'utilisation, puis retour ─────────────────

def test_arena_dropout_shows_offline_then_resyncs():
    async def scenario():
        comp = demo_composition()
        server = FakeArenaServer(comp)
        await server.start()
        state, client, engine, push, mcu, mcu_wire = make_bridge(server.port)
        task = asyncio.create_task(client.run())
        assert await wait_for(lambda: client.connected, 3)

        # une commande passe, un changement fait dans Arena (ex. à la souris via Parsec) arrive
        clip = state.layers[0].clips[0]
        push.handle_midi(mido.Message("note_on", note=pad_note(0, 0), velocity=100))
        await wait_for(lambda: server.commands("trigger"), 2)
        assert server.commands("trigger")[0]["parameter"] == f"/composition/clips/by-id/{clip.id}/connect"
        await server.update(state.layers[1].clips[2].connected.id, "Connected")
        assert await wait_for(lambda: state.layers[1].clips[2].playing, 2)

        await server.kill()                                        # coupure brutale
        assert await wait_for(lambda: not client.connected, 3)
        engine.render(); mcu.flush()
        assert "ARENA OFFLINE" in lcd_text(mcu_wire) or "CONNEXION ARENA" in lcd_text(mcu_wire)
        sent = len(server.received)
        push.handle_midi(mido.Message("note_on", note=pad_note(0, 1), velocity=100))   # ignoré, pas de plantage
        mcu.handle_midi(mido.Message("pitchwheel", channel=0, pitch=0))
        engine.flush_actions()
        assert engine.throttle.pending == {}

        changed = copy.deepcopy(comp)                              # Arena revient avec un état différent
        changed["master"]["value"] = 0.3
        server.composition = changed
        await server.start()
        assert await wait_for(lambda: client.connected, 5)
        assert abs(state.master.value - 0.3) < 1e-9               # resynchronisé
        assert not state.layers[1].clips[2].playing                # l'état d'avant la coupure ne survit pas
        assert server.sessions == 2
        assert await wait_for(lambda: len(server.received) - sent == len(state.params), 2)   # réabonnement complet
        assert not [m for m in server.received[sent:] if m.get("action") in ("set", "trigger")]  # pas de rejeu
        engine.render(); mcu.flush()
        assert "Layer 1" in lcd_text(mcu_wire)
        client.stop(); task.cancel(); await server.stop()

    asyncio.run(scenario())


def test_empty_composition_sent_on_arena_quit_is_ignored():
    """Vu à l'essai réseau du 25/11/2026 : Arena envoie une composition vide (0 couche) juste avant de fermer la connexion."""
    async def scenario():
        comp = demo_composition()
        server = FakeArenaServer(comp)
        await server.start()
        state, client, engine, push, mcu, mcu_wire = make_bridge(server.port)
        task = asyncio.create_task(client.run())
        assert await wait_for(lambda: client.connected, 3)
        version = state.structure_version
        empty = {"name": {"id": 1, "valuetype": "ParamString", "value": ""}, "layers": [], "columns": [], "decks": []}
        for ws in list(server._conns):
            await ws.send(json.dumps(empty))
        await asyncio.sleep(0.2)
        assert state.structure_version == version and len(state.layers) == 3   # rien n'a été vidé
        client.stop(); task.cancel(); await server.stop()

    asyncio.run(scenario())


# ─── 3. fader bougé en continu : débit plafonné, dernière valeur reçue ──────

def test_throttle_caps_rate_and_keeps_last_value():
    t = CommandThrottle(60)
    sent = []
    now = 0.0
    for k in range(1000):                    # 1000 valeurs en 1 s
        now = k / 1000
        t.put(7, k)
        sent += t.due(now)
    sent += t.due(now + 1 / 60)              # créneau suivant : la dernière valeur part
    assert 55 <= len(sent) <= 62
    assert sent[-1] == (7, 999)


def test_fader_burst_through_engine_is_throttled():
    rig = Rig()
    op = rig.state.layer_param(rig.state.layers[0], "video.opacity")
    for k in range(500):                     # fader bougé en continu pendant 0,5 s (1 message / ms)
        for ev in rig.mcu.parse(mido.Message("pitchwheel", channel=0, pitch=k * 16 - 8192)):
            rig.engine.handle(ev)
        rig.clock += 0.001
        rig.engine.flush_actions()
    rig.clock += 0.05
    rig.engine.flush_actions()
    sets = [a for a in rig.arena.sent if a[0] == "set" and a[1] == op.id]
    assert 25 <= len(sets) <= 32             # ≈ 60 Hz sur 0,5 s
    assert abs(sets[-1][2] - (499 * 16) / 16383) < 1e-3


def test_triggers_are_not_throttled():
    rig = Rig()
    for _ in range(10):
        for ev in rig.push.parse(mido.Message("note_on", note=pad_note(0, 0), velocity=100)):
            rig.engine.handle(ev)
        for ev in rig.push.parse(mido.Message("note_on", note=pad_note(0, 0), velocity=0)):
            rig.engine.handle(ev)
    assert len([a for a in rig.arena.sent if a[0] == "trigger"]) == 20


# ─── 4. configuration : host, alias web_port, mode local inchangé ───────────

def test_config_network_fields(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("arena:\n  host: 192.168.1.50\n  web_port: 8081\n  reconnect: {initial_delay_ms: 250, max_delay_ms: 4000}\n",
                 encoding="utf-8")
    cfg = load_config(p)
    assert (cfg.arena.host, cfg.arena.port) == ("192.168.1.50", 8081)
    c = ArenaClient.from_cfg(StateModel(), cfg.arena)
    assert c.url == "ws://192.168.1.50:8081/api/v1" and (c.initial_delay, c.max_delay) == (0.25, 4.0)
    assert ArenaCfg().host == "127.0.0.1" and ArenaCfg().port == 8080          # mode local par défaut


def test_offline_engine_blocks_inputs_and_clears_controllers():
    rig = Rig()
    rig.frame()
    rig.arena.connected = False
    rig.push_wire.take(); rig.mcu_wire.take()
    rig.feed(rig.push, mido.Message("note_on", note=pad_note(0, 0), velocity=100))
    assert not [a for a in rig.arena.sent if a[0] == "trigger"]
    rig.frame()
    pads = [m for m in rig.push_wire.take() if m.type == "note_on" and m.channel == 0]
    assert pads and all(m.velocity == 0 for m in pads)               # pads éteints
    msgs = rig.mcu_wire.take()
    colors = next(m for m in msgs if m.type == "sysex" and m.data[4] == 0x72)
    assert list(colors.data[5:]) == [1] * 8                            # écrans en rouge
    assert not [m for m in msgs if m.type == "pitchwheel"]            # moteurs laissés en place
    dm = rig.engine._display_model()
    assert dm.alert == "ARENA OFFLINE"
