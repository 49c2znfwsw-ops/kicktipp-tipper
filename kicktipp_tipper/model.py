"""Torverteilungsmodell (Poisson mit Dixon-Coles-Korrektur).

Zwei Wege zu den Torerwartungen (lambda_heim, lambda_gast):
  1. aus Wettquoten: lambdas so wählen, dass das Modell die margenbereinigten
     1X2-Wahrscheinlichkeiten (und ggf. Über/Unter 2,5) trifft;
  2. aus Ergebnissen: Angriffs-/Abwehrstärken je Team mit zeitlich abklingender Gewichtung.
Beide werden im Log-Raum gewichtet gemischt.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

MAX_GOALS = 10


def poisson_pmf(lam: float, n: int = MAX_GOALS) -> np.ndarray:
    k = np.arange(n + 1)
    logp = k * math.log(lam) - lam - np.array([math.lgamma(i + 1) for i in k])
    return np.exp(logp)


def score_matrix(lam_home: float, lam_away: float, rho: float = 0.0, n: int = MAX_GOALS) -> np.ndarray:
    m = np.outer(poisson_pmf(lam_home, n), poisson_pmf(lam_away, n))
    if rho:
        m[0, 0] *= 1 - lam_home * lam_away * rho
        m[0, 1] *= 1 + lam_home * rho
        m[1, 0] *= 1 + lam_away * rho
        m[1, 1] *= 1 - rho
        m = np.clip(m, 0, None)
    return m / m.sum()


def outcome_probs(m: np.ndarray) -> tuple[float, float, float]:
    home = float(np.tril(m, -1).sum())
    draw = float(np.trace(m))
    return home, draw, 1.0 - home - draw


def over_prob(m: np.ndarray, line: float = 2.5) -> float:
    n = m.shape[0]
    totals = np.add.outer(np.arange(n), np.arange(n))
    return float(m[totals > line].sum())


def devig(odds: list[float]) -> list[float]:
    """Buchmachermarge proportional herausrechnen."""
    inv = [1.0 / o for o in odds]
    s = sum(inv)
    return [x / s for x in inv]


def _nelder_mead(f, x0: np.ndarray, step: float = 0.3, tol: float = 1e-10, max_iter: int = 400) -> np.ndarray:
    dim = len(x0)
    pts = [x0] + [x0 + step * np.eye(dim)[i] for i in range(dim)]
    vals = [f(p) for p in pts]
    for _ in range(max_iter):
        order = np.argsort(vals)
        pts = [pts[i] for i in order]
        vals = [vals[i] for i in order]
        if abs(vals[-1] - vals[0]) < tol:
            break
        centroid = np.mean(pts[:-1], axis=0)
        xr = centroid + (centroid - pts[-1])
        fr = f(xr)
        if fr < vals[0]:
            xe = centroid + 2 * (centroid - pts[-1])
            fe = f(xe)
            pts[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
        else:
            xc = centroid + 0.5 * (pts[-1] - centroid)
            fc = f(xc)
            if fc < vals[-1]:
                pts[-1], vals[-1] = xc, fc
            else:
                pts = [pts[0]] + [pts[0] + 0.5 * (p - pts[0]) for p in pts[1:]]
                vals = [vals[0]] + [f(p) for p in pts[1:]]
    return pts[int(np.argmin(vals))]


def lambdas_from_market(
    p_home: float, p_draw: float, p_away: float, p_over25: float | None = None, rho: float = 0.0
) -> tuple[float, float]:
    target = np.array([p_home, p_draw, p_away])

    def loss(x: np.ndarray) -> float:
        lh, la = math.exp(x[0]), math.exp(x[1])
        m = score_matrix(lh, la, rho)
        err = float(((np.array(outcome_probs(m)) - target) ** 2).sum())
        if p_over25 is not None:
            err += (over_prob(m) - p_over25) ** 2
        return err

    x = _nelder_mead(loss, np.array([math.log(1.5), math.log(1.2)]))
    return math.exp(x[0]), math.exp(x[1])


@dataclass
class TeamRatings:
    mu: float
    home_adv: float
    attack: dict[str, float]
    defence: dict[str, float]

    def lambdas(self, home: str, away: str) -> tuple[float, float] | None:
        if home not in self.attack or away not in self.attack:
            return None
        lh = math.exp(self.mu + self.home_adv + self.attack[home] - self.defence[away])
        la = math.exp(self.mu + self.attack[away] - self.defence[home])
        return lh, la


@dataclass
class Result:
    home: str
    away: str
    home_goals: int
    away_goals: int
    date: datetime


def fit_ratings(
    results: list[Result],
    half_life_days: float = 180,
    now: datetime | None = None,
    shrink: float = 3.0,
    iterations: int = 200,
) -> TeamRatings | None:
    """Gewichtete Poisson-Regression (Angriff/Abwehr je Team, Heimvorteil).

    shrink wirkt wie eine L2-Strafe (Pseudo-Spiele) und hält Aufsteiger mit wenig Daten nahe am Mittel.
    """
    if not results:
        return None
    now = now or datetime.now(timezone.utc)
    teams = sorted({r.home for r in results} | {r.away for r in results})
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    hi = np.array([idx[r.home] for r in results])
    ai = np.array([idx[r.away] for r in results])
    hg = np.array([r.home_goals for r in results], dtype=float)
    ag = np.array([r.away_goals for r in results], dtype=float)
    age = np.array([(now - r.date).total_seconds() / 86400 for r in results])
    w = 0.5 ** (np.clip(age, 0, None) / half_life_days)

    mu = math.log(max((hg * w).sum() + (ag * w).sum(), 1e-6) / (2 * w.sum()))
    home_adv = 0.2
    att = np.zeros(n)
    dfn = np.zeros(n)
    # Diagonales Newton-Verfahren: Schrittweite je Parameter = Gradient / Krümmung
    for _ in range(iterations):
        lh = np.exp(mu + home_adv + att[hi] - dfn[ai])
        la = np.exp(mu + att[ai] - dfn[hi])
        rh, ra = w * (hg - lh), w * (ag - la)
        ch, ca = w * lh, w * la
        g_att = np.bincount(hi, rh, n) + np.bincount(ai, ra, n) - shrink * att
        h_att = np.bincount(hi, ch, n) + np.bincount(ai, ca, n) + shrink
        g_def = -np.bincount(ai, rh, n) - np.bincount(hi, ra, n) - shrink * dfn
        h_def = np.bincount(ai, ch, n) + np.bincount(hi, ca, n) + shrink
        att += 0.5 * g_att / h_att
        dfn += 0.5 * g_def / h_def
        att -= att.mean()
        dfn -= dfn.mean()
        mu += 0.5 * (rh.sum() + ra.sum()) / max(ch.sum() + ca.sum(), 1e-9)
        home_adv += 0.5 * rh.sum() / max(ch.sum(), 1e-9)
    return TeamRatings(
        mu=float(mu),
        home_adv=float(home_adv),
        attack={t: float(att[i]) for t, i in idx.items()},
        defence={t: float(dfn[i]) for t, i in idx.items()},
    )


def blend_lambdas(
    market: tuple[float, float] | None, model: tuple[float, float] | None, weight_market: float
) -> tuple[float, float] | None:
    if market and model:
        w = weight_market
        return tuple(math.exp(w * math.log(m) + (1 - w) * math.log(r)) for m, r in zip(market, model))  # type: ignore[return-value]
    return market or model
