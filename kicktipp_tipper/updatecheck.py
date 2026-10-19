"""Prüft im laufenden Programm, ob auf GitHub eine neuere Version liegt (für einen Hinweis in der Oberfläche)."""
from __future__ import annotations

import re
import time

import requests

from . import __version__

URL = "https://raw.githubusercontent.com/49c2znfwsw-ops/kicktipp-tipper/main/pyproject.toml"
TTL_S = 30 * 60

_cache: dict = {"at": 0.0, "version": None}


def _parse(v: str | None) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split(".")) if v else (0,)


def latest_version() -> str | None:
    if time.time() - _cache["at"] > TTL_S:
        _cache["at"] = time.time()
        try:
            text = requests.get(f"{URL}?t={int(time.time())}", timeout=5).text
            m = re.search(r'^version\s*=\s*"([\d.]+)"', text, re.M)
            _cache["version"] = m.group(1) if m else None
        except Exception:
            pass  # ohne Internet kein Hinweis
    return _cache["version"]


def newer_version() -> str | None:
    latest = latest_version()
    return latest if latest and _parse(latest) > _parse(__version__) else None
