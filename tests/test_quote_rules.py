"""Quoten-Punkteregel und Strategieverbot nach den Spielregeln der Tipprunde."""
from pathlib import Path

import pytest

from kicktipp_tipper import crowd, model
from kicktipp_tipper.crowd import CrowdModel, QuoteRule
from kicktipp_tipper.optimizer import StrategyBan, optimize, result_class, violations
from kicktipp_tipper.scoring import ScoringRules, rank_tips
from kicktipp_tipper.sources.kicktipp import parse_tippabgabe

FIX = Path(__file__).parent / "fixtures"
RULE = QuoteRule(3, 10)


def test_quote_formula_example_from_rules():
    # Beispiel aus den Spielregeln: 25 Tipps, 5 auf Heimsieg -> 7 Punkte
    assert RULE.points(5, 25) == 7


@pytest.mark.parametrize("t,n,expected", [(25, 25, 3), (1, 25, 10), (12, 25, 4), (2, 20, 10), (3, 20, 9), (10, 20, 4)])
def test_quote_formula_clamped_and_rounded(t, n, expected):
    assert RULE.points(t, n) == expected


def test_points_with_quote_range_5_to_14():
    r = ScoringRules(quote=RULE)
    q = (3.0, 7.0, 10.0)
    assert r.points(2, 1, 3, 0, q) == 5  # Tendenz Heim: 3 + 2
    assert r.points(2, 1, 3, 2, q) == 6  # Tordifferenz: 3 + 3
    assert r.points(0, 1, 0, 1, q) == 14  # Ergebnis Auswärtssieg: 10 + 4
    assert r.points(1, 1, 2, 2, q) == 9  # Remis-Tendenz: 7 + 2
    assert r.points(1, 1, 1, 1, q) == 11
    assert r.points(1, 1, 2, 1, q) == 0


def test_expected_quote_points_contrarian_higher():
    shares = CrowdModel(1.5, 0.7).shares(0.55, 0.25, 0.20)
    assert shares[0] > 0.6 and shares[1] < 0.2
    qh, qd, qa = crowd.expected_quote_points(RULE, shares, 20)
    assert qh < qd and qh < qa
    assert 3 <= qh <= 10 and 3 <= qa <= 10


def test_own_tip_counts_in_small_round():
    # Niemand sonst tippt Auswärtssieg: tippe ich ihn, bin ich 1 von 5 -> 10/(10*0.2) - 1 + 3 = 7
    q = crowd.expected_quote_points(RULE, (0.999999, 1e-9, 1e-6), 5)
    assert q[2] == pytest.approx(7, abs=0.01)


def test_quote_shifts_recommendation_to_underdog_or_draw():
    probs = model.score_matrix(1.6, 1.1, -0.08)
    std = rank_tips(probs, ScoringRules())[0]
    assert std.home > std.away
    # Runde tippt fast geschlossen Heimsieg -> Remis/Auswärts bringen viel mehr Punkte
    q = crowd.expected_quote_points(RULE, (0.9, 0.05, 0.05), 20)
    best = rank_tips(probs, ScoringRules(quote=RULE), 5, q)[0]
    assert best.home <= best.away


def test_observed_quote_drives_shares_and_removes_own_tip():
    model_shares = (0.6, 0.15, 0.25)
    s = crowd.others_shares(RULE, (10, 10, 3), model_shares, 20, None, 1.0)
    assert s[2] > 0.7  # Rundenquote 3 für Gast -> Gast ist Favorit der Runde
    with_me = crowd.others_shares(RULE, (10, 10, 3), model_shares, 20, 2, 1.0)
    assert with_me[2] < s[2]


def test_fit_crowd_recovers_behaviour():
    true = CrowdModel(2.0, 0.5)
    obs = []
    for ph in [0.3, 0.4, 0.5, 0.6, 0.7, 0.35, 0.45, 0.55, 0.65, 0.25, 0.2, 0.5]:
        pd = 0.26
        p = (ph, pd, 1 - ph - pd)
        obs.append((p, true.shares(*p)))
    fit = crowd.fit_crowd(obs)
    assert fit.exponent == pytest.approx(2.0, abs=0.1)
    assert fit.draw_factor == pytest.approx(0.5, abs=0.1)


def test_strategy_ban_cap():
    ban = StrategyBan()
    assert ban.cap(8) is None
    assert ban.cap(9) == 6
    assert ban.cap(18) == 12
    assert result_class((1, 2)) == result_class((2, 1))
    assert violations([(2, 1)] * 4 + [(1, 2)] * 3 + [(0, 0), (1, 1)], 9, ban) == {"2:1": 7}
    assert violations([(2, 1)] * 7, 8, ban) == {}


def test_optimizer_respects_ban_and_minimises_loss():
    ban = StrategyBan()
    options = {}
    for i in range(9):
        # Alle wollen 2:1, erlaubt sind 6; Spiele 0-2 verlieren am wenigsten beim Ausweichen auf 1:0
        loss = 0.01 if i < 3 else 0.5
        options[f"m{i}"] = [((2, 1), 3.0), ((1, 0), 3.0 - loss), ((1, 1), 2.0)]
    chosen = optimize(options, [], 9, ban)
    assert not violations(list(chosen.values()), 9, ban)
    assert chosen["m0"] == chosen["m1"] == chosen["m2"] == (1, 0)
    assert all(chosen[f"m{i}"] == (2, 1) for i in range(3, 9))


def test_optimizer_counts_fixed_tips():
    ban = StrategyBan()
    options = {f"m{i}": [((2, 1), 3.0), ((1, 0), 2.9)] for i in range(4)}
    chosen = optimize(options, [(1, 2)] * 5, 9, ban)  # 5 geschlossene Spiele schon 2:1/1:2
    assert sum(1 for t in chosen.values() if result_class(t) == (2, 1)) == 1


def test_parse_round_quote():
    md = parse_tippabgabe((FIX / "tippabgabe.html").read_text())
    assert [m.round_quote for m in md.matches] == [(3, 8, 10), (5, 6, 4), (10, 10, 3)]
    assert md.matches[0].odds == (1.55, 4.5, 5.25)
