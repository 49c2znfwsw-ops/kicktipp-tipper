"""Nachvollziehbare Rechnung für den Reiter "So funktioniert's".

Rechnet ein Spiel mit denselben Funktionen durch, die auch die Empfehlungen erzeugen,
und gibt jeden Zwischenschritt mit Zahlen zurück. So kann die Erklärung nie vom Verfahren abweichen.
"""
from __future__ import annotations

import numpy as np

from . import crowd, model
from .profiles import _probs
from .scoring import ScoringRules, rank_tips

EXAMPLE = {
    "home": "Heimelf", "away": "Gastelf",
    "odds": (1.80, 3.80, 4.20), "round_quote": (4, 7, 9), "tipper": 22,
}

# Drei erfundene Mitspieler, um das Profil-Modell zu zeigen
EXAMPLE_PLAYERS = [
    {"name": "Anna (Mitläuferin)", "a": 1.6, "b": [1.0, 1.0, 1.0]},
    {"name": "Ben (Querdenker)", "a": 0.4, "b": [1.0, 1.0, 1.0]},
    {"name": "Cem (Remis-Fan)", "a": 1.0, "b": [0.8, 2.0, 0.8]},
]


def _tip_breakdown(probs: np.ndarray, rules: ScoringRules, tip: tuple[int, int], q: tuple[float, float, float]) -> dict:
    """Erwartete Punkte eines Tipps, zerlegt in Ergebnis / Tordifferenz / nur Tendenz."""
    th, ta = tip
    sign = (th > ta) - (th < ta)
    t_idx = {1: 0, 0: 1, -1: 2}[sign]
    table = {1: rules.heimsieg, 0: rules.unentschieden, -1: rules.auswaertssieg}[sign]
    n = probs.shape[0]
    p_exact = float(probs[th, ta]) if th < n and ta < n else 0.0
    p_diff = p_tend = 0.0
    for h in range(n):
        for a in range(n):
            if ((h > a) - (h < a)) != sign or (h, a) == (th, ta):
                continue
            if h - a == th - ta:
                p_diff += float(probs[h, a])
            else:
                p_tend += float(probs[h, a])
    qt = q[t_idx]
    ev = p_exact * (qt + table.ergebnis) + p_diff * (qt + table.tordifferenz) + p_tend * (qt + table.tendenz)
    return {
        "tipp": f"{th}:{ta}", "tendenz": ["Heimsieg", "Remis", "Auswärtssieg"][t_idx],
        "p_ergebnis": p_exact, "p_differenz": p_diff, "p_tendenz": p_tend,
        "quote": qt, "pkt_ergebnis": qt + table.ergebnis, "pkt_differenz": qt + table.tordifferenz,
        "pkt_tendenz": qt + table.tendenz, "erwartet": ev,
    }


def explain(engine, key: str | None = None) -> dict:
    cfg = engine.cfg
    rule = cfg.rules.quote or crowd.QuoteRule()
    profiles = engine.profiles()
    rec = None
    if key:
        rec = next((m for m in (engine.state or {}).get("matches", []) if m["key"] == key and m["probs"]), None)

    if rec:
        home, away = rec["home"], rec["away"]
        odds = None  # echte Spiele: Wahrscheinlichkeiten stehen schon fest (ggf. aus mehreren Quellen gemischt)
        p = [rec["probs"]["heim"], rec["probs"]["remis"], rec["probs"]["gast"]]
        lam = tuple(rec["lambdas"])
        rq = tuple(rec["round_quote"]) if rec.get("round_quote") else None
        n_default = round(rec.get("erwartete_tipper") or cfg.anzahl_tipper)
        quellen = rec["sources"]
    else:
        home, away, odds, rq, n_default = (EXAMPLE["home"], EXAMPLE["away"], EXAMPLE["odds"],
                                            EXAMPLE["round_quote"], EXAMPLE["tipper"])
        p = model.devig(list(odds))
        lam = model.lambdas_from_market(*p, None, cfg.rho)
        quellen = ["Wettquote (Beispiel)"]

    step_odds = None
    if odds:
        inv = [1 / o for o in odds]
        step_odds = {"quoten": list(odds), "kehrwerte": inv, "summe": sum(inv), "marge": sum(inv) - 1, "wahrsch": p}

    probs = model.score_matrix(lam[0], lam[1], cfg.rho)
    ph, pd, pa = model.outcome_probs(probs)
    flat = sorted(((float(probs[h, a]), h, a) for h in range(7) for a in range(7)), reverse=True)[:8]

    # Rundenquote -> Anteile der Runde -> Prognose -> erwartete Quotenpunkte
    model_shares = cfg.crowd.shares(ph, pd, pa)
    basis = crowd.shares_from_quote(rule, rq) if rq else model_shares
    use_profiles = profiles is not None and len(profiles.others()) >= 5
    if use_profiles:
        forecast, n_exp = profiles.crowd_forecast(home, away, basis)
        n = max(2, round(n_exp))
    else:
        forecast, n = model_shares, n_default
    current = rec.get("current_tip") if rec else None
    my_t = None if not current else (0 if current[0] > current[1] else 1 if current[0] == current[1] else 2)
    others = crowd.others_shares(rule, rq, forecast, n, my_t, cfg.gewicht_rundenquote)
    q = crowd.expected_quote_points(rule, others, n)
    exakt = None
    submitted = engine._submitted.get(rec["key"]) if rec else None
    if rec and submitted is not None and use_profiles and rq:
        # Gleiche Rechnung wie die Engine: abgegebene Tipps exakt, nur Fehlende prognostiziert
        from types import SimpleNamespace

        holder = SimpleNamespace(runde_basis=None, erwartete_tipper=None)
        match = SimpleNamespace(key=rec["key"], round_quote=rq, home=home, away=away, open=rec["open"])
        q = engine._exact_quote(match, holder, profiles, submitted, current, model_shares, rule)
        n = round(holder.erwartete_tipper)
        others = tuple(holder.runde_basis)
        quote_now = engine._submitted_quote.get(rec["key"]) or rq
        exakt = {"abgegeben": len(submitted), "zaehlung": crowd.exact_counts(rule, quote_now, len(submitted), prior=model_shares).tolist()
                 if crowd.exact_counts(rule, quote_now, len(submitted)) is not None else None,
                 "erwartet": holder.erwartete_tipper}
    beispiel_spieler = [
        {"name": s["name"], "a": s["a"], "b": s["b"],
         "p": _probs(np.array(basis), s["a"], np.array(s["b"])).tolist()} for s in EXAMPLE_PLAYERS
    ]
    formel = None
    if rq:
        t_home = max(1, round(basis[0] * n))
        formel = {"n": n, "t": t_home, "roh": rule.max / (10 * (t_home / n)) - rule.max / 10 + rule.min,
                  "punkte": rule.points(t_home, n), "min": rule.min, "max": rule.max}

    ranked = rank_tips(probs, cfg.rules, cfg.max_tore_tipp, q)
    candidates = [(t.home, t.away) for t in ranked[:3]]
    for extra in ((1, 1), (0, 1)):
        if extra not in candidates:
            candidates.append(extra)
    tips = [_tip_breakdown(probs, cfg.rules, c, q) for c in candidates]

    return {
        "spiel": {"home": home, "away": away, "beispiel": rec is None, "quellen": quellen},
        "schritt_quoten": step_odds,
        "wahrsch": p,
        "gewicht_quoten": cfg.gewicht_quoten,
        "xg": list(lam),
        "rho": cfg.rho,
        "top_ergebnisse": [{"ergebnis": f"{h}:{a}", "p": v} for v, h, a in flat],
        "tendenzen_modell": [ph, pd, pa],
        "rundenquote": list(rq) if rq else None,
        "basis": list(basis),
        "formel": formel,
        "beispiel_spieler": beispiel_spieler,
        "profile_genutzt": use_profiles,
        "prognose_runde": list(forecast),
        "gewicht_rundenquote": cfg.gewicht_rundenquote,
        "anteile_tippschluss": list(others),
        "tipper": n,
        "quote_erwartet": list(q),
        "exakt": exakt,
        "regeln": {"quote": rule.__dict__, "sieg": cfg.rules.heimsieg.__dict__, "remis": cfg.rules.unentschieden.__dict__},
        "tipps": tips,
        "empfehlung": f"{ranked[0].home}:{ranked[0].away}",
        "strategieverbot": ({"anteil": cfg.strategieverbot.max_share, "ab": cfg.strategieverbot.min_matches,
                             "grenze": cfg.strategieverbot.cap(9)} if cfg.strategieverbot else None),
        "spiele_auswahl": [{"key": m["key"], "name": f"{m['home']} – {m['away']}"}
                           for m in (engine.state or {}).get("matches", []) if m["probs"]],
    }
