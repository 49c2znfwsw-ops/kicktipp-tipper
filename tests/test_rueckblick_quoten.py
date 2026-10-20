"""Rückblick (Empfehlung vs. eigene Tipps vs. Runde) und Quotenvergleich (Odds API auf Klick)."""
import copy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import test_engine_web as t
import test_mitspieler as tm
from kicktipp_tipper.crowd import QuoteRule
from kicktipp_tipper.rueckblick import points_for, rueckblick
from kicktipp_tipper.scoring import ScoringRules
from kicktipp_tipper.sources import oddsapi
from kicktipp_tipper.sources.kicktipp import parse_tippuebersicht
from kicktipp_tipper.store import Store

RULES = ScoringRules(quote=QuoteRule(3, 10))


def test_points_recomputed_exactly_like_kicktipp():
    """Alle Punkte von Spieltag 6 aus Tipps, Ergebnis und Tipp-Verteilung nachgerechnet = Kicktipp."""
    ov = parse_tippuebersicht(tm.OVERVIEW)
    checked = 0
    for i, m in enumerate(ov.matches):
        tips = [(mb.tips[i], mb.points[i]) for mb in ov.members if mb.tips[i]]
        counts = [0, 0, 0]
        for tip, _ in tips:
            counts[0 if tip[0] > tip[1] else 1 if tip[0] == tip[1] else 2] += 1
        for tip, pts in tips:
            assert points_for(RULES, tip, m.result, counts) == (pts or 0)
            checked += 1
    assert checked == 201


def test_rueckblick_swaps_own_tip_for_recommendation(tmp_path):
    st = Store(str(tmp_path / "r.sqlite"))
    ov = parse_tippuebersicht(tm.OVERVIEW)
    st.save_overview(6, ov, True)
    first = ov.matches[0]  # Frankfurt – Köln 1:0, ich: 2:1 (6 Punkte)
    recs = {first.key: {"tip": [1, 0]}}
    r = rueckblick(st.history(), recs, "1", RULES)
    sp = r["spieltage"][0]
    g = sp["spiele"][0]
    assert g["tipp"] == [2, 1] and g["punkte"] == 6
    assert g["punkte_empfehlung"] == 3 + 4  # Heim-Quote 3 (bleibt bei Tausch Heim gegen Heim) + exaktes Ergebnis
    assert g["abweichung"]
    assert sp["punkte_du"] == 40 and sp["spiele_mit_empfehlung"] == 1 and sp["punkte_du_vergleichbar"] == 6
    assert sp["bester_runde"] == 38 and 15 < sp["schnitt_runde"] < 30


def test_recommendation_saved_and_rueckblick_api(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "FakeKicktipp", tm.HistFake)
    engine, _ = t.make_engine(tmp_path, rules=RULES, anzahl_tipper=25)
    state = engine.refresh()
    recs = engine.store.recommendations()
    assert all(m["key"] in recs for m in state["matches"] if m["open"])
    engine.load_history(pause_s=0)
    client = TestClient(t.create_app(engine, background=False))
    r = client.get("/api/rueckblick").json()
    assert [s["spieltag"] for s in r["spieltage"]] == [1, 2, 3, 4, 5, 6, 7]


ODDS = [
    {"home_team": "VfB Stuttgart", "away_team": "Borussia Monchengladbach", "commence_time": "2026-10-23T18:30:00Z",
     "bookmakers": [
         {"key": "bwin", "title": "Bwin", "markets": [
             {"key": "h2h", "outcomes": [{"name": "VfB Stuttgart", "price": 1.41}, {"name": "Draw", "price": 5.5},
                                         {"name": "Borussia Monchengladbach", "price": 6.25}]},
             {"key": "totals", "outcomes": [{"name": "Over", "price": 1.5, "point": 2.5}, {"name": "Under", "price": 2.6, "point": 2.5}]}]},
         {"key": "pinnacle", "title": "Pinnacle", "markets": [
             {"key": "h2h", "outcomes": [{"name": "VfB Stuttgart", "price": 1.80}, {"name": "Draw", "price": 4.0},
                                         {"name": "Borussia Monchengladbach", "price": 4.4}]}]},
     ]},
]


def test_odds_parse_and_compare():
    from kicktipp_tipper.quoten import compare

    events = oddsapi.parse_detailed(ODDS)
    assert events[0].books[0].bookmaker == "Bwin" and events[0].books[0].over25 == 1.5
    matches = [{"key": "k", "home": "VfB Stuttgart", "away": "Bor. Mönchengladbach", "kickoff": "2026-10-23T20:30:00+02:00",
                "probs": {"heim": 0.68, "remis": 0.17, "gast": 0.15}}]
    row = compare(events, matches)[0]
    assert row["gefunden"] and row["buchmacher"] == 2
    assert sum(row["markt"]) == pytest.approx(1)
    assert row["beste_quoten"][0] == {"quote": 1.80, "buchmacher": "Pinnacle"}
    assert row["bwin"][0] == pytest.approx(0.68, abs=0.01)
    assert row["auffaellig"]  # Markt sieht Stuttgart deutlich schwächer als bwin/Prognose


def test_odds_only_on_click(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(key, sport, session=None):
        calls.append(key)
        return ODDS, {"verbleibend": "498", "verbraucht": "2"}

    monkeypatch.setattr(oddsapi, "fetch_raw", fake_fetch)
    monkeypatch.setattr(t, "FakeKicktipp", tm.HistFake)
    engine, _ = t.make_engine(tmp_path, rules=RULES, anzahl_tipper=25)
    client = TestClient(t.create_app(engine, background=False))
    engine.refresh()
    assert client.post("/api/quoten", json={"abrufen": True}).status_code == 400  # kein Schlüssel
    r = client.post("/api/quoten", json={"schluessel": "geheim123"}).json()
    assert r["schluessel"] and r["abruf"] is None and not calls  # Speichern ruft nichts ab
    engine.refresh()
    engine.refresh()
    assert not calls  # Aktualisierungen nutzen die Odds API nicht
    r = client.post("/api/quoten", json={"abrufen": True}).json()
    assert calls == ["geheim123"] and r["kontingent"]["verbleibend"] == "498"
    assert r["spiele"][0]["gefunden"]
    # Ergebnis bleibt gespeichert, ohne neuen Abruf
    again = client.post("/api/quoten", json={}).json()
    assert len(calls) == 1 and again["abruf"] == r["abruf"]


def test_trend_movement():
    from kicktipp_tipper.quoten import kicktipp_trend, trend

    pts = [("2026-10-20T08:00:00+00:00", [0.50, 0.25, 0.25]),
           ("2026-10-20T09:00:00+00:00", [0.50, 0.25, 0.25]),  # unverändert -> zusammengefasst
           ("2026-10-21T07:00:00+00:00", [0.52, 0.25, 0.23]),
           ("2026-10-21T12:00:00+00:00", [0.56, 0.24, 0.20])]
    tr = trend(pts, "x")
    assert len(tr["punkte"]) == 3
    assert tr["gesamt"][0] == pytest.approx(0.06) and tr["deutlich"]
    assert tr["24h"][0] == pytest.approx(0.06)  # Bezug: Stand von 09:00 am Vortag
    assert tr["letzte_aenderung"] == "2026-10-21T12:00:00+00:00"
    # Kicktipp-Wettquote hat Vorrang vor der Prognose
    snaps = [("2026-10-20T08:00:00+00:00", {"wettquote": [2.0, 3.6, 3.6], "probs": {"heim": 0.4, "remis": 0.3, "gast": 0.3}}),
             ("2026-10-20T10:00:00+00:00", {"wettquote": [1.8, 3.8, 4.2], "probs": {"heim": 0.4, "remis": 0.3, "gast": 0.3}})]
    kt = kicktipp_trend(snaps)
    assert kt["quelle"].startswith("Kicktipp") and kt["gesamt"][0] > 0.03
    assert kicktipp_trend([(a, {"probs": m["probs"]}) for a, m in snaps])["quelle"] == "Prognose"


def test_market_trend_between_fetches(tmp_path, monkeypatch):
    import copy

    data = [copy.deepcopy(ODDS)]

    def fake_fetch(key, sport, session=None):
        return data[-1], {"verbleibend": "498"}

    monkeypatch.setattr(oddsapi, "fetch_raw", fake_fetch)
    monkeypatch.setattr(t, "FakeKicktipp", tm.HistFake)
    engine, _ = t.make_engine(tmp_path, rules=RULES, anzahl_tipper=25)
    client = TestClient(t.create_app(engine, background=False))
    engine.refresh()
    engine.refresh()
    client.post("/api/quoten", json={"schluessel": "k"})
    r = client.post("/api/quoten", json={}).json()
    assert r["spiele"] and r["spiele"][0]["trend_kicktipp"] is not None  # ohne Abruf, aus den Ständen
    first = client.post("/api/quoten", json={"abrufen": True}).json()["spiele"][0]
    assert first["trend_markt"] is None
    moved = copy.deepcopy(ODDS)
    moved[0]["bookmakers"][1]["markets"][0]["outcomes"][0]["price"] = 1.55
    data.append(moved)
    second = client.post("/api/quoten", json={"abrufen": True}).json()["spiele"][0]
    assert second["trend_markt"]["gesamt"][0] > 0


def test_odds_archive_keeps_every_fetch(tmp_path, monkeypatch):
    future = copy.deepcopy(ODDS)[0]
    future.update({"id": "evt-spieltag-9", "home_team": "FC Bayern München", "away_team": "1. FC Köln",
                   "commence_time": "2026-11-07T14:30:00Z"})
    monkeypatch.setattr(oddsapi, "fetch_raw", lambda key, sport, session=None: (ODDS + [future], {"verbleibend": "498"}))
    monkeypatch.setattr(t, "FakeKicktipp", tm.HistFake)
    engine, _ = t.make_engine(tmp_path, rules=RULES, anzahl_tipper=25)
    client = TestClient(t.create_app(engine, background=False))
    engine.refresh()
    client.post("/api/quoten", json={"schluessel": "k"})
    r = client.post("/api/quoten", json={"abrufen": True}).json()
    # auch das Spiel eines späteren Spieltags landet im Archiv
    assert r["archiv"]["spiele"] == 2 and r["archiv"]["buchmacher"] == 2 and r["archiv"]["quoten"] == 4
    csv_text = client.get("/api/quoten/archiv.csv").text
    assert "buchmacher" in csv_text.splitlines()[0] and "FC Bayern München" in csv_text and "1,41" in csv_text
