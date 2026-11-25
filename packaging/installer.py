"""Installateur de Push X-Touch Resolume (compilé en un seul .exe par packaging/build.py, qui y embarque l'application).

Installe pour l'utilisateur courant, sans droits administrateur :
  programme   %LOCALAPPDATA%\\Programs\\push-xtouch-resolume   (remplacé à chaque mise à jour)
  réglages    %APPDATA%\\push-xtouch-resolume                  (jamais écrasés ; créés au 1er lancement)
  menu Démarrer, bureau (option), démarrage de Windows (option), entrée dans « Applications installées ».

Usage : Setup.exe                          interface
        Setup.exe --silent [--target DIR] [--no-desktop] [--autostart] [--no-launch]
        uninstall.exe --uninstall [--silent] [--remove-settings]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

APP_ID = "push-xtouch-resolume"
APP_NAME = "Push X-Touch Resolume"
EXE = "PushXTouchResolume.exe"
VERSION = "0.1.0"                     # = pxr.__version__ (build.py vérifie)
UNINSTALL_EXE = "uninstall.exe"
REG_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}"
NO_WINDOW = 0x08000000
DETACHED = 0x00000008


def payload_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent / "build" / "stage"))
    return base / "payload"


def default_target() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Programs" / APP_ID


def settings_dir() -> Path:
    return Path(os.environ["APPDATA"]) / APP_ID


def start_menu_dir() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def startup_dir() -> Path:
    return start_menu_dir() / "Startup"


def desktop_dir() -> Path:
    r = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    return Path(r.stdout.strip() or Path.home() / "Desktop")


def shortcut_paths() -> dict[str, Path]:
    return {"menu": start_menu_dir() / f"{APP_NAME}.lnk",
            "desktop": desktop_dir() / f"{APP_NAME}.lnk",
            "startup": startup_dir() / f"{APP_NAME}.lnk"}


# ─── registre (HKCU, sans droits administrateur) ───────────────────────────

def installed_info() -> dict | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
            return {n: winreg.QueryValueEx(k, n)[0] for n in ("DisplayVersion", "InstallLocation")}
    except OSError:
        return None


def write_registry(target: Path) -> None:
    import winreg
    size_kb = sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) // 1024
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
        for name, val in (("DisplayName", APP_NAME), ("DisplayVersion", VERSION), ("Publisher", APP_ID),
                          ("InstallLocation", str(target)), ("DisplayIcon", str(target / EXE)),
                          ("UninstallString", f'"{target / UNINSTALL_EXE}" --uninstall'),
                          ("QuietUninstallString", f'"{target / UNINSTALL_EXE}" --uninstall --silent')):
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
        for name, val in (("NoModify", 1), ("NoRepair", 1), ("EstimatedSize", size_kb)):
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, val)


def delete_registry() -> None:
    import winreg
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REG_KEY)
    except OSError:
        pass


# ─── processus et raccourcis ───────────────────────────────────────────────

def app_running() -> bool:
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {EXE}", "/NH"], capture_output=True, text=True,
                       creationflags=NO_WINDOW)
    return EXE.lower() in r.stdout.lower()


QUIT_EVENT = f"Local\\{APP_ID}-quit"     # = pxr.tray.QUIT_EVENT


def signal_quit() -> bool:
    """Demande à l'application de quitter proprement (elle éteint les LED et remet la palette du Push)."""
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.OpenEventW.restype = ctypes.c_void_p
    k32.SetEvent.argtypes = k32.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k32.OpenEventW(0x0002, False, QUIT_EVENT)        # EVENT_MODIFY_STATE
    if not h:
        return False
    ok = bool(k32.SetEvent(h))
    k32.CloseHandle(h)
    return ok


def stop_app(log) -> None:
    if not app_running():
        return
    log(f"Arrêt de {EXE}…")
    if signal_quit():
        for _ in range(100):                               # jusqu'à 10 s pour s'arrêter proprement
            if not app_running():
                log("Arrêtée proprement")
                return
            time.sleep(0.1)
    log("Arrêt forcé")
    subprocess.run(["taskkill", "/IM", EXE, "/F"], capture_output=True, creationflags=NO_WINDOW)
    for _ in range(50):
        if not app_running():
            break
        time.sleep(0.1)


def make_shortcut(lnk: Path, target: Path, workdir: Path, icon: Path) -> None:
    lnk.parent.mkdir(parents=True, exist_ok=True)
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);"
          "$s.TargetPath=$env:TGT;$s.WorkingDirectory=$env:WD;$s.IconLocation=$env:ICO;$s.Save()")
    env = dict(os.environ, LNK=str(lnk), TGT=str(target), WD=str(workdir), ICO=f"{icon},0")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, capture_output=True, text=True,
                       creationflags=NO_WINDOW)
    if r.returncode or not lnk.exists():
        raise OSError(f"raccourci {lnk.name} : {r.stderr.strip()[:200]}")


# ─── installation / désinstallation ────────────────────────────────────────

def install(target: Path, desktop: bool, autostart: bool, log) -> None:
    src = payload_dir()
    if not (src / EXE).exists():
        raise FileNotFoundError(f"application absente de l'installateur ({src})")
    if target.exists() and any(target.iterdir()) and not is_app_dir(target):
        raise OSError(f"{target} n'est pas vide et ne contient pas {APP_NAME} : choisissez un dossier vide")
    stop_app(log)
    if target.exists():
        log(f"Remplacement de l'ancienne version dans {target}")
        for child in target.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    log(f"Copie des fichiers vers {target}")
    shutil.copytree(src, target, dirs_exist_ok=True)
    if getattr(sys, "frozen", False):
        shutil.copy2(sys.executable, target / UNINSTALL_EXE)
    exe = target / EXE
    links = shortcut_paths()
    make_shortcut(links["menu"], exe, target, exe)
    log("Raccourci du menu Démarrer créé")
    for key, wanted in (("desktop", desktop), ("startup", autostart)):
        if wanted:
            make_shortcut(links[key], exe, target, exe)
            log("Raccourci " + ("sur le bureau" if key == "desktop" else "au démarrage de Windows") + " créé")
        elif links[key].exists():
            links[key].unlink()
    write_registry(target)
    log(f"Installé : {APP_NAME} {VERSION}")
    log(f"Réglages : {settings_dir()} (créés au premier lancement, conservés ensuite)")


def is_app_dir(target: Path) -> bool:
    """Garde-fou : on ne vide jamais un dossier qui ne contient pas l'application."""
    return (target / EXE).exists() or (target / UNINSTALL_EXE).exists()


def uninstall(target: Path, remove_settings: bool, log) -> None:
    if target.exists() and not is_app_dir(target):
        raise OSError(f"{target} ne contient pas {APP_NAME} : rien n'est supprimé")
    stop_app(log)
    for lnk in shortcut_paths().values():
        if lnk.exists():
            lnk.unlink()
    log("Raccourcis supprimés")
    delete_registry()
    me = Path(sys.executable).resolve() if getattr(sys, "frozen", False) else None
    if target.exists():
        for child in target.iterdir():
            if me is not None and child.resolve() == me:
                continue                    # l'exe en cours : supprimé après la sortie (voir plus bas)
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        log(f"Fichiers supprimés de {target}")
    if remove_settings and settings_dir().exists():
        shutil.rmtree(settings_dir())
        log(f"Réglages supprimés ({settings_dir()})")
    elif settings_dir().exists():
        log(f"Réglages conservés : {settings_dir()}")
    if target.exists():                     # dossier vidé, sauf uninstall.exe verrouillé tant qu'il tourne
        subprocess.Popen(f'cmd /c ping 127.0.0.1 -n 3 >nul & rmdir /s /q "{target}"',
                         creationflags=NO_WINDOW | DETACHED, close_fds=True)
    log("Désinstallation terminée")


def launch(target: Path) -> None:
    subprocess.Popen([str(target / EXE)], cwd=str(target), creationflags=DETACHED, close_fds=True)


# ─── interface ─────────────────────────────────────────────────────────────

def gui(uninstall_mode: bool) -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    info = installed_info()
    root = tk.Tk()
    root.title(f"{APP_NAME} {VERSION} — " + ("désinstallation" if uninstall_mode else "installation"))
    root.resizable(False, False)
    ico = payload_dir() / "app.ico"
    if ico.exists():
        try:
            root.iconbitmap(str(ico))
        except tk.TclError:
            pass
    frm = ttk.Frame(root, padding=16)
    frm.grid()
    ttk.Label(frm, text=APP_NAME, font=("Segoe UI", 16, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
    ttk.Label(frm, text="Passerelle Resolume Arena ↔ Ableton Push 2 / Behringer X-Touch").grid(
        row=1, column=0, columnspan=3, sticky="w")
    state = f"Version installée : {info['DisplayVersion']}" if info else "Pas encore installé"
    ttk.Label(frm, text=f"Version de l'installateur : {VERSION}   ·   {state}").grid(
        row=2, column=0, columnspan=3, sticky="w", pady=(4, 12))

    target = tk.StringVar(value=(info or {}).get("InstallLocation") or str(default_target()))
    desktop, autostart, run_after = tk.BooleanVar(value=True), tk.BooleanVar(value=False), tk.BooleanVar(value=True)
    remove_settings = tk.BooleanVar(value=False)
    if uninstall_mode:
        ttk.Label(frm, text=f"Dossier : {target.get()}").grid(row=3, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(frm, text=f"Supprimer aussi mes réglages ({settings_dir()})", variable=remove_settings).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=4)
    else:
        ttk.Label(frm, text="Dossier :").grid(row=3, column=0, sticky="w")
        ttk.Entry(frm, textvariable=target, width=60).grid(row=3, column=1, sticky="we", padx=6)
        ttk.Button(frm, text="Parcourir…", command=lambda: target.set(
            filedialog.askdirectory(initialdir=target.get()) or target.get())).grid(row=3, column=2)
        for r, (txt, var) in enumerate((("Raccourci sur le bureau", desktop),
                                        ("Lancer au démarrage de Windows", autostart),
                                        ("Lancer à la fin de l'installation", run_after)), start=4):
            ttk.Checkbutton(frm, text=txt, variable=var).grid(row=r, column=0, columnspan=3, sticky="w")
        ttk.Label(frm, text="Rappel : dans Arena > Preferences > MIDI, décocher « Ableton Push 2 » et la X-Touch.",
                  foreground="#b05a00").grid(row=7, column=0, columnspan=3, sticky="w", pady=(8, 0))

    out = tk.Text(frm, height=9, width=86, state="disabled", font=("Consolas", 9))
    out.grid(row=8, column=0, columnspan=3, pady=10)

    def log(msg: str) -> None:
        out.configure(state="normal")
        out.insert("end", msg + "\n")
        out.see("end")
        out.configure(state="disabled")
        root.update()

    btns = ttk.Frame(frm)
    btns.grid(row=9, column=0, columnspan=3, sticky="e")

    def confirm_running() -> bool:
        return not app_running() or messagebox.askokcancel(
            APP_NAME, "La passerelle est en cours d'exécution. Elle va être arrêtée. Continuer ?")

    def do_install() -> None:
        if not confirm_running():
            return
        go.configure(state="disabled")
        try:
            t = Path(target.get())
            install(t, desktop.get(), autostart.get(), log)
            if run_after.get():
                launch(t)
                log("Lancée : icône près de l'horloge")
        except Exception as e:
            log(f"ÉCHEC : {e}")
            messagebox.showerror(APP_NAME, f"L'installation a échoué :\n{e}")
            go.configure(state="normal")
            return
        go.configure(text="Terminé", command=root.destroy, state="normal")

    def do_uninstall() -> None:
        if not messagebox.askyesno(APP_NAME, f"Désinstaller {APP_NAME} ?") or not confirm_running():
            return
        go.configure(state="disabled")
        try:
            uninstall(Path(target.get()), remove_settings.get(), log)
        except Exception as e:
            log(f"ÉCHEC : {e}")
            go.configure(state="normal")
            return
        go.configure(text="Fermer", command=root.destroy, state="normal")

    go = ttk.Button(btns, text="Désinstaller" if uninstall_mode else ("Mettre à jour" if info else "Installer"),
                    command=do_uninstall if uninstall_mode else do_install)
    go.grid(row=0, column=1, padx=4)
    ttk.Button(btns, text="Annuler", command=root.destroy).grid(row=0, column=0, padx=4)
    root.mainloop()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="Setup")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--silent", action="store_true")
    ap.add_argument("--target")
    ap.add_argument("--no-desktop", action="store_true")
    ap.add_argument("--autostart", action="store_true")
    ap.add_argument("--no-launch", action="store_true")
    ap.add_argument("--remove-settings", action="store_true")
    a = ap.parse_args(argv)
    if not a.silent:
        return gui(a.uninstall)
    lines: list[str] = []
    info = installed_info()
    target = Path(a.target or (info or {}).get("InstallLocation") or default_target())
    try:
        if a.uninstall:
            uninstall(target, a.remove_settings, lines.append)
        else:
            install(target, not a.no_desktop, a.autostart, lines.append)
            if not a.no_launch:
                launch(target)
        code = 0
    except Exception as e:
        lines.append(f"ÉCHEC : {e}")
        code = 1
    log_path = Path(os.environ.get("TEMP", ".")) / f"{APP_ID}_setup.log"
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())
