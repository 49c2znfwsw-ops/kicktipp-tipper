"""Quoten mehrerer Buchmacher über The Odds API (optional, Schlüssel nötig): https://the-odds-api.com"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median

import requests

from ..model import devig

API = "https://api.the-odds-api.com/v4"


@dataclass
class MarketOdds:
    home: str
    away: str
    kickoff: datetime
    p_home: float
    p_draw: float
    p_away: float
    p_over25: float | None
    bookmakers: int


def parse_events(data: list[dict]) -> list[MarketOdds]:
    """Je Buchmacher Marge entfernen, dann Median über alle Buchmacher."""
    out = []
    for ev in data:
        home, away = ev["home_team"], ev["away_team"]
        h2h, over = [], []
        for bm in ev.get("bookmakers", []):
            for mk in bm.get("markets", []):
                prices = {o["name"]: o["price"] for o in mk.get("outcomes", [])}
                if mk["key"] == "h2h" and {home, away, "Draw"} <= prices.keys():
                    h2h.append(devig([prices[home], prices["Draw"], prices[away]]))
                elif mk["key"] == "totals":
                    pts = {o.get("point") for o in mk.get("outcomes", [])}
                    if pts == {2.5} and {"Over", "Under"} <= prices.keys():
                        over.append(devig([prices["Over"], prices["Under"]])[0])
        if not h2h:
            continue
        ph, pd, pa = (median(x[i] for x in h2h) for i in range(3))
        s = ph + pd + pa
        out.append(
            MarketOdds(
                home=home,
                away=away,
                kickoff=datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")),
                p_home=ph / s,
                p_draw=pd / s,
                p_away=pa / s,
                p_over25=median(over) if over else None,
                bookmakers=len(h2h),
            )
        )
    return out


def fetch_odds(api_key: str, sport: str, session: requests.Session | None = None) -> list[MarketOdds]:
    s = session or requests.Session()
    r = s.get(
        f"{API}/sports/{sport}/odds",
        params={"apiKey": api_key, "regions": "eu", "markets": "h2h,totals", "oddsFormat": "decimal"},
        timeout=30,
    )
    r.raise_for_status()
    return parse_events(r.json())
