"""Tests gegen die echte (anonymisierte) Kicktipp-Tippabgabe-Seite einer Runde mit Quotenregel."""
from pathlib import Path

import pytest

from kicktipp_tipper.crowd import QuoteRule
from kicktipp_tipper.optimizer import StrategyBan, violations
from kicktipp_tipper.scoring import ScoringRules
from kicktipp_tipper.sources.kicktipp import parse_tippabgabe
from kicktipp_tipper.teams import best_match

import test_engine_web as t

REAL = Path(__file__).parent / "fixtures" / "tippabgabe_echt.html"


def test_parse_real_page():
    md = parse_tippabgabe(REAL.read_text(encoding="utf-8"))
    assert md.index == 7
    assert md.form_action == "https://www.kicktipp.de/meine-runde/tippabgabe"
    assert len(md.matches) == 9
    first = md.matches[0]
    assert (first.home, first.away) == ("VfB Stuttgart", "Bor. Mönchengladbach")
    assert first.key == "9000000608" and first.open and first.tip_home is None
    assert first.odds == (1.41, 5.5, 6.25)
    assert first.round_quote == (4, 5, 10)
    assert md.matches[1].round_quote == (5, 5, 5)
    # Zeit fehlt in Folgezeilen -> wie Vorzeile; Zeitumstellung am 25.10.
    assert md.matches[2].kickoff == md.matches[1].kickoff
    assert md.matches[-1].kickoff.utcoffset().total_seconds() == 3600
    fields = dict(md.form_fields)
    assert fields["tipperId"] == "1" and fields["tippsaisonId"] == "2" and fields["bonus"] == "false"
    assert "submitbutton" in fields  # <button type="submit">


@pytest.mark.parametrize(
    "kicktipp,openligadb",
    [
        ("Bor. Mönchengladbach", "Borussia Mönchengladbach"),
        ("1899 Hoffenheim", "TSG Hoffenheim"),
        ("FSV Mainz 05", "1. FSV Mainz 05"),
        ("SC Paderborn 07", "SC Paderborn 07"),
        ("SV Elversberg", "SV 07 Elversberg"),
        ("FC Schalke 04", "FC Schalke 04"),
        ("Hamburger SV", "Hamburger SV"),
        ("1. FC Köln", "1. FC Köln"),
        ("FC Bayern München", "FC Bayern München"),
    ],
)
def test_real_team_names(kicktipp, openligadb):
    pool = ["Borussia Mönchengladbach", "TSG Hoffenheim", "1. FSV Mainz 05", "SC Paderborn 07", "SV 07 Elversberg",
            "FC Schalke 04", "Hamburger SV", "1. FC Köln", "FC Bayern München", "Borussia Dortmund", "SC Freiburg",
            "Werder Bremen", "VfB Stuttgart", "RB Leipzig", "FC Augsburg", "1. FC Union Berlin", "Eintracht Frankfurt",
            "Bayer 04 Leverkusen"]
    assert best_match(kicktipp, pool) == openligadb


class RealFake(t.FakeKicktipp):
    def __init__(self):
        super().__init__()
        self.html = REAL.read_text(encoding="utf-8")


def test_engine_on_real_matchday(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "FakeKicktipp", RealFake)
    engine, session = t.make_engine(
        tmp_path, rules=ScoringRules(quote=QuoteRule(3, 10)), strategieverbot=StrategyBan(2 / 3, 9), anzahl_tipper=25
    )
    state = engine.refresh()
    assert state["strategieverbot"]["grenze"] == 6
    tips = [tuple(m["tip"]) for m in state["matches"]]
    assert len(tips) == 9 and not violations(tips, 9, engine.cfg.strategieverbot)
    assert all(m["expected_quote"] and m["round_quote"] for m in state["matches"])
    engine.release(7, {m["key"]: tuple(m["tip"]) for m in state["matches"]})
    posted = dict(session.posts[-1])
    assert posted["tipperId"] == "1" and "submitbutton" in posted
    assert all(m["current_tip"] == m["tip"] for m in engine.state["matches"])


def _summary(md):
    return [(m.key, m.home, m.away, m.kickoff, m.open, m.odds, m.round_quote) for m in md.matches]


def test_parse_server_html_without_column_classes():
    """Kicktipp liefert die Zeilen ohne "cell colN"-Klassen; die setzt erst das Browser-JavaScript."""
    browser = parse_tippabgabe(REAL.read_text(encoding="utf-8"))
    server = parse_tippabgabe((REAL.parent / "tippabgabe_echt_server.html").read_text(encoding="utf-8"))
    assert _summary(server) == _summary(browser)
    assert len(server.matches) == 9


def test_parse_without_header_uses_positions():
    import re

    html = (REAL.parent / "tippabgabe_echt_server.html").read_text(encoding="utf-8")
    html = re.sub(r"<thead>.*?</thead>", "", html, flags=re.S)
    md = parse_tippabgabe(html)
    assert len(md.matches) == 9 and md.matches[0].round_quote == (4, 5, 10)


def test_no_matches_saves_diagnosis(tmp_path, monkeypatch):
    from kicktipp_tipper.sources.kicktipp import KicktippClient, KicktippError

    class Empty(t.FakeKicktipp):
        def __init__(self):
            super().__init__()
            self.html = '<form id="tippabgabeForm"><table id="tippabgabeSpiele"><tr><td>x</td></tr></table></form>'

    monkeypatch.chdir(tmp_path)
    with pytest.raises(KicktippError, match="keine Spiele erkannt"):
        KicktippClient("r", "u", "p", session=Empty()).matchday()
    assert (tmp_path / "kicktipp-tippabgabe-diagnose.html").exists()
