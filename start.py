"""Startet den Kicktipp-Tipper: Update von GitHub, Einrichtung beim ersten Mal, dann die Oberfläche.

Wird von den Startdateien (Windows/Mac) mit dem System-Python aufgerufen und nutzt deshalb nur
die Standardbibliothek. Lokale Dateien (.env, config.toml, .venv, Datenbank) werden nie überschrieben.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import venv
import zipfile
from pathlib import Path

REPO = "49c2znfwsw-ops/kicktipp-tipper"
BRANCH = "main"
VERSION_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/pyproject.toml"
ZIP_URL = f"https://codeload.github.com/{REPO}/zip/refs/heads/{BRANCH}"

ROOT = Path(__file__).resolve().parent
# Gehören dem Nutzer bzw. dieser Installation, werden beim Update nicht angefasst
PROTECTED_NAMES = {".env", "config.toml", ".venv", ".git"}
PROTECTED_SUFFIXES = (".sqlite", "-diagnose.html")

IMPORT_CHECK = "import numpy, scipy, fastapi, uvicorn, bs4, requests, zoneinfo; zoneinfo.ZoneInfo('Europe/Berlin')"


def parse_version(pyproject: str) -> tuple[int, ...] | None:
    m = re.search(r'^version\s*=\s*"([\d.]+)"', pyproject, re.M)
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def local_version() -> tuple[int, ...] | None:
    return parse_version((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _fetch(url: str, timeout: float = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "kicktipp-tipper-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def is_protected(rel: Path) -> bool:
    return rel.parts[0] in PROTECTED_NAMES or rel.name.endswith(PROTECTED_SUFFIXES)


def apply_update(zip_bytes: bytes, target: Path) -> list[str]:
    """Inhalt des GitHub-Archivs über target kopieren. Gibt die geänderten Dateien zurück."""
    changed = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            parts = Path(info.filename).parts[1:]  # oberster Ordner "kicktipp-tipper-main/"
            if not parts or info.is_dir():
                continue
            rel = Path(*parts)
            if is_protected(rel):
                continue
            data = zf.read(info)
            dest = target / rel
            if dest.exists() and dest.read_bytes() == data:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".neu")
            tmp.write_bytes(data)
            os.replace(tmp, dest)
            if rel.suffix == ".command":
                dest.chmod(0o755)
            changed.append(str(rel))
    return changed


def update() -> bool:
    """True, wenn eine neue Version installiert wurde."""
    if os.environ.get("KICKTIPP_KEIN_UPDATE"):
        return False
    try:
        remote = parse_version(_fetch(VERSION_URL).decode("utf-8"))
        local = local_version()
        if not remote or (local and remote <= local):
            return False
        print(f"Neue Version {'.'.join(map(str, remote))} gefunden, wird geladen ...")
        changed = apply_update(_fetch(ZIP_URL, timeout=60), ROOT)
        print(f"Update installiert ({len(changed)} Dateien).\n")
        return True
    except Exception as e:  # ohne Internet o. Ä. einfach mit der vorhandenen Version weiter
        print(f"Update-Prüfung nicht möglich ({e}). Starte vorhandene Version.\n")
        return False


def venv_python() -> Path:
    return ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(*args) -> None:
    subprocess.run([str(a) for a in args], cwd=ROOT, check=True)


def ensure_installed() -> None:
    version = ".".join(map(str, local_version() or (0,)))
    marker = ROOT / ".venv" / f"installiert-{version}.ok"
    if marker.exists():
        return
    print("Kicktipp-Tipper wird eingerichtet. Das dauert ein bis zwei Minuten ...\n")
    if not venv_python().exists():
        venv.create(ROOT / ".venv", with_pip=True)
    py = venv_python()
    run(py, "-m", "pip", "install", "--upgrade", "pip")
    run(py, "-m", "pip", "install", ".")
    run(py, "-c", IMPORT_CHECK)
    for old in (ROOT / ".venv").glob("installiert-*.ok"):
        old.unlink()
    marker.write_text("ok", encoding="utf-8")
    print("\nEinrichtung abgeschlossen.\n")


def ensure_config() -> None:
    cfg = ROOT / "config.toml"
    if cfg.exists():
        return
    print("Noch keine config.toml – kurze Einrichtung der Tipprunde.")
    runde = input("Name der Tipprunde, wie in der Adresse kicktipp.de/NAME/ : ").strip().strip("/")
    tipper = input("Wie viele Mitglieder tippen ungefähr? [20]: ").strip() or "20"
    text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    text = text.replace('runde = "meine-tipprunde"', f'runde = "{runde}"')
    text = re.sub(r"^anzahl_tipper = \d+", f"anzahl_tipper = {int(tipper)}", text, flags=re.M)
    cfg.write_text(text, encoding="utf-8")
    print("config.toml angelegt.\n")


def main() -> int:
    os.chdir(ROOT)
    if update():
        # Neue start.py übernehmen: sich selbst neu starten
        return subprocess.call([sys.executable, str(ROOT / "start.py"), *sys.argv[1:]], env={**os.environ, "KICKTIPP_KEIN_UPDATE": "1"})
    try:
        ensure_installed()
        ensure_config()
        py = venv_python()
        if not (ROOT / ".env").exists():
            run(py, "-m", "kicktipp_tipper", "einrichten")
        run(py, "-m", "kicktipp_tipper", "serve", "--browser")
    except subprocess.CalledProcessError:
        print("\nEtwas ist schiefgegangen. Bitte einen Screenshot dieses Fensters schicken")
        print("(gern auch weiter oben scrollen, dort steht meist die eigentliche Ursache).")
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
