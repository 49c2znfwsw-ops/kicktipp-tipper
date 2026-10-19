"""Tippübersicht, Mitspieler-Profile, Prognosen und Verlauf – gegen die echte (anonymisierte) Seite."""
import re
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

import test_engine_web as t
from kicktipp_tipper.crowd import QuoteRule
from kicktipp_tipper.profiles import Profiles
from kicktipp_tipper.scoring import ScoringRules
from kicktipp_tipper.sources.kicktipp import parse_tippuebersicht
from kicktipp_tipper.store import Store

FIX = Path(__file__).parent / "fixtures"
OVERVIEW = (FIX / "tippuebersicht_echt.html").read_text(encoding="utf-8")


def test_parse_tippuebersicht():
    ov = parse_tippuebersicht(OVERVIEW)
    assert ov.index == 6 and len(ov.matches) == 9 and len(ov.members) == 25
    m = ov.matches[0]
    assert (m.home, m.away, m.result, m.round_quote) == ("Eintracht Frankfurt", "1. FC Köln", (1, 0), (3, 7, 9))
    me = ov.members[0]
    assert me.member_id == "1" and me.tips[0] == (2, 1) and me.points[0] == 6 and me.points[1] is None
    assert me.total == 218 and me.matchday_points == 40
    assert ov.members[-1].tips == [None] * 9  # hat nicht getippt


def test_quote_formula_matches_all_real_round_quotes():
    """Aus den Tipps der Runde nachgerechnet ergibt sich exakt die von Kicktipp angezeigte Rundenquote."""
    ov = parse_tippuebersicht(OVERVIEW)
    rule = QuoteRule(3, 10)
    for i, m in enumerate(ov.matches):
        tips = [mb.tips[i] for mb in ov.members if mb.tips[i]]
        counts = [sum(a > b for a, b in tips), sum(a == b for a, b in tips), sum(a < b for a, b in tips)]
        assert tuple(int(rule.points(c, len(tips))) for c in counts) == m.round_quote, m.home


def _history(tmp_path, spieltage=1):
    st = Store(str(tmp_path / "h.sqlite"))
    ov = parse_tippuebersicht(OVERVIEW)
    st.save_overview(6, ov, True)
    return st


def test_profiles(tmp_path):
    P = Profiles(_history(tmp_path).history(), "1")
    prof = {p["member_id"]: p for p in P.as_dict()}
    me = prof["1"]
    assert me["ich"] and me["platz"] == 1 and me["punkte"] == 218 and me["spiele"] == 9
    assert me["trefferquote"] == pytest.approx(6 / 9, abs=0.01)
    assert sum(me["anteile"]) == pytest.approx(1)
    inactive = prof["1025"]
    assert inactive["spiele"] == 0 and inactive["teilnahme"] < 0.2
    assert "1" not in P.others() and len(P.others()) == 24
    assert all(0 <= p["mehrheit"] <= 1 for p in prof.values())


def test_prediction_follows_crowd_and_counts_active_tippers(tmp_path):
    P = Profiles(_history(tmp_path).history(), "1")
    shares, n = P.crowd_forecast("FC Bayern München", "SC Freiburg", (0.8, 0.12, 0.08))
    assert shares[0] > 0.6
    assert 20 < n < 24  # 22 aktive Mitspieler + ich, nicht 25
    p = P.predict("1002", "FC Bayern München", "SC Freiburg", (0.8, 0.12, 0.08))
    assert p.sum() == pytest.approx(1) and np.argmax(p) == 0


class HistFake(t.FakeKicktipp):
    """Liefert die echte Tippabgabe (Spieltag 7) und für Spieltag 1–6 Tippübersichten."""

    def __init__(self):
        super().__init__()
        self.html = (FIX / "tippabgabe_echt.html").read_text(encoding="utf-8")
        self.overview_calls = []

    def get(self, url, params=None, timeout=None):
        if "tippuebersicht" in url and self.logged_in:
            idx = (params or {}).get("spieltagIndex")
            self.overview_calls.append(idx)
            html = OVERVIEW.replace("900000", f"9{idx}0000")  # eigene Spiel-IDs je Spieltag
            # Gesamtpunkte je Spieltag ungefähr hochrechnen, damit ein Verlauf entsteht
            html = re.sub(r'(<td class="gesamtpunkte[^>]*>)\s*(\d+)', lambda m: m.group(1) + str(int(m.group(2)) * idx // 6), html)
            return t.FakeResponse(html)
        return super().get(url, params, timeout)


def test_engine_history_profiles_and_verlauf(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "FakeKicktipp", HistFake)
    engine, session = t.make_engine(tmp_path, rules=ScoringRules(quote=QuoteRule(3, 10)), anzahl_tipper=25)
    before = {m["key"]: m for m in engine.refresh()["matches"]}
    assert engine.me_id == "1"
    res = engine.load_history(pause_s=0)
    assert res["geladen"] == [1, 2, 3, 4, 5, 6, 7] and not res["fehler"]
    assert engine.load_history(pause_s=0)["geladen"] == []  # abgeschlossene Spieltage nicht erneut
    after = engine.refresh()
    m = after["matches"][0]
    assert any("Mitspieler-Profile" in s for s in m["sources"])
    assert 20 < m["erwartete_tipper"] < 24
    assert m["expected_quote"] != before[m["key"]]["expected_quote"]

    ms = engine.mitspieler()
    assert len(ms["profile"]) == 25 and ms["ich"] == "1"
    assert len(ms["prognosen"]) == 9 and len(ms["prognosen"][0]["tipper"]) == 24

    v = engine.verlauf()
    assert v["spieltage"] == [1, 2, 3, 4, 5, 6, 7]
    me = next(r for r in v["reihen"] if r["ich"])
    assert me["gesamt"][5] == 218 and me["platz"][5] == 1
    assert me["gesamt"][0] < me["gesamt"][5]

    client = TestClient(t.create_app(engine, background=False))
    assert client.get("/api/verlauf").json()["spieltage"] == [1, 2, 3, 4, 5, 6, 7]
    assert len(client.get("/api/mitspieler").json()["profile"]) == 25
