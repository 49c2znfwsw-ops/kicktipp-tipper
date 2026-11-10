"""Review der Spieltage."""
import pytest
from fastapi.testclient import TestClient

import test_engine_web as t
import test_mitspieler as tm
from kicktipp_tipper.crowd import QuoteRule
from kicktipp_tipper.review import _art, _ranks, review
from kicktipp_tipper.scoring import ScoringRules


def test_art_and_ranks():
    assert _art([2, 1], [2, 1]) == "exakt"
    assert _art([2, 1], [3, 2]) == "Tordifferenz"
    assert _art([2, 1], [3, 0]) == "Tendenz"
    assert _art([1, 1], [3, 0]) == "falsch"
    assert _art(None, [3, 0]) == "kein Tipp"
    assert _ranks({"a": 10, "b": 12, "c": 10, "d": 3}) == {"b": 1, "a": 2, "c": 2, "d": 4}


def _small_history():
    # zwei Spieltage, drei Mitspieler (ich = "me"), je ein Spiel
    matches = [{"key": "m1", "spieltag": 1, "home": "A", "away": "B", "result": [2, 0], "round_quote": [3, 10, 10]},
               {"key": "m2", "spieltag": 2, "home": "C", "away": "D", "result": [0, 1], "round_quote": [3, 10, 8]}]
    tips = [{"match_key": "m1", "member_id": "me", "spieltag": 1, "tip": [1, 0], "points": 5},
            {"match_key": "m1", "member_id": "x", "spieltag": 1, "tip": [2, 0], "points": 7},
            {"match_key": "m1", "member_id": "y", "spieltag": 1, "tip": [1, 1], "points": 0},
            {"match_key": "m2", "member_id": "me", "spieltag": 2, "tip": [0, 2], "points": 12},
            {"match_key": "m2", "member_id": "x", "spieltag": 2, "tip": [2, 1], "points": 0},
            {"match_key": "m2", "member_id": "y", "spieltag": 2, "tip": [1, 0], "points": 0}]
    members = [{"spieltag": 1, "member_id": "me", "name": "Ich", "day_points": 5, "total": 5},
               {"spieltag": 1, "member_id": "x", "name": "X", "day_points": 7, "total": 7},
               {"spieltag": 1, "member_id": "y", "name": "Y", "day_points": 0, "total": 0},
               {"spieltag": 2, "member_id": "me", "name": "Ich", "day_points": 12, "total": 17},
               {"spieltag": 2, "member_id": "x", "name": "X", "day_points": 0, "total": 7},
               {"spieltag": 2, "member_id": "y", "name": "Y", "day_points": 0, "total": 0}]
    return {"matches": matches, "tips": tips, "members": members}


def test_review_matchday_and_season():
    r = review(_small_history(), "me")
    assert r["spieltage"] == [1, 2] and r["spieltag"] == 2
    k = r["kopf"]
    assert k["punkte"] == 12 and k["schnitt"] == 0 and k["platz_vorher"] == 2 and k["platz_nachher"] == 1
    assert k["abstand_spitze_vorher"] == -2 and k["abstand_spitze_nachher"] == 0
    g = r["spiele"][0]
    assert g["art"] == "Tendenz" and g["gegentipp"] and g["differenz"] == 12 and g["tore_daneben"] == 1
    x = next(c for c in r["konkurrenz"] if c["member_id"] == "x")
    assert x["spitze"] and x["abstand_vorher"] == -2 and x["abstand_nachher"] == 10
    assert x["entscheidend"][0]["differenz"] == 12
    assert r["verlierer"] == [] and r["gewinner"][0]["key"] == "m2"
    s = r["saison"]
    assert s["punkte"] == 17 and s["schnitt"] == pytest.approx(3.5)
    assert s["nach_art"]["Gegentipp"]["spiele"] == 1 and s["nach_art"]["Mehrheitstipp"]["spiele"] == 1
    assert s["verlierer"] == [] and [g["key"] for g in s["gewinner"]] == ["m2", "m1"]  # 5 gegen Ø 3,5 = +1,5


def test_review_api(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "FakeKicktipp", tm.HistFake)
    engine, _ = t.make_engine(tmp_path, rules=ScoringRules(quote=QuoteRule(3, 10)), anzahl_tipper=25)
    engine.refresh()
    engine.load_history(pause_s=0)
    client = TestClient(t.create_app(engine, background=False))
    r = client.get("/api/review").json()
    assert r["spieltag"] == r["spieltage"][-1] and len(r["spiele"]) == 9
    assert sum(g["punkte"] for g in r["spiele"]) == r["kopf"]["punkte"]  # passt zu Kicktipps Spieltagspunkten
    first = client.get(f"/api/review?spieltag={r['spieltage'][0]}").json()
    assert first["spieltag"] == r["spieltage"][0] and len(first["saison"]["verlauf"]) == len(r["spieltage"])
