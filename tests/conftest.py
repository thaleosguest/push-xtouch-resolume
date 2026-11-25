import mido
import pytest

from pxr.config import Mapping
from pxr.drivers.mcu import McuDriver
from pxr.drivers.push2 import Push2Driver
from pxr.engine import Engine, McuUnit
from pxr.fake_arena import FakeArena
from pxr.state import StateModel


class Wire:
    """Faux port MIDI : garde les messages envoyés."""

    def __init__(self):
        self.sent: list[mido.Message] = []

    def __call__(self, msg):
        self.sent.append(msg)

    def take(self):
        out, self.sent = self.sent, []
        return out


class Rig:
    def __init__(self, mapping: Mapping | None = None):
        self.state = StateModel()
        self.arena = FakeArena(self.state)
        self.push_wire, self.mcu_wire = Wire(), Wire()
        self.events = []
        self.push = Push2Driver(self.push_wire, self.events.append)
        self.mcu = McuDriver("xtouch", self.mcu_wire, self.events.append)
        self.clock = 1000.0
        self.mcu.now = lambda: self.clock
        self.engine = Engine(self.state, self.arena, mapping or Mapping(), push=self.push, mcus=[McuUnit(self.mcu)])
        self.state.subscribe(lambda kind, pid: self.engine.on_structure() if kind == "structure" else None)
        self.arena.load()

    def feed(self, driver, msg):
        """Message MIDI entrant -> événements -> moteur -> actions envoyées."""
        for ev in driver.parse(msg):
            self.engine.handle(ev)
        self.engine.flush_actions()

    def frame(self):
        self.engine.render()
        self.push.flush()
        self.mcu.flush()


@pytest.fixture
def rig():
    return Rig()
