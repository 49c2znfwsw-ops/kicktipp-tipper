"""Tipp-Empfehlungen und Freigabe für Kicktipp-Tipprunden."""
import re
from pathlib import Path


def _version() -> str:
    # Beim Start aus dem Programmordner gilt die Version aus pyproject.toml (wird vom Update mitgeliefert)
    try:
        text = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r'^version\s*=\s*"([\d.]+)"', text, re.M)
        if m:
            return m.group(1)
    except OSError:
        pass
    try:
        from importlib.metadata import version

        return version("kicktipp-tipper")
    except Exception:
        return "0"


__version__ = _version()
