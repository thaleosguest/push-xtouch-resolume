"""Miroir local de la composition Arena (source de vérité = Arena).

Construit depuis l'objet composition (message initial du WebSocket ou GET /composition) puis tenu à jour par
les `parameter_update`. Chaque paramètre suivi est indexé par son id pour que le client s'y abonne.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

PLAYING = ("Connected", "Connected & previewing")


@dataclass
class Param:
    id: int
    valuetype: str
    value: Any
    min: float | None = None
    max: float | None = None
    options: list | None = None

    @classmethod
    def from_json(cls, d: dict) -> "Param":
        return cls(d["id"], d.get("valuetype", ""), d.get("value"), d.get("min"), d.get("max"), d.get("options"))

    @property
    def is_range(self) -> bool:
        return self.valuetype == "ParamRange" and self.min is not None and self.max is not None and self.max > self.min

    def norm(self) -> float:
        """Valeur ramenée à 0..1 (ParamRange), 0/1 pour un booléen."""
        if self.is_range:
            return min(1.0, max(0.0, (float(self.value) - self.min) / (self.max - self.min)))
        return 1.0 if self.value is True else 0.0

    def denorm(self, x: float) -> float:
        x = min(1.0, max(0.0, x))
        return self.min + x * (self.max - self.min) if self.is_range else x


@dataclass
class Clip:
    id: int
    layer: int        # index 1..N
    column: int       # index 1..N
    name: Param
    connected: Param
    colorid: Param | None

    @property
    def empty(self) -> bool:
        return self.connected.value == "Empty"

    @property
    def playing(self) -> bool:
        return self.connected.value in PLAYING


@dataclass
class Layer:
    id: int
    index: int
    name: Param
    colorid: Param | None
    selected: Param | None
    bypassed: Param | None
    solo: Param | None
    master: Param | None
    raw: dict
    clips: list[Clip] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return str(self.name.value).replace("#", str(self.index))

    @property
    def playing_clip(self) -> Clip | None:
        return next((c for c in self.clips if c.playing), None)


@dataclass
class Column:
    id: int
    index: int
    name: Param
    connected: Param

    @property
    def display_name(self) -> str:
        return str(self.name.value).replace("#", str(self.index))


@dataclass
class Deck:
    id: int
    index: int
    name: Param
    selected: Param


@dataclass
class Effect:
    id: int
    index: int
    name: str
    bypassed: Param | None
    params: list[tuple[str, Param]]  # paramètres réglables (ParamRange), dans l'ordre d'Arena


def resolve(raw: dict, dotted: str) -> dict | None:
    """Chemin pointé ("video.opacity") dans un objet JSON Arena -> objet paramètre (avec "id"), ou None."""
    cur: Any = raw
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur if isinstance(cur, dict) and "id" in cur else None


class StateModel:
    def __init__(self) -> None:
        self.params: dict[int, Param] = {}
        self.layers: list[Layer] = []
        self.columns: list[Column] = []
        self.decks: list[Deck] = []
        self.effects: list[Effect] = []
        self.master: Param | None = None
        self.tempo: Param | None = None
        self.play_state: Param | None = None
        self.tempo_tap: Param | None = None
        self.crossfader: Param | None = None
        self.name = ""
        self.loaded = False
        self.extra_layer_paths: set[str] = set()   # chemins demandés par le mapping (ex. "transition.duration")
        self.structure_version = 0
        self._listeners: list[Callable[[str, int | None], None]] = []

    # ─── écoute ──────────────────────────────────────────────────────────
    def subscribe(self, fn: Callable[[str, int | None], None]) -> None:
        """fn(kind, param_id) ; kind = "structure" (composition rechargée), "param" (valeur reçue),
        "track" (nouveau paramètre à suivre) ou "connection" (Arena connectée / perdue)."""
        self._listeners.append(fn)

    def _emit(self, kind: str, pid: int | None = None) -> None:
        for fn in self._listeners:
            fn(kind, pid)

    # ─── chargement ──────────────────────────────────────────────────────
    def load(self, comp: dict) -> None:
        self._old, self.params = self.params, {}
        try:
            self._load(comp)
        finally:
            del self._old
        self.loaded = True
        self.structure_version += 1
        log.info("composition chargée name=%r layers=%d columns=%d decks=%d effects=%d params=%d",
                 self.name, len(self.layers), len(self.columns), len(self.decks), len(self.effects), len(self.params))
        self._emit("structure")

    def _keep(self, d: Any) -> Param | None:
        """Objet paramètre JSON -> Param indexé. Un id déjà connu garde son objet (valeur rafraîchie)."""
        if not isinstance(d, dict) or "id" not in d:
            return None
        fresh = Param.from_json(d)
        if fresh.id in self.params:        # déjà vu pendant ce chargement (ex. "master" demandé aussi par le mapping)
            return self.params[fresh.id]
        p = self._old.get(fresh.id)
        if p is None:
            p = fresh
        else:
            p.value, p.min, p.max, p.options, p.valuetype = fresh.value, fresh.min, fresh.max, fresh.options, fresh.valuetype
        self.params[p.id] = p
        return p

    def _load(self, comp: dict) -> None:
        k = self._keep
        self.name = (comp.get("name") or {}).get("value", "")
        self.master = k(comp.get("master"))
        tc = comp.get("tempocontroller") or {}
        self.tempo, self.play_state, self.tempo_tap = k(tc.get("tempo")), k(tc.get("play_state")), k(tc.get("tempo_tap"))
        self.crossfader = k((comp.get("crossfader") or {}).get("phase"))
        self.decks = [Deck(d["id"], i, k(d["name"]), k(d["selected"])) for i, d in enumerate(comp.get("decks", []), 1)]
        self.columns = [Column(c["id"], i, k(c["name"]), k(c["connected"])) for i, c in enumerate(comp.get("columns", []), 1)]
        self.layers = []
        for li, L in enumerate(comp.get("layers", []), 1):
            layer = Layer(L["id"], li, k(L["name"]), k(L.get("colorid")), k(L.get("selected")), k(L.get("bypassed")),
                          k(L.get("solo")), k(L.get("master")), L)
            for path in self.extra_layer_paths:
                k(resolve(L, path))
            for ci, C in enumerate(L.get("clips", []), 1):
                layer.clips.append(Clip(C["id"], li, ci, k(C["name"]), k(C["connected"]), k(C.get("colorid"))))
            self.layers.append(layer)
        self.effects = []
        for ei, E in enumerate((comp.get("video") or {}).get("effects", []), 1):
            params = [(n, k(p)) for n, p in (E.get("params") or {}).items()
                      if isinstance(p, dict) and p.get("valuetype") == "ParamRange"]
            self.effects.append(Effect(E["id"], ei, E.get("display_name") or E.get("name", "?"), k(E.get("bypassed")), params))

    # ─── accès ───────────────────────────────────────────────────────────
    def layer_param(self, layer: Layer, path: str) -> Param | None:
        """Paramètre de couche par chemin. Un paramètre pas encore suivi est ajouté à la volée (événement
        "track" : le client Arena s'y abonne)."""
        d = resolve(layer.raw, path)
        if d is None:
            return None
        p = self.params.get(d["id"])
        if p is None:
            p = self.params[d["id"]] = Param.from_json(d)
            self.extra_layer_paths.add(path)
            self._emit("track", p.id)
        return p

    def track_layer_path(self, path: str) -> None:
        """Demande le suivi d'un paramètre de couche supplémentaire (pris en compte au prochain chargement)."""
        self.extra_layer_paths.add(path)

    def apply(self, pid: int, value: Any) -> bool:
        p = self.params.get(pid)
        if p is None or p.value == value:
            return False
        p.value = value
        self._emit("param", pid)
        return True

    def selected_deck(self) -> Deck | None:
        return next((d for d in self.decks if d.selected.value), None)
