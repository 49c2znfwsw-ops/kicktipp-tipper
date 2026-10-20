"""Rückblick: Was hätten die Empfehlungen gebracht – verglichen mit deinen Tipps und der Runde?

Für jedes abgeschlossene Spiel wird die letzte Empfehlung vor Anpfiff so bewertet, als hättest du sie
getippt. Da nach Anpfiff alle Tipps sichtbar sind, lässt sich die Rundenquote dafür exakt nachrechnen:
dein tatsächlicher Tipp wird herausgenommen, die Empfehlung eingesetzt.
"""
from __future__ import annotations

from collections import defaultdict

from .crowd import QuoteRule
from .scoring import ScoringRules


def _tendency(tip) -> int:
    return 0 if tip[0] > tip[1] else 1 if tip[0] == tip[1] else 2


def points_for(rules: ScoringRules, tip, result, counts: list[int]) -> float:
    """Punkte eines Tipps bei bekannter Tendenz-Verteilung aller Tipps (inklusive dieses Tipps)."""
    quote = (0.0, 0.0, 0.0)
    if rules.quote:
        n = sum(counts)
        quote = tuple(rules.quote.points(c, n) if c else rules.quote.max for c in counts)
    return rules.points(tip[0], tip[1], result[0], result[1], quote)


def rueckblick(history: dict, recommendations: dict, me_id: str | None, rules: ScoringRules) -> dict:
    tips_by_match: dict[str, dict[str, dict]] = defaultdict(dict)
    for t in history["tips"]:
        tips_by_match[t["match_key"]][t["member_id"]] = t
    day_points = defaultdict(dict)  # spieltag -> member -> Punkte des Spieltags
    for r in history["members"]:
        if r["day_points"] is not None:
            day_points[r["spieltag"]][r["member_id"]] = r["day_points"]

    spieltage: dict[int, dict] = {}
    for m in history["matches"]:
        if not m["result"]:
            continue
        result = tuple(m["result"])
        tips = tips_by_match.get(m["key"], {})
        all_tips = {mid: tuple(t["tip"]) for mid, t in tips.items()}
        counts = [0, 0, 0]
        for tip in all_tips.values():
            counts[_tendency(tip)] += 1
        mine = all_tips.get(me_id)
        mine_pts = tips[me_id]["points"] if mine and tips[me_id]["points"] is not None else (0 if mine else None)
        rec = recommendations.get(m["key"])
        rec_pts = None
        if rec:
            c = counts.copy()
            if mine:
                c[_tendency(mine)] -= 1  # deinen echten Tipp herausnehmen …
            c[_tendency(rec["tip"])] += 1  # … und die Empfehlung einsetzen
            rec_pts = points_for(rules, rec["tip"], result, c)
        others = [t["points"] or 0 for mid, t in tips.items() if mid != me_id]
        row = {
            "key": m["key"], "home": m["home"], "away": m["away"], "ergebnis": list(result),
            "quote": m["round_quote"], "tipp": list(mine) if mine else None, "punkte": mine_pts,
            "empfehlung": rec["tip"] if rec else None, "punkte_empfehlung": rec_pts,
            "abweichung": bool(rec and mine and list(mine) != list(rec["tip"])),
            "schnitt_runde": sum(others) / len(others) if others else None,
            "bester": max(others) if others else None,
        }
        sp = spieltage.setdefault(m["spieltag"], {"spieltag": m["spieltag"], "spiele": []})
        sp["spiele"].append(row)

    out = []
    total = {"du": 0.0, "empfehlung": 0.0, "vergleichbar_du": 0.0, "spiele_mit_empfehlung": 0}
    for st in sorted(spieltage):
        sp = spieltage[st]
        games = sp["spiele"]
        with_rec = [g for g in games if g["punkte_empfehlung"] is not None]
        others_days = [p for mid, p in day_points.get(st, {}).items() if mid != me_id and p is not None]
        sp.update({
            "punkte_du": sum(g["punkte"] or 0 for g in games),
            "punkte_empfehlung": sum(g["punkte_empfehlung"] for g in with_rec) if with_rec else None,
            # fair vergleichen: deine Punkte nur in den Spielen, für die es eine Empfehlung gab
            "punkte_du_vergleichbar": sum(g["punkte"] or 0 for g in with_rec) if with_rec else None,
            "spiele_mit_empfehlung": len(with_rec),
            "abweichungen": sum(g["abweichung"] for g in games),
            "schnitt_runde": sum(others_days) / len(others_days) if others_days else None,
            "bester_runde": max(others_days) if others_days else None,
        })
        total["du"] += sp["punkte_du"]
        if with_rec:
            total["empfehlung"] += sp["punkte_empfehlung"]
            total["vergleichbar_du"] += sp["punkte_du_vergleichbar"]
            total["spiele_mit_empfehlung"] += len(with_rec)
        out.append(sp)
    return {"spieltage": out, "gesamt": total}
