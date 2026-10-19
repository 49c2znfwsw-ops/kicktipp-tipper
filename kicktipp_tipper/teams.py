"""Vereinsnamen aus verschiedenen Quellen einander zuordnen ("Bayern München" vs. "FC Bayern München" vs. "Bayern Munich")."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Schreibweisen, die sich nicht über Ähnlichkeit finden lassen
ALIASES = {
    "munich": "munchen",
    "cologne": "koln",
    "gladbach": "monchengladbach",
    "bmg": "monchengladbach",
    "hsv": "hamburg",
    "bvb": "dortmund",
    "fcb": "bayern",
    "s04": "schalke",
    "rbl": "leipzig",
    "b04": "leverkusen",
    "sge": "frankfurt",
    "eintracht": "frankfurt",
    "hertha": "hertha berlin",
    "union": "union berlin",
    "fcu": "union berlin",
    "tsg": "hoffenheim",
    "fca": "augsburg",
    "svw": "bremen",
    "werder": "bremen",
    "vfb": "stuttgart",
    "scf": "freiburg",
    "m05": "mainz",
    "wob": "wolfsburg",
    "fch": "heidenheim",
    "koe": "koln",
}
_STOP = {"fc", "sv", "vfl", "vfb", "tsg", "sc", "bv", "rb", "1", "1899", "04", "05", "09", "1846", "1848",
         "1860", "fsv", "borussia", "bayer", "eintracht", "e", "v", "ev", "ac", "afc", "cf", "club", "de",
         "hamburger", "spvgg", "ssv", "ksc", "tsv", "sportclub", "bor", "07"}


def normalize(name: str) -> str:
    s = unicodedata.normalize("NFKD", name.lower().replace("ß", "ss"))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    words = [ALIASES.get(w, w) for w in s.split()]
    core = [w for w in " ".join(words).split() if w not in _STOP]
    return " ".join(core) or " ".join(words)


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if na == nb:
        return 1.0
    ta, tb = set(na.split()), set(nb.split())
    if ta and tb and (ta <= tb or tb <= ta):
        return 0.95
    return SequenceMatcher(None, na, nb).ratio()


def best_match(name: str, candidates: list[str], threshold: float = 0.72) -> str | None:
    best, score = None, 0.0
    for c in candidates:
        s = similarity(name, c)
        if s > score:
            best, score = c, s
    return best if score >= threshold else None
