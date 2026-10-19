"""Quoten-Punkteregel: Punkte für die richtige Tendenz hängen vom Tippverhalten der Tipprunde ab.

Kicktipp-Formel: Punkte = MAX / (10 · T/N) − MAX/10 + MIN, gerundet und auf [MIN, MAX] begrenzt
(N = abgegebene Tipps, T = Tipps mit dieser Tendenz).

Da die Tipps der anderen erst nach Ablauf der Tippzeit sichtbar sind, wird ihr Verhalten geschätzt:
Anteil je Tendenz ∝ p^k (Favoriten werden überproportional getippt), Remis zusätzlich mit Faktor d.
k und d lassen sich aus beobachteten Rundenquoten vergangener Spiele lernen.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class QuoteRule:
    min: float = 3
    max: float = 10

    def points(self, t: int, n: int) -> float:
        if n <= 0 or t <= 0:
            return self.max
        raw = self.max / (10 * t / n) - self.max / 10 + self.min
        return float(min(self.max, max(self.min, math.floor(raw + 0.5))))

    def implied_share(self, q: float) -> float:
        """Umkehrung der Formel (wegen Rundung/Begrenzung nur ungefähr)."""
        return min(1.0, self.max / (10 * (q - self.min + self.max / 10)))


@dataclass(frozen=True)
class CrowdModel:
    exponent: float = 1.5
    draw_factor: float = 0.7

    def shares(self, p_home: float, p_draw: float, p_away: float) -> tuple[float, float, float]:
        w = np.array([p_home, p_draw, p_away]) ** self.exponent
        w[1] *= self.draw_factor
        w /= w.sum()
        return float(w[0]), float(w[1]), float(w[2])


def expected_quote_points(rule: QuoteRule, shares: tuple[float, float, float], n_tippers: int) -> tuple[float, float, float]:
    """Erwartete Tendenz-Punkte, falls ich Heim / Remis / Gast tippe.

    Die übrigen n-1 Tipper tippen unabhängig nach den geschätzten Anteilen; mein eigener Tipp zählt mit.
    """
    others = max(n_tippers - 1, 0)
    k = np.arange(others + 1)
    log_fact = np.array([math.lgamma(i + 1) for i in range(others + 1)])
    out = []
    for s in shares:
        s = min(max(s, 1e-9), 1 - 1e-9)
        pmf = np.exp(log_fact[-1] - log_fact - log_fact[::-1] + k * math.log(s) + (others - k) * math.log(1 - s))
        pts = np.array([rule.points(int(t) + 1, others + 1) for t in k])
        out.append(float((pmf * pts).sum()))
    return out[0], out[1], out[2]


def shares_from_quote(rule: QuoteRule, quote: tuple[float, float, float]) -> tuple[float, float, float]:
    s = np.array([rule.implied_share(q) for q in quote])
    s /= s.sum()
    return float(s[0]), float(s[1]), float(s[2])


def fit_crowd(observations: list[tuple[tuple[float, float, float], tuple[float, float, float]]]) -> CrowdModel | None:
    """observations: (Markt-Wahrscheinlichkeiten H/U/A, beobachtete Tipp-Anteile H/U/A)."""
    if len(observations) < 10:
        return None
    best, best_err = None, float("inf")
    for k in np.arange(0.5, 3.01, 0.05):
        for d in np.arange(0.2, 1.51, 0.05):
            model = CrowdModel(float(k), float(d))
            err = sum(sum((a - b) ** 2 for a, b in zip(model.shares(*p), s)) for p, s in observations)
            if err < best_err:
                best, best_err = model, err
    return best


def others_shares(
    rule: QuoteRule,
    observed_quote: tuple[float, float, float] | None,
    model_shares: tuple[float, float, float],
    n_tippers: int,
    my_tendency: int | None,
    weight_observed: float,
) -> tuple[float, float, float]:
    """Geschätzte Tendenz-Anteile der ANDEREN Tipper zum Tippschluss.

    Die angezeigte Rundenquote spiegelt den aktuellen Stand (inkl. meines bisherigen Tipps).
    Mein Tipp wird herausgerechnet; bis zum Anpfiff tippen noch weitere, daher Mischung mit dem Modell.
    my_tendency: 0 = Heim, 1 = Remis, 2 = Gast, None = noch nicht getippt.
    """
    if observed_quote is None:
        return model_shares
    s = np.array(shares_from_quote(rule, observed_quote))
    if my_tendency is not None and n_tippers > 1:
        counts = s * n_tippers
        counts[my_tendency] = max(counts[my_tendency] - 1, 0)
        s = counts / counts.sum()
    mixed = weight_observed * s + (1 - weight_observed) * np.array(model_shares)
    mixed /= mixed.sum()
    return float(mixed[0]), float(mixed[1]), float(mixed[2])
