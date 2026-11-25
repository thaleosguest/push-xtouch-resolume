"""Point d'entrée console : python -m pxr [--config config.yaml] [--mapping mapping.yaml] [--dry-run].
(L'application installée passe par pxr.tray : icône près de l'horloge.)"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .arena import ArenaClient
from .config import Config, Mapping, load_config, load_mapping
from .drivers.mcu import McuDriver
from .drivers.push2 import Push2Driver
from .drivers.push2_display import Push2Display
from .engine import Engine, McuUnit
from .fake_arena import FakeArena
from .midi import MidiPort
from .state import StateModel

log = logging.getLogger("pxr")


@dataclass
class Status:
    """État des connexions, lu par l'icône de la zone de notification (lecture seule depuis un autre thread)."""
    arena: bool = False
    composition: str = ""
    push_midi: bool | None = None       # None = désactivé dans la config
    push_display: bool | None = None
    mcu: dict[str, bool] = field(default_factory=dict)
    running: bool = False


async def run(cfg: Config, mapping: Mapping, dry_run: bool = False,
              stop: asyncio.Event | None = None, status: Status | None = None) -> None:
    loop = asyncio.get_running_loop()
    status = status if status is not None else Status()
    state = StateModel()
    arena = FakeArena(state) if dry_run else ArenaClient(state, cfg.arena.host, cfg.arena.port)
    engine: Engine | None = None

    def emit(ev) -> None:
        if engine is not None:
            engine.handle(ev)

    def midi_in(driver):
        # rtmidi appelle depuis son propre thread : on repasse dans la boucle asyncio (état des drivers mono-thread)
        return lambda msg: loop.call_soon_threadsafe(driver.handle_midi, msg)

    ports: list[tuple[MidiPort, object]] = []
    push = display = None
    if cfg.push2.enabled:
        push_port = MidiPort("Push 2", cfg.push2.midi.input, cfg.push2.midi.output, on_message=lambda m: None)
        push = Push2Driver(push_port.send, emit)
        push_port.on_message, push_port.on_open, push_port.on_close = midi_in(push), push.on_open, push.on_close
        ports.append((push_port, push))
        if cfg.push2.display:
            display = Push2Display(cfg.push2.display_fps)
            display.start()
    units = []
    for mc in cfg.mcu:
        if not mc.enabled:
            continue
        port = MidiPort(mc.name, mc.midi.input, mc.midi.output, on_message=lambda m: None)
        drv = McuDriver(mc.name, port.send, emit, mc.sysex_id, mc.has_master)
        port.on_message, port.on_open, port.on_close = midi_in(drv), drv.on_open, drv.on_close
        ports.append((port, drv))
        units.append(McuUnit(drv, mc.strip_offset))

    engine = Engine(state, arena, mapping, push=push, display=display, mcus=units)
    state.subscribe(lambda kind, pid: engine.on_structure() if kind == "structure" else None)

    arena_task = asyncio.create_task(arena.run(), name="arena")
    period = 1.0 / cfg.refresh_hz
    next_poll = 0.0
    log.info("démarrage dry_run=%s refresh_hz=%s controleurs=%s", dry_run, cfg.refresh_hz,
             [p.label for p, _ in ports])
    status.running = True
    status.push_midi = False if push is not None else None
    status.push_display = False if display is not None else None
    try:
        while stop is None or not stop.is_set():
            now = loop.time()
            if now >= next_poll:
                for port, _ in ports:
                    port.poll()
                next_poll = now + cfg.port_poll_s
            engine.flush_actions()
            engine.render()
            if push is not None and state.tempo is not None:
                push.clock.bpm = float(state.tempo.value)
            for port, drv in ports:
                if port.is_open:
                    drv.flush()
            status.arena = bool(arena.connected)
            status.composition = state.name if state.loaded else ""
            for port, drv in ports:
                if drv is push:
                    status.push_midi = port.is_open
                else:
                    status.mcu[port.label] = port.is_open
            if display is not None:
                status.push_display = display.connected
            if arena_task.done() and arena_task.exception():
                raise arena_task.exception()
            await asyncio.sleep(period)
    finally:
        status.running = False
        log.info("arrêt : extinction des contrôleurs")
        arena.stop()
        arena_task.cancel()
        for port, drv in ports:
            if port.is_open:
                drv.clear()
                drv.flush()
                if isinstance(drv, Push2Driver):
                    drv.restore_palette()
            if hasattr(drv, "clock"):
                drv.clock.stop()
            port.close(None)
        if display is not None:
            display.stop()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pxr", description="Passerelle Resolume Arena <-> Push 2 / X-Touch")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--mapping", default="mapping.yaml")
    ap.add_argument("--dry-run", action="store_true", help="sans Arena : composition de démo simulée")
    ap.add_argument("--log-level", default=None)
    a = ap.parse_args(argv)
    cfg = load_config(a.config if Path(a.config).exists() else None)
    mapping = load_mapping(a.mapping if Path(a.mapping).exists() else None)
    logging.basicConfig(level=(a.log_level or cfg.log_level).upper(),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", stream=sys.stdout)
    try:
        asyncio.run(run(cfg, mapping, a.dry_run))
    except KeyboardInterrupt:
        pass
    return 0
