"""Ports MIDI ouverts par motif regex, avec reconnexion automatique (débranchement, veille, contrôleur éteint).

Les messages entrants arrivent dans le thread de rtmidi : le rappel `on_message` doit être thread-safe
(les drivers le relaient vers la boucle asyncio via `call_soon_threadsafe`).
Sous Windows, un port d'entrée ne s'ouvre que dans UNE application à la fois (spike 4 : Arena tenait le Push).
"""
from __future__ import annotations

import logging
import re
from typing import Callable

import mido

log = logging.getLogger(__name__)


class MidiPort:
    def __init__(self, label: str, in_pattern: str, out_pattern: str,
                 on_message: Callable[[mido.Message], None],
                 on_open: Callable[[], None] | None = None,
                 on_close: Callable[[], None] | None = None) -> None:
        self.label = label
        self.in_rx, self.out_rx = re.compile(in_pattern), re.compile(out_pattern)
        self.on_message, self.on_open, self.on_close = on_message, on_open, on_close
        self.inp: mido.ports.BaseInput | None = None
        self.out: mido.ports.BaseOutput | None = None
        self._last_error: str | None = None

    @property
    def is_open(self) -> bool:
        return self.inp is not None and self.out is not None

    def _match(self, rx: re.Pattern, names: list[str]) -> str | None:
        return next((n for n in names if rx.search(n)), None)

    def poll(self) -> None:
        """À appeler régulièrement : ouvre si absent, ferme si le périphérique a disparu."""
        try:
            ins, outs = mido.get_input_names(), mido.get_output_names()
        except Exception as e:  # énumération qui échoue pendant un rebranchement
            log.debug("énumération MIDI impossible : %s", e)
            return
        if self.is_open:
            if self.inp.name not in ins or self.out.name not in outs:
                self.close("périphérique disparu")
            return
        i, o = self._match(self.in_rx, ins), self._match(self.out_rx, outs)
        if not (i and o):
            self._note_error(f"ports introuvables (entrée={i!r}, sortie={o!r})")
            return
        try:
            self.inp = mido.open_input(i, callback=self.on_message)
            self.out = mido.open_output(o)
        except Exception as e:
            self.close(None)
            self._note_error(f"ouverture impossible ({e}) — port pris par une autre application ?")
            return
        self._last_error = None
        log.info("%s connecté in=%r out=%r", self.label, i, o)
        if self.on_open:
            self.on_open()

    def _note_error(self, msg: str) -> None:
        if msg != self._last_error:  # n'inonde pas le journal pendant qu'on attend le contrôleur
            log.warning("%s : %s", self.label, msg)
            self._last_error = msg

    def send(self, msg: mido.Message) -> None:
        if self.out is None:
            return
        try:
            self.out.send(msg)
        except Exception as e:
            self.close(f"envoi impossible : {e}")

    def close(self, reason: str | None = "arrêt") -> None:
        was_open = self.is_open
        for p in (self.inp, self.out):
            if p is not None:
                try:
                    p.close()
                except Exception:
                    pass
        self.inp = self.out = None
        if was_open:
            if reason:
                log.warning("%s déconnecté : %s", self.label, reason)
            if self.on_close:
                self.on_close()
