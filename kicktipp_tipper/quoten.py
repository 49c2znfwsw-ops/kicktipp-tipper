"""Quotenvergleich: Wettquoten vieler Buchmacher (The Odds API) neben der eigenen Prognose.

Nur auf Klick abgerufen (schont das Kontingent) und bewusst NICHT in die Prognose eingerechnet –
der Reiter dient dazu, größere Abweichungen zu finden.
"""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import median

from .model import devig
from .sources import oddsapi
from .teams import best_match

ABWEICHUNG_PP = 0.05  # ab 5 Prozentpunkten wird eine Abweichung hervorgehoben


def _probs(book: oddsapi.BookOdds) -> list[float] | None:
    if book.home and book.draw and book.away:
        return devig([book.home, book.draw, book.away])
    return None


def compare(events: list[oddsapi.EventOdds], matches: list[dict]) -> list[dict]:
    rows = []
    for m in matches:
        kickoff = datetime.fromisoformat(m["kickoff"]) if m.get("kickoff") else None
        ev = next(
            (e for e in events
             if best_match(m["home"], [e.home]) and best_match(m["away"], [e.away])
             and (kickoff is None or abs((e.kickoff - kickoff).total_seconds()) < 36 * 3600)),
            None,
        )
        row = {"key": m["key"], "home": m["home"], "away": m["away"], "kickoff": m.get("kickoff"),
               "prognose": [m["probs"]["heim"], m["probs"]["remis"], m["probs"]["gast"]] if m.get("probs") else None,
               "gefunden": ev is not None}
        if ev:
            books = [b for b in ev.books if _probs(b)]
            probs = [_probs(b) for b in books]
            market = [median(p[i] for p in probs) for i in range(3)] if probs else None
            if market:
                s = sum(market)
                market = [x / s for x in market]
            best = []
            for i, attr in enumerate(("home", "draw", "away")):
                top = max(books, key=lambda b: getattr(b, attr) or 0, default=None)
                best.append({"quote": getattr(top, attr), "buchmacher": top.bookmaker} if top else None)
            overs = [devig([b.over25, b.under25])[0] for b in ev.books if b.over25 and b.under25]
            bwin = next((b for b in books if "bwin" in b.bookmaker.lower()), None)
            row.update({
                "buchmacher": len(books),
                "markt": market,
                "beste_quoten": best,
                "ueber25": median(overs) if overs else None,
                "bwin": _probs(bwin) if bwin else None,
                "spanne": [[min(p[i] for p in probs), max(p[i] for p in probs)] for i in range(3)] if probs else None,
                "einzeln": [{"buchmacher": b.bookmaker, "quoten": [b.home, b.draw, b.away],
                             "ueber_unter": [b.over25, b.under25]} for b in ev.books],
            })
            if market and row["prognose"]:
                diff = [row["prognose"][i] - market[i] for i in range(3)]
                row["abweichung"] = diff
                row["auffaellig"] = max(abs(d) for d in diff) >= ABWEICHUNG_PP
        rows.append(row)
    return rows


def run(engine, fetch: bool) -> dict:
    key = engine.odds_api_key()
    saved = engine.store.settings().get("quoten_abruf")
    if fetch:
        if not key:
            raise ValueError("Bitte zuerst den API-Schlüssel eintragen.")
        data, quota = oddsapi.fetch_raw(key, engine.cfg.odds_api_sport)
        saved = {"zeit": datetime.now(timezone.utc).isoformat(timespec="seconds"), "daten": data, "kontingent": quota}
        engine.store.save_settings({"quoten_abruf": saved})
    matches = (engine.state or {}).get("matches", [])
    result = {"schluessel": bool(key), "abruf": None, "spiele": [], "schwelle": ABWEICHUNG_PP}
    if saved:
        events = oddsapi.parse_detailed(saved["daten"])
        result.update({"abruf": saved["zeit"], "kontingent": saved["kontingent"], "spiele": compare(events, matches)})
    return result
