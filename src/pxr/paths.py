"""Emplacements de l'application installée.

- Programme : dossier de l'exe (installé dans %LOCALAPPDATA%\\Programs\\push-xtouch-resolume), avec `defaults/`
  (config.yaml et mapping.yaml d'origine).
- Réglages et journaux de l'utilisateur : %APPDATA%\\push-xtouch-resolume (gardés lors des mises à jour).
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_ID = "push-xtouch-resolume"
APP_NAME = "Push X-Touch Resolume"
CONFIG_FILES = ("config.yaml", "mapping.yaml")


def program_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]          # dépôt : src/pxr/paths.py -> racine


def defaults_dir() -> Path:
    d = program_dir() / "defaults"
    return d if d.is_dir() else program_dir()           # en développement : config.yaml à la racine du dépôt


def user_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / APP_ID
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_dir() -> Path:
    d = user_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_user_config() -> list[str]:
    """Copie config.yaml / mapping.yaml d'origine dans le dossier de l'utilisateur s'ils n'y sont pas encore.
    Renvoie la liste des fichiers créés (jamais d'écrasement)."""
    created = []
    for name in CONFIG_FILES:
        dst = user_dir() / name
        src = defaults_dir() / name
        if not dst.exists() and src.exists():
            shutil.copy2(src, dst)
            created.append(name)
    return created
