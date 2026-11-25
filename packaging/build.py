"""Compilation de l'installateur : dist/PushXTouchResolume_Setup_<version>.exe

    .venv\\Scripts\\python.exe packaging\\build.py

1. icône (dessinée par pxr.icon) ;
2. PyInstaller compile l'application (icône près de l'horloge, sans console) en dossier ;
3. build/stage/payload = ce dossier + defaults/config.yaml, defaults/mapping.yaml + README ;
4. PyInstaller compile packaging/installer.py en UN fichier qui embarque build/stage.
Rien de personnel n'est embarqué : les réglages de l'utilisateur vivent dans %APPDATA%.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "build"
DIST = ROOT / "dist"
APP = "PushXTouchResolume"
sys.path.insert(0, str(ROOT / "src"))


def version() -> str:
    from pxr import __version__
    inst = (ROOT / "packaging" / "installer.py").read_text(encoding="utf-8")
    m = re.search(r'^VERSION = "([^"]+)"', inst, re.M)
    if not m or m.group(1) != __version__:
        raise SystemExit(f"packaging/installer.py : VERSION ({m and m.group(1)}) différente de pxr ({__version__})")
    return __version__


def pyinstaller(args: list[str]) -> None:
    r = subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                        "--workpath", str(WORK / "work"), "--specpath", str(WORK)] + args, cwd=ROOT)
    if r.returncode:
        raise SystemExit(f"ÉCHEC de PyInstaller (code {r.returncode})")


def main() -> int:
    ver = version()
    WORK.mkdir(exist_ok=True)
    icon = WORK / "app.ico"
    from pxr.icon import save_ico
    save_ico(str(icon))

    print(f"== Application {APP} {ver}")
    out = WORK / "out"
    pyinstaller(["--noconsole", "--name", APP, "--icon", str(icon), "--distpath", str(out),
                 "--paths", str(ROOT / "src"),
                 "--collect-data", "pxr",
                 "--collect-all", "libusb_package",
                 "--hidden-import", "mido.backends.rtmidi",
                 "--hidden-import", "pystray._win32",
                 str(ROOT / "packaging" / "launcher.py")])

    print("== Préparation du contenu")
    stage = WORK / "stage"
    shutil.rmtree(stage, ignore_errors=True)
    payload = stage / "payload"
    shutil.copytree(out / APP, payload)
    (payload / "defaults").mkdir()
    for f in ("config.yaml", "mapping.yaml"):
        shutil.copy2(ROOT / f, payload / "defaults" / f)
    shutil.copy2(ROOT / "README.md", payload / "README.md")
    shutil.copy2(icon, payload / "app.ico")

    print("== Installateur")
    DIST.mkdir(exist_ok=True)
    name = f"{APP}_Setup_{ver}"
    pyinstaller(["--onefile", "--windowed", "--name", name, "--icon", str(icon), "--distpath", str(DIST),
                 "--add-data", f"{stage}{';'}.", str(ROOT / "packaging" / "installer.py")])
    exe = DIST / f"{name}.exe"
    print(f"OK : {exe} ({exe.stat().st_size / 1e6:.0f} Mo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
