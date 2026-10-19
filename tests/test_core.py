import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from kicktipp_tipper import model
from kicktipp_tipper.scoring import OutcomePoints, ScoringRules, rank_tips
from kicktipp_tipper.sources import oddsapi, openligadb
from kicktipp_tipper.sources.kicktipp import parse_tippabgabe
from kicktipp_tipper.teams import best_match

FIX = Path(__file__).parent / "fixtures"


def test_points_standard():
    r = ScoringRules()
    assert r.points(2, 1, 2, 1) == 4
    assert r.points(2, 1, 3, 2) == 3
    assert r.points(2, 1, 3, 0) == 2
    assert r.points(2, 1, 1, 1) == 0
    assert r.points(1, 1, 1, 1) == 4
    assert r.points(1, 1, 2, 2) == 2  # Remis: keine Tordifferenz-Wertung
    assert r.points(0, 1, 0, 2) == 2


def test_rules_change_best_tip():
    probs = model.score_matrix(1.6, 1.1, -0.08)
    std = rank_tips(probs, ScoringRules())[0]
    assert (std.home, std.away) in {(2, 1), (1, 0)}
    # Wenn Remis-Tendenz sehr hoch belohnt wird, kippt die Empfehlung auf ein Remis
    draw_heavy = ScoringRules(unentschieden=OutcomePoints(6, 6, 8))
    best = rank_tips(probs, draw_heavy)[0]
    assert best.home == best.away


def test_market_fit_recovers_lambdas():
    m = model.score_matrix(1.9, 0.9, -0.05)
    ph, pd, pa = model.outcome_probs(m)
    lh, la = model.lambdas_from_market(ph, pd, pa, model.over_prob(m), -0.05)
    assert lh == pytest.approx(1.9, abs=0.02)
    assert la == pytest.approx(0.9, abs=0.02)


def test_devig():
    p = model.devig([1.55, 4.5, 5.25])
    assert sum(p) == pytest.approx(1)
    assert p[0] > p[1] > p[2]


def test_fit_ratings_orders_teams():
    rng = np.random.default_rng(1)
    strength = {"Stark": 0.5, "Mittel": 0.0, "Schwach": -0.5, "Auch Mittel": 0.0}
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    results = []
    teams = list(strength)
    for k in range(400):
        h, a = random.Random(k).sample(teams, 2)
        lh = math.exp(0.25 + 0.2 + strength[h] - strength[a] * 0.5)
        la = math.exp(0.25 + strength[a] - strength[h] * 0.5)
        results.append(model.Result(h, a, int(rng.poisson(lh)), int(rng.poisson(la)), now - timedelta(days=k)))
    r = model.fit_ratings(results, now=now)
    assert r.attack["Stark"] > r.attack["Mittel"] > r.attack["Schwach"]
    assert r.home_adv > 0
    lh, la = r.lambdas("Stark", "Schwach")
    assert lh > la


def test_parse_tippabgabe():
    md = parse_tippabgabe((FIX / "tippabgabe.html").read_text())
    assert md.index == 7
    assert [m.home for m in md.matches] == ["Bayern München", "Werder Bremen", "1. FC Köln"]
    first, second, third = md.matches
    assert not first.open and (first.tip_home, first.tip_away) == (2, 1)
    assert first.odds == (1.55, 4.5, 5.25)
    assert second.open and second.key == "1001" and (second.tip_home, second.tip_away) == (1, 1)
    assert third.tip_home is None
    assert third.kickoff == second.kickoff  # leere Zeit -> wie Vorzeile
    assert second.kickoff.hour == 15
    names = [n for n, _ in md.form_fields]
    assert "spieltippForms[1001].tippAbgegeben" in names and "submitbutton" in names
    assert "spieltippForms[1001].heimTipp" not in names


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Bayern Munich", "FC Bayern München"),
        ("1. FC Köln", "1. FC Köln"),
        ("FC Koln", "1. FC Köln"),
        ("Borussia Monchengladbach", "Borussia Mönchengladbach"),
        ("Bayer Leverkusen", "Bayer 04 Leverkusen"),
        ("Dortmund", "Borussia Dortmund"),
        ("TSG Hoffenheim", "TSG 1899 Hoffenheim"),
        ("1. FSV Mainz 05", "1. FSV Mainz 05"),
        ("Union Berlin", "1. FC Union Berlin"),
    ],
)
def test_team_matching(name, expected):
    pool = ["FC Bayern München", "1. FC Köln", "Borussia Mönchengladbach", "Bayer 04 Leverkusen", "Borussia Dortmund",
            "TSG 1899 Hoffenheim", "1. FSV Mainz 05", "1. FC Union Berlin", "Hertha BSC", "Hamburger SV"]
    assert best_match(name, pool) == expected


def test_team_matching_no_false_positive():
    assert best_match("Real Madrid", ["FC Bayern München", "Borussia Dortmund"]) is None


def test_oddsapi_parse():
    data = [{
        "home_team": "Bayern Munich", "away_team": "Borussia Dortmund", "commence_time": "2026-10-24T16:30:00Z",
        "bookmakers": [
            {"markets": [
                {"key": "h2h", "outcomes": [{"name": "Bayern Munich", "price": 1.5}, {"name": "Draw", "price": 4.6},
                                            {"name": "Borussia Dortmund", "price": 5.8}]},
                {"key": "totals", "outcomes": [{"name": "Over", "price": 1.4, "point": 2.5}, {"name": "Under", "price": 2.9, "point": 2.5}]},
            ]},
            {"markets": [{"key": "h2h", "outcomes": [{"name": "Bayern Munich", "price": 1.55}, {"name": "Draw", "price": 4.4},
                                                     {"name": "Borussia Dortmund", "price": 5.5}]}]},
        ],
    }]
    ev = oddsapi.parse_events(data)[0]
    assert ev.bookmakers == 2
    assert ev.p_home + ev.p_draw + ev.p_away == pytest.approx(1)
    assert 0.6 < ev.p_over25 < 0.7


def test_openligadb_parse():
    data = [
        {"matchIsFinished": True, "matchDateTimeUTC": "2026-09-01T13:30:00Z", "team1": {"teamName": "A"},
         "team2": {"teamName": "B"}, "matchResults": [{"resultTypeID": 1, "pointsTeam1": 0, "pointsTeam2": 0},
                                                       {"resultTypeID": 2, "pointsTeam1": 2, "pointsTeam2": 1}]},
        {"matchIsFinished": False, "matchDateTimeUTC": "2026-11-01T13:30:00Z", "team1": {"teamName": "A"},
         "team2": {"teamName": "C"}, "matchResults": []},
    ]
    res = openligadb.parse_matches(data)
    assert len(res) == 1 and (res[0].home_goals, res[0].away_goals) == (2, 1)
