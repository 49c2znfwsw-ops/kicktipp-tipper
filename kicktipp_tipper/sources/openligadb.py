"""Ergebnisse von OpenLigaDB (frei, ohne Schlüssel): https://api.openligadb.de"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from ..model import Result

API = "https://api.openligadb.de"


def _final_score(match: dict) -> tuple[int, int] | None:
    results = match.get("matchResults") or []
    final = next((r for r in results if r.get("resultTypeID") == 2), None)
    final = final or max(results, key=lambda r: r.get("resultOrderID", 0), default=None)
    if not final:
        return None
    return int(final["pointsTeam1"]), int(final["pointsTeam2"])


def parse_matches(data: list[dict]) -> list[Result]:
    out = []
    for m in data:
        if not m.get("matchIsFinished"):
            continue
        score = _final_score(m)
        if score is None:
            continue
        date = datetime.fromisoformat(m.get("matchDateTimeUTC") or m["matchDateTime"])
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        out.append(Result(m["team1"]["teamName"], m["team2"]["teamName"], score[0], score[1], date))
    return out


def fetch_results(league: str, season: int, session: requests.Session | None = None) -> list[Result]:
    s = session or requests.Session()
    r = s.get(f"{API}/getmatchdata/{league}/{season}", timeout=30)
    r.raise_for_status()
    return parse_matches(r.json())
