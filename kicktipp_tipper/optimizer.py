"""Strategieverbot: höchstens ein Anteil (z. B. 2/3) der Tipps eines Spieltags mit gleichem Ergebnis.

"2:1" und "1:2" gelten als gleiches Ergebnis. Die Tipps aller offenen Spiele werden gemeinsam so gewählt,
dass die Summe der erwarteten Punkte maximal ist und die Grenze eingehalten wird (Zuordnungsproblem).
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

Tip = tuple[int, int]


@dataclass(frozen=True)
class StrategyBan:
    max_share: float = 2 / 3
    min_matches: int = 9

    def cap(self, n_matches: int) -> int | None:
        if n_matches < self.min_matches:
            return None
        return math.floor(self.max_share * n_matches + 1e-9)


def result_class(tip: Tip) -> Tip:
    return (max(tip), min(tip))


def violations(tips: list[Tip], n_matches: int, ban: StrategyBan | None) -> dict[str, int]:
    """Ergebnisse, die zu oft getippt sind -> Anzahl."""
    cap = ban.cap(n_matches) if ban else None
    if cap is None:
        return {}
    counts = Counter(result_class(t) for t in tips)
    return {f"{a}:{b}": c for (a, b), c in counts.items() if c > cap}


def optimize(
    options: dict[str, list[tuple[Tip, float]]],
    fixed: list[Tip],
    n_matches: int,
    ban: StrategyBan | None,
) -> dict[str, Tip]:
    """options: Spiel -> [(Tipp, erwartete Punkte), ...]; fixed: Tipps, die feststehen (geschlossene Spiele)."""
    best = {k: max(v, key=lambda o: o[1])[0] for k, v in options.items() if v}
    cap = ban.cap(n_matches) if ban else None
    if cap is None or not violations(list(best.values()) + fixed, n_matches, ban):
        return best

    used = Counter(result_class(t) for t in fixed)
    keys = [k for k in options if options[k]]
    # Je Spiel und Ergebnisklasse der beste Tipp
    per_class: dict[str, dict[Tip, tuple[Tip, float]]] = {}
    for k in keys:
        d: dict[Tip, tuple[Tip, float]] = {}
        for tip, ev in options[k]:
            c = result_class(tip)
            if c not in d or ev > d[c][1]:
                d[c] = (tip, ev)
        per_class[k] = d
    classes = sorted({c for d in per_class.values() for c in d})
    slots = [c for c in classes for _ in range(max(0, min(cap - used[c], len(keys))))]
    cost = np.full((len(keys), len(slots)), 1e6)
    for i, k in enumerate(keys):
        for j, c in enumerate(slots):
            if c in per_class[k]:
                cost[i, j] = -per_class[k][c][1]
    rows, cols = linear_sum_assignment(cost)
    return {keys[i]: per_class[keys[i]][slots[j]][0] for i, j in zip(rows, cols)}
