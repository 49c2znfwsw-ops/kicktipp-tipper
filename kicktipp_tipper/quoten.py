"""Quotenvergleich: Wettquoten vieler Buchmacher (The Odds API) neben der eigenen Prognose.

Nur auf Klick abgerufen (schont das Kontingent) und bewusst NICHT in die Prognose eingerechnet –
der Reiter dient dazu, größere Abweichungen zu finden.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median

from .model import devig
from .sources import oddsapi
from .teams import best_match

ABWEICHUNG_PP = 0.05  # ab 5 Prozentpunkten wird eine Abweichung hervorgehoben
LEICHT_PP = 0.025  # ab 2,5 Prozentpunkten leicht markiert
BEWEGUNG_PP = 0.03  # ab 3 Prozentpunkten gilt eine Quotenbewegung als deutlich
MAX_ABRUFE = 100  # so viele Odds-API-Abrufe bleiben für die Bewegung gespeichert


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
               "empfehlung": m.get("tip"),
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


def trend(points: list[tuple[str, list[float]]], quelle: str) -> dict | None:
    """Bewegung der Wahrscheinlichkeiten H/U/A über die Zeit (älteste zuerst).

    Gleichbleibende Stände werden zusammengefasst; verglichen wird der letzte Stand mit dem ersten
    und mit dem Stand vor 24 Stunden.
    """
    if not points:
        return None
    kept: list[tuple[str, list[float]]] = []
    for at, p in points:
        if not kept or max(abs(a - b) for a, b in zip(p, kept[-1][1])) > 1e-4:
            kept.append((at, p))
    last_at, last = points[-1]
    if kept[-1][0] != last_at:
        kept.append((last_at, last))  # Linie bis zum letzten Stand weiterführen
    t_last = datetime.fromisoformat(last_at)
    ref = next((p for at, p in reversed(kept) if datetime.fromisoformat(at) <= t_last - timedelta(hours=24)), None)
    first_at, first = kept[0]
    gesamt = [last[i] - first[i] for i in range(3)]
    tag = [last[i] - ref[i] for i in range(3)] if ref else None
    changes = [at for (at, p), (_, q) in zip(kept[1:], kept) if max(abs(a - b) for a, b in zip(p, q)) > 1e-4]
    return {
        "quelle": quelle,
        "seit": first_at,
        "stand": last_at,
        "aktuell": last,
        "gesamt": gesamt,
        "24h": tag,
        "letzte_aenderung": changes[-1] if changes else None,
        "deutlich": max(abs(d) for d in (tag or gesamt)) >= BEWEGUNG_PP,
        "punkte": [[at, *[round(x, 4) for x in p]] for at, p in kept[-200:]],
    }


def kicktipp_trend(snaps: list[tuple[str, dict]]) -> dict | None:
    """Bewegung der Kicktipp-Wettquote (bwin) aus den gespeicherten Ständen – kostet keine Abfrage.

    Stände aus der Zeit, bevor die Wettquote mitgespeichert wurde, gehen über die Prognose ein.
    """
    snaps = sorted(snaps, key=lambda x: datetime.fromisoformat(x[0]))
    odds = [(at, devig(m["wettquote"])) for at, m in snaps if m.get("wettquote") and all(m["wettquote"])]
    if len(odds) >= 2:
        return trend(odds, "Kicktipp-Wettquote (bwin)")
    probs = [(at, [m["probs"]["heim"], m["probs"]["remis"], m["probs"]["gast"]]) for at, m in snaps if m.get("probs")]
    return trend(probs, "Prognose") if probs else None


def run(engine, fetch: bool) -> dict:
    key = engine.odds_api_key()
    saved = engine.store.settings().get("quoten_abruf")
    history = engine.store.settings().get("quoten_verlauf") or []
    if saved and not engine.store.odds_archive_stats()["quoten"]:
        engine.store.archive_odds(saved["zeit"], saved["daten"])  # Abruf aus der Zeit vor dem Archiv übernehmen
    state = engine.state or {}
    matches = state.get("matches", [])
    if fetch:
        if not key:
            raise ValueError("Bitte zuerst den API-Schlüssel eintragen.")
        data, quota = oddsapi.fetch_raw(key, engine.cfg.odds_api_sport)
        saved = {"zeit": datetime.now(timezone.utc).isoformat(timespec="seconds"), "daten": data, "kontingent": quota}
        rows = compare(oddsapi.parse_detailed(data), matches)
        history = (history + [{"zeit": saved["zeit"], "markt": {r["key"]: r["markt"] for r in rows if r.get("markt")}}])[-MAX_ABRUFE:]
        engine.store.save_settings({"quoten_abruf": saved, "quoten_verlauf": history})
        engine.store.archive_odds(saved["zeit"], data)
    result = {"schluessel": bool(key), "abruf": None, "schwelle": ABWEICHUNG_PP, "leicht": LEICHT_PP, "bewegung": BEWEGUNG_PP}
    events = []
    if saved:
        events = oddsapi.parse_detailed(saved["daten"])
        result.update({"abruf": saved["zeit"], "kontingent": saved["kontingent"]})
    rows = compare(events, matches)
    series = engine.store.match_series(state.get("spieltag"))
    for r in rows:
        r["trend_kicktipp"] = kicktipp_trend(series.get(r["key"], []))
        markt = [(h["zeit"], h["markt"][r["key"]]) for h in history if h["markt"].get(r["key"])]
        r["trend_markt"] = trend(markt, "Markt (Odds API)") if len(markt) >= 2 else None
    result["spiele"] = rows
    result["archiv"] = engine.store.odds_archive_stats()
    return result
