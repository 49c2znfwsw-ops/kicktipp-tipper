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
        # Reihenfolge wie bei Kicktipp (erst T/N): an der Rundungsgrenze ergibt das z. B. 7,4999… -> 7
        raw = self.max / (10 * (t / n)) - self.max / 10 + self.min
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


def exact_counts(
    rule: QuoteRule, quote: tuple[int, int, int], n: int, prior: tuple[float, float, float] | None = None
) -> np.ndarray | None:
    """Tendenz-Zählung (H, U, A), die bei n abgegebenen Tipps genau diese Rundenquote ergibt.

    Mit bekanntem n ist die Verteilung meist eindeutig. An den Grenzen (3 bzw. 10 Punkte) passen oft
    mehrere; dann wird nach ihrer Wahrscheinlichkeit unter `prior` (erwartete Anteile) gewichtet gemittelt.
    """
    if n <= 0:
        return None
    hits = [
        (h, d, n - h - d)
        for h in range(n + 1)
        for d in range(n + 1 - h)
        if tuple(int(rule.points(x, n)) for x in (h, d, n - h - d)) == tuple(quote)
    ]
    if not hits:
        return None
    arr = np.array(hits, dtype=float)
    if prior is None or len(hits) == 1:
        return arr.mean(axis=0)
    logp = np.log(np.maximum(np.asarray(prior, dtype=float), 1e-3))
    log_fact = np.array([math.lgamma(k + 1) for k in range(n + 1)])
    ll = np.array([-log_fact[int(h)] - log_fact[int(d)] - log_fact[int(a)] + h * logp[0] + d * logp[1] + a * logp[2]
                   for h, d, a in arr])
    w = np.exp(ll - ll.max())
    return (arr * w[:, None]).sum(axis=0) / w.sum()


def expected_quote_points_exact(
    rule: QuoteRule, fixed: np.ndarray, remaining: list[tuple[float, np.ndarray]]
) -> tuple[float, float, float]:
    """Erwartete Tendenz-Punkte, falls ich Heim / Remis / Gast tippe.

    fixed: bereits abgegebene Tipps der anderen je Tendenz (bekannt).
    remaining: je noch fehlendem Mitspieler (Wahrscheinlichkeit, dass er tippt; Tendenz-Wahrscheinlichkeiten).
    Rechnet exakt über alle Kombinationen (Poisson-Binomial, gemeinsam für "wie viele mit Tendenz t" und "wie viele insgesamt").
    """
    n_fixed = int(round(float(np.sum(fixed))))
    m = len(remaining)
    out = []
    for t in range(3):
        # dist[k, j]: k zusätzliche Tipps mit Tendenz t, j zusätzliche Tipps insgesamt
        dist = np.zeros((m + 1, m + 1))
        dist[0, 0] = 1.0
        for p_tip, probs in remaining:
            q = float(probs[t])
            new = dist * (1 - p_tip)
            new[1:, 1:] += dist[:-1, :-1] * p_tip * q
            new[:, 1:] += dist[:, :-1] * p_tip * (1 - q)
            dist = new
        ev = 0.0
        base_t = float(fixed[t])
        for k in range(m + 1):
            for j in range(k, m + 1):
                w = dist[k, j]
                if w > 1e-12:
                    # +1: mein eigener Tipp zählt mit
                    ev += w * rule.points(int(round(base_t)) + k + 1, n_fixed + j + 1)
        out.append(ev)
    return out[0], out[1], out[2]
