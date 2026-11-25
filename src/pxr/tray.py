"""Application installée : la passerelle tourne en arrière-plan, une icône près de l'horloge montre son état.

Pastille verte = Arena et tous les contrôleurs connectés ; orange = il en manque ; rouge = Arena injoignable.
Réglages : %APPDATA%\\push-xtouch-resolume\\config.yaml et mapping.yaml (« Redémarrer » les relit).
Journal : %APPDATA%\\push-xtouch-resolume\\logs\\pxr.log
"""
from __future__ import annotations

import asyncio
import ctypes
import logging
import logging.handlers
import os
import sys
import threading
import time

from . import __version__
from .app import Status, run
from .config import Config, Mapping, load_config, load_mapping
from .icon import make_icon
from .paths import APP_ID, APP_NAME, ensure_user_config, log_dir, user_dir

log = logging.getLogger("pxr.tray")
_mutex = None


def single_instance() -> bool:
    """Une seule passerelle à la fois : deux instances se disputeraient les ports MIDI."""
    global _mutex
    if sys.platform != "win32":
        return True
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, f"Local\\{APP_ID}")
    return ctypes.windll.kernel32.GetLastError() != 183   # ERROR_ALREADY_EXISTS


QUIT_EVENT = f"Local\\{APP_ID}-quit"   # signalé par l'installateur pour un arrêt propre (LED éteintes, palette remise)


def wait_quit_signal(on_quit) -> None:
    """Thread : attend l'événement Windows QUIT_EVENT puis appelle on_quit()."""
    if sys.platform != "win32":
        return
    k32 = ctypes.windll.kernel32
    k32.CreateEventW.restype = ctypes.c_void_p
    k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    h = k32.CreateEventW(None, True, False, QUIT_EVENT)
    if not h:
        return

    def loop() -> None:
        if k32.WaitForSingleObject(h, 0xFFFFFFFF) == 0:
            log.info("arrêt demandé par l'installateur")
            on_quit()

    threading.Thread(target=loop, name="quit-signal", daemon=True).start()


def message_box(text: str, error: bool = False) -> None:
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(0, text, APP_NAME, 0x10 if error else 0x40)
    else:
        print(text)


def setup_logging(level: str) -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    h = logging.handlers.RotatingFileHandler(log_dir() / "pxr.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(h)
    root.setLevel(level.upper())


def load_settings() -> tuple[Config, Mapping]:
    return load_config(user_dir() / "config.yaml"), load_mapping(user_dir() / "mapping.yaml")


class Gateway:
    """La passerelle (boucle asyncio) dans un thread, démarrable / arrêtable depuis le menu."""

    def __init__(self) -> None:
        self.status = Status()
        self.error: str | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None

    def start(self, cfg: Config, mapping: Mapping) -> None:
        self.status, self.error = Status(), None
        ready = threading.Event()

        def target() -> None:
            async def main() -> None:
                self._loop, self._stop = asyncio.get_running_loop(), asyncio.Event()
                ready.set()
                await run(cfg, mapping, stop=self._stop, status=self.status)
            try:
                asyncio.run(main())
            except Exception as e:  # la passerelle s'est arrêtée sur une erreur : visible dans le menu
                log.exception("passerelle arrêtée sur une erreur")
                self.error = f"{type(e).__name__}: {e}"
            finally:
                ready.set()

        self._thread = threading.Thread(target=target, name="gateway", daemon=True)
        self._thread.start()
        ready.wait(5)

    def stop(self, timeout: float = 8.0) -> None:
        if self._thread and self._thread.is_alive() and self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set)
            self._thread.join(timeout)

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())


def overall(gw: Gateway) -> str:
    s = gw.status
    if not gw.alive or not s.arena:
        return "down"
    devices = [v for v in (s.push_midi, s.push_display, *s.mcu.values()) if v is not None]
    return "ok" if all(devices) else "partial"


def _yn(v: bool | None, on: str = "connecté", off: str = "absent") -> str:
    return "désactivé" if v is None else (on if v else off)


def main() -> int:
    if not single_instance():
        message_box(f"{APP_NAME} est déjà lancé (icône près de l'horloge).")
        return 1
    ensure_user_config()
    try:
        cfg, mapping = load_settings()
    except Exception as e:
        message_box(f"Erreur dans les réglages ({user_dir()}):\n\n{e}", error=True)
        return 1
    setup_logging(cfg.log_level)
    log.info("%s %s — réglages %s", APP_NAME, __version__, user_dir())
    gw = Gateway()
    gw.start(cfg, mapping)

    import pystray  # importé ici : le module n'est utile qu'à l'application installée
    icons = {k: make_icon(k) for k in ("ok", "partial", "down")}
    running = threading.Event()
    running.set()

    def line_arena(_item) -> str:
        if gw.error:
            return "Erreur : voir le journal"
        s = gw.status
        where = f" ({s.arena_where})" if s.arena_where else ""
        if s.arena:
            return f"Arena{where} : connectée — {s.composition}"
        return f"Arena{where} : OFFLINE" + (f" — {s.arena_error}" if s.arena_error else "")

    def diagnose(icon, _item) -> None:
        def work() -> None:
            from .diag import check
            try:
                c = load_settings()[0]
            except Exception as e:
                message_box(f"Réglages invalides :\n{e}", error=True)
                return
            ok, report = check(c.arena.host, c.arena.port)
            log.info("diagnostic :\n%s", report)
            message_box(report, error=not ok)
        threading.Thread(target=work, name="diagnostic", daemon=True).start()

    def line_push(_item) -> str:
        s = gw.status
        if s.push_midi is None:
            return "Push 2 : désactivé"
        return f"Push 2 : {_yn(s.push_midi)}, écran {_yn(s.push_display, 'OK', 'absent')}"

    def line_mcu(_item) -> str:
        s = gw.status
        return "  ·  ".join(f"{name} : {_yn(ok)}" for name, ok in s.mcu.items()) or "X-Touch : désactivée"

    def open_log(_icon, _item) -> None:
        os.startfile(log_dir() / "pxr.log")

    def open_settings(_icon, _item) -> None:
        os.startfile(user_dir())

    def restart(icon, _item) -> None:
        try:
            new_cfg, new_map = load_settings()
        except Exception as e:
            log.error("réglages invalides, redémarrage annulé : %s", e)
            icon.notify(f"Réglages invalides, rien n'a changé :\n{e}"[:250], APP_NAME)
            return
        setup_logging(new_cfg.log_level)
        log.info("redémarrage demandé")
        gw.stop()
        gw.start(new_cfg, new_map)

    def quit_(icon, _item) -> None:
        running.clear()
        gw.stop()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(f"{APP_NAME} {__version__}", None, enabled=False),
        pystray.MenuItem(line_arena, None, enabled=False),
        pystray.MenuItem(line_push, None, enabled=False),
        pystray.MenuItem(line_mcu, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Diagnostic Arena", diagnose),
        pystray.MenuItem("Ouvrir le journal", open_log),
        pystray.MenuItem("Ouvrir les réglages", open_settings),
        pystray.MenuItem("Redémarrer (relit les réglages)", restart),
        pystray.MenuItem("Quitter", quit_),
    )
    icon = pystray.Icon(APP_ID, icons["down"], APP_NAME, menu)
    wait_quit_signal(lambda: quit_(icon, None))

    def watcher(ic) -> None:
        ic.visible = True
        last = None
        while running.is_set():
            state = overall(gw)
            snapshot = (state, line_arena(None), line_push(None), line_mcu(None))
            if snapshot != last:
                ic.icon = icons[state]
                ic.title = f"{APP_NAME} — {snapshot[1]}"[:127]
                ic.update_menu()
                last = snapshot
            time.sleep(1)

    icon.run(setup=watcher)
    log.info("fin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
