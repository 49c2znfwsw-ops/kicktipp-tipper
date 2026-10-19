"""Punktevergabe einer Tipprunde und Erwartungswert je möglichem Tipp."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .crowd import QuoteRule


@dataclass(frozen=True)
class OutcomePoints:
    tendenz: float = 2
    tordifferenz: float = 3
    ergebnis: float = 4


@dataclass(frozen=True)
class ScoringRules:
    """Kicktipp vergibt Punkte je nach Ausgang des Spiels (nicht des Tipps).

    Es zählt immer nur die höchste erreichte Stufe: Ergebnis > Tordifferenz > Tendenz.
    Bei aktiver Quotenregel kommen für die richtige Tendenz die Rundenquote-Punkte hinzu.
    """

    heimsieg: OutcomePoints = field(default_factory=OutcomePoints)
    unentschieden: OutcomePoints = field(default_factory=lambda: OutcomePoints(2, 2, 4))
    auswaertssieg: OutcomePoints = field(default_factory=OutcomePoints)
    quote: QuoteRule | None = None

    def points(self, tip_home: int, tip_away: int, home: int, away: int, quote_points=(0.0, 0.0, 0.0)) -> float:
        tip_sign = (tip_home > tip_away) - (tip_home < tip_away)
        res_sign = (home > away) - (home < away)
        if tip_sign != res_sign:
            return 0.0
        table = {1: self.heimsieg, 0: self.unentschieden, -1: self.auswaertssieg}[res_sign]
        base = quote_points[{1: 0, 0: 1, -1: 2}[res_sign]]
        if tip_home == home and tip_away == away:
            return base + table.ergebnis
        if tip_home - tip_away == home - away:
            return base + table.tordifferenz
        return base + table.tendenz

    def points_matrix(self, tip_home: int, tip_away: int, max_goals: int, quote_points=(0.0, 0.0, 0.0)) -> np.ndarray:
        """Punkte des Tipps für jedes Ergebnis 0..max_goals x 0..max_goals."""
        n = max_goals + 1
        m = np.empty((n, n))
        for h in range(n):
            for a in range(n):
                m[h, a] = self.points(tip_home, tip_away, h, a, quote_points)
        return m


@dataclass
class TipValue:
    home: int
    away: int
    expected_points: float

    @property
    def label(self) -> str:
        return f"{self.home}:{self.away}"


def rank_tips(
    score_probs: np.ndarray, rules: ScoringRules, max_tip_goals: int = 5, quote_points=(0.0, 0.0, 0.0)
) -> list[TipValue]:
    """Alle Tipps bis max_tip_goals nach erwarteten Punkten absteigend sortiert.

    quote_points: erwartete Quotenpunkte für richtige Tendenz Heim/Remis/Gast (0 ohne Quotenregel).
    """
    max_goals = score_probs.shape[0] - 1
    out = []
    for th in range(max_tip_goals + 1):
        for ta in range(max_tip_goals + 1):
            ev = float((score_probs * rules.points_matrix(th, ta, max_goals, quote_points)).sum())
            out.append(TipValue(th, ta, ev))
    out.sort(key=lambda t: t.expected_points, reverse=True)
    return out
