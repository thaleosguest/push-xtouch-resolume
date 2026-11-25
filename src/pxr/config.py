"""Chargement et validation de config.yaml (matériel, réseau) et mapping.yaml (assignations)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

RGB = tuple[int, int, int]


# ─── config.yaml ────────────────────────────────────────────────────────────

class ArenaCfg(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8080


class PortCfg(BaseModel):
    input: str            # motif regex sur le nom du port d'entrée
    output: str           # motif regex sur le nom du port de sortie


class Push2Cfg(BaseModel):
    enabled: bool = True
    midi: PortCfg = PortCfg(input=r"^Ableton Push 2 \d+$", output=r"^Ableton Push 2 \d+$")
    display: bool = True
    display_fps: float = 20


class McuCfg(BaseModel):
    name: str = "xtouch"
    enabled: bool = True
    transport: Literal["usb", "rtp"] = "usb"   # informatif : en RTP, seul le motif de port change
    midi: PortCfg = PortCfg(input=r"^X-Touch \d+$", output=r"^X-Touch \d+$")
    sysex_id: int = 0x14                     # 0x14 X-Touch, 0x15 Extender
    has_master: bool = True                  # l'Extender n'a pas de fader master
    strip_offset: int = 0                    # 8 pour un Extender placé à droite de la X-Touch


class Config(BaseModel):
    arena: ArenaCfg = ArenaCfg()
    push2: Push2Cfg = Push2Cfg()
    mcu: list[McuCfg] = Field(default_factory=lambda: [McuCfg()])
    refresh_hz: float = 40                   # trames de rendu LED / faders par seconde
    port_poll_s: float = 2.0                 # intervalle de recherche des contrôleurs débranchés
    log_level: str = "INFO"


# ─── mapping.yaml ───────────────────────────────────────────────────────────

class Push2Map(BaseModel):
    max_layer_rows: int = 7                  # rangées de pads pour les couches (bas -> haut)
    column_row: bool = True                  # une rangée de déclenchement de colonnes au-dessus des couches
    bank_prev: str = "left"
    bank_next: str = "right"
    layers_up: str = "up"
    layers_down: str = "down"
    deck_buttons: list[str] = Field(default_factory=lambda: [f"scene{i}" for i in range(1, 9)])
    page_buttons: dict[str, str] = Field(default_factory=lambda: {"layers": "mix", "fx": "device"})
    default_page: Literal["layers", "fx"] = "layers"
    layer_param: str = "video.opacity"       # chemin dans l'objet couche Arena
    master_slot: int | None = None           # encodeur (0-7) doublant le master composition (page Couches) ;
                                             # par défaut non : l'encodeur master du Push s'en charge (retour d'essai)
    fx_prev: str = "page_left"
    fx_next: str = "page_right"
    encoder_step: float = 0.004              # par cran (≈ 210 crans par tour)
    play_button: str | None = "play"
    tap_button: str | None = "tap_tempo"
    playing_animation: Literal["none", "pulse", "blink"] = "pulse"
    touchstrip_crossfader: bool = False


class McuMap(BaseModel):
    fader_param: str = "video.opacity"
    encoder_param: str = "transition.duration"
    encoder_step: float = 0.01
    select_button: bool = True               # Select = sélection de la couche dans Arena
    solo_param: str | None = "solo"
    mute_param: str | None = "bypassed"
    bank_prev: str = "bank_left"
    bank_next: str = "bank_right"
    channel_prev: str = "channel_left"
    channel_next: str = "channel_right"
    play_button: str | None = "play"
    stop_button: str | None = "stop"
    tap_button: str | None = "cycle"


class ColorsMap(BaseModel):
    # colorid Arena -> couleur de LED. Les couleurs d'Arena (spike 5, 25/11/2026) sont ternes et se distinguent
    # surtout par la clarté, invisible sur des LED : on choisit une teinte franche et distincte par colorid.
    # Absent ("1" = gris = pas de couleur) -> couleur de la couche, puis couleur de repli de la couche.
    colorid_led: dict[str, RGB] = Field(default_factory=lambda: {
        "2": (255, 40, 60),     # Arena : brun rosé
        "3": (60, 255, 190),    # Arena : vert menthe
        "4": (0, 200, 30),      # Arena : vert d'eau foncé
        "5": (30, 70, 255),     # Arena : bleu ardoise
        "6": (190, 40, 255),    # Arena : mauve
    })
    layer_fallback: list[RGB] = Field(default_factory=lambda: [
        (255, 70, 0), (0, 190, 255), (255, 220, 0), (255, 80, 170), (170, 255, 0), (255, 255, 255), (255, 170, 110)])
    dim_factor: float = 40 / 255             # « chargé » : 16 % de la puissance (choisi au spike 6)
    column_active: RGB = (255, 255, 255)
    deck_active: RGB = (0, 200, 255)


class Mapping(BaseModel):
    push2: Push2Map = Push2Map()
    mcu: McuMap = McuMap()
    colors: ColorsMap = ColorsMap()


def _load_yaml(path: str | Path | None) -> dict:
    if path is None:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def load_config(path: str | Path | None) -> Config:
    return Config.model_validate(_load_yaml(path))


def load_mapping(path: str | Path | None) -> Mapping:
    return Mapping.model_validate(_load_yaml(path))
