"""Review der Spieltage: deine Tipps gegen die Ergebnisse und gegen die Runde.

Je Spieltag: Abweichung der Tipps von den Ergebnissen, größte Gewinner und Verlierer gegenüber dem
Schnitt der Runde, Bewegung in der Tabelle und wo du dich von den direkten Konkurrenten abgesetzt hast.
Dazu eine Saisonübersicht. Grundlage ist allein die Tipp-Historie aus der Kicktipp-Tippübersicht.
"""
from __future__ import annotations

from collections import defaultdict

from .rueckblick import _tendency

GEGENTIPP_ANTEIL = 1 / 3  # tippt höchstens ein Drittel der Runde diese Tendenz, gilt der Tipp als Gegentipp
KONKURRENZ_PLAETZE = 2  # so viele Plätze über und unter dir zählen als direkte Konkurrenz


def _art(tip, result) -> str:
    if tip is None:
        return "kein Tipp"
    if tuple(tip) == tuple(result):
        return "exakt"
    if _tendency(tip) != _tendency(result):
        return "falsch"
    if tip[0] - tip[1] == result[0] - result[1]:
        return "Tordifferenz"
    return "Tendenz"


def _ranks(totals: dict[str, float]) -> dict[str, int]:
    """Platz nach Gesamtpunkten, Gleichstand teilt sich den Platz."""
    order = sorted(totals.items(), key=lambda x: -x[1])
    out, prev, pos = {}, None, 0
    for n, (mid, tot) in enumerate(order, 1):
        if tot != prev:
            pos, prev = n, tot
        out[mid] = pos
    return out


def _spieltag(st: int, matches: list[dict], tips: dict, members: dict, prev_totals: dict, me_id: str) -> tuple[dict, dict]:
    """Auswertung eines Spieltags und Gesamtpunkte danach. members: member_id -> Zeile aus hist_members."""
    names = {mid: r["name"] for mid, r in members.items()}
    n_members = len(members)
    games = []
    pts_by_member: dict[str, dict[str, float]] = defaultdict(dict)  # member -> match -> Punkte
    for m in matches:
        if not m["result"]:
            continue
        res = m["result"]
        mt = tips.get(m["key"], {})
        for mid in members:
            t = mt.get(mid)
            pts_by_member[mid][m["key"]] = (t["points"] or 0) if t else 0
        mine = mt.get(me_id)
        tip = mine["tip"] if mine else None
        counts = [0, 0, 0]
        for t in mt.values():
            counts[_tendency(t["tip"])] += 1
        n_tips = sum(counts)
        others = [pts_by_member[mid][m["key"]] for mid in members if mid != me_id]
        schnitt = sum(others) / len(others) if others else 0.0
        punkte = pts_by_member.get(me_id, {}).get(m["key"], 0)
        fehler = [abs(t["tip"][0] - res[0]) + abs(t["tip"][1] - res[1]) for mid, t in mt.items() if mid != me_id]
        same = counts[_tendency(tip)] / n_tips if tip and n_tips else None
        games.append({
            "key": m["key"], "home": m["home"], "away": m["away"], "ergebnis": res, "quote": m["round_quote"],
            "tipp": tip, "art": _art(tip, res), "punkte": punkte,
            "tore_daneben": abs(tip[0] - res[0]) + abs(tip[1] - res[1]) if tip else None,
            "tore_daneben_runde": sum(fehler) / len(fehler) if fehler else None,
            "schnitt": schnitt, "differenz": punkte - schnitt,
            "anteil_richtig": counts[_tendency(res)] / n_tips if n_tips else None,  # Anteil der Runde mit richtiger Tendenz
            "anteil_gleich": same,  # Anteil der Runde mit deiner Tendenz
            "gegentipp": same is not None and same <= GEGENTIPP_ANTEIL,
            "exakt_runde": sum(1 for t in mt.values() if tuple(t["tip"]) == tuple(res)),
            "tipps": n_tips,
        })

    day = {mid: (r["day_points"] if r["day_points"] is not None else sum(pts_by_member[mid].values()))
           for mid, r in members.items()}
    totals = {mid: r["total"] if r["total"] is not None else prev_totals.get(mid, 0) + day[mid] for mid, r in members.items()}
    before = {mid: totals[mid] - day[mid] for mid in members}
    r_before, r_after, r_day = _ranks(before), _ranks(totals), _ranks(day)
    others_day = [p for mid, p in day.items() if mid != me_id]
    best_mid = max((mid for mid in day if mid != me_id), key=lambda mid: day[mid], default=None)
    leader_before = min(members, key=lambda mid: (r_before[mid], names[mid]))
    leader_after = min(members, key=lambda mid: (r_after[mid], names[mid]))

    kopf = {
        "punkte": day.get(me_id), "schnitt": sum(others_day) / len(others_day) if others_day else None,
        "bester": day[best_mid] if best_mid else None, "bester_name": names.get(best_mid),
        "platz_spieltag": r_day.get(me_id), "platz_vorher": r_before.get(me_id), "platz_nachher": r_after.get(me_id),
        "teilnehmer": n_members,
        "abstand_spitze_vorher": before.get(me_id, 0) - max(before.values()) if members else None,
        "abstand_spitze_nachher": totals.get(me_id, 0) - max(totals.values()) if members else None,
        "tore_daneben": _mean(g["tore_daneben"] for g in games),
        "tore_daneben_runde": _mean(g["tore_daneben_runde"] for g in games),
        "arten": {a: sum(1 for g in games if g["art"] == a) for a in ("exakt", "Tordifferenz", "Tendenz", "falsch", "kein Tipp")},
        "gegentipps": sum(g["gegentipp"] for g in games),
        "gegentipps_richtig": sum(g["gegentipp"] and g["art"] != "falsch" for g in games),
    }

    ranked = sorted(games, key=lambda g: -g["differenz"])
    gewinner = [g for g in ranked if g["differenz"] > 0][:3]
    verlierer = [g for g in reversed(ranked) if g["differenz"] < 0][:3]

    # Direkte Konkurrenz: Plätze um dich herum vor dem Spieltag, dazu wer vorher und nachher vorn lag
    konkurrenz = []
    if me_id in members:
        mine_rank = r_before[me_id]
        rivals = [mid for mid in members if mid != me_id and abs(r_before[mid] - mine_rank) <= KONKURRENZ_PLAETZE]
        for lead in (leader_before, leader_after):
            if lead != me_id and lead not in rivals:
                rivals.append(lead)
        for mid in sorted(rivals, key=lambda x: (r_before[x], names[x])):
            per_game = [{"spiel": f"{g['home']} – {g['away']}", "du": pts_by_member[me_id][g["key"]],
                         "er": pts_by_member[mid][g["key"]],
                         "tipp": tips.get(g["key"], {}).get(mid, {}).get("tip")} for g in games]
            for p in per_game:
                p["differenz"] = p["du"] - p["er"]
            decisive = sorted((p for p in per_game if p["differenz"]), key=lambda p: -abs(p["differenz"]))[:3]
            konkurrenz.append({
                "member_id": mid, "name": names[mid], "platz_vorher": r_before[mid], "platz_nachher": r_after[mid],
                "punkte": day[mid], "abstand_vorher": before[me_id] - before[mid], "abstand_nachher": totals[me_id] - totals[mid],
                "spitze": mid in (leader_before, leader_after), "entscheidend": decisive,
            })

    return {"spieltag": st, "kopf": kopf, "spiele": games, "gewinner": gewinner, "verlierer": verlierer,
            "konkurrenz": konkurrenz}, totals


def _mean(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def review(history: dict, me_id: str | None, spieltag: int | None = None) -> dict:
    tips: dict[str, dict[str, dict]] = defaultdict(dict)
    for t in history["tips"]:
        tips[t["match_key"]][t["member_id"]] = t
    members: dict[int, dict[str, dict]] = defaultdict(dict)
    for r in history["members"]:
        members[r["spieltag"]][r["member_id"]] = r
    matches: dict[int, list[dict]] = defaultdict(list)
    for m in history["matches"]:
        matches[m["spieltag"]].append(m)
    # nur Spieltage mit mindestens einem Ergebnis
    done = [st for st in sorted(matches) if any(m["result"] for m in matches[st]) and members.get(st)]
    if not done or me_id is None:
        return {"spieltage": [], "spieltag": None}

    days, totals = {}, {}
    for st in done:
        days[st], totals = _spieltag(st, matches[st], tips, members[st], totals, me_id)

    all_games = [dict(g, spieltag=st) for st in done for g in days[st]["spiele"] if g["tipp"]]
    by_kind = {}
    for kind, sel in (("Mehrheitstipp", lambda g: not g["gegentipp"]), ("Gegentipp", lambda g: g["gegentipp"])):
        gs = [g for g in all_games if sel(g)]
        by_kind[kind] = {
            "spiele": len(gs),
            "tendenz_richtig": sum(g["art"] != "falsch" for g in gs),
            "punkte": sum(g["punkte"] for g in gs),
            "differenz": sum(g["differenz"] for g in gs),
        }
    ranked = sorted(all_games, key=lambda g: -g["differenz"])
    saison = {
        "verlauf": [{"spieltag": st, **{k: days[st]["kopf"][k] for k in
                     ("punkte", "schnitt", "bester", "platz_spieltag", "platz_nachher", "abstand_spitze_nachher",
                      "tore_daneben", "tore_daneben_runde", "gegentipps")}} for st in done],
        "gewinner": [g for g in ranked if g["differenz"] > 0][:5],
        "verlierer": [g for g in reversed(ranked) if g["differenz"] < 0][:5],
        "nach_art": by_kind,
        "punkte": sum(days[st]["kopf"]["punkte"] or 0 for st in done),
        "schnitt": sum(days[st]["kopf"]["schnitt"] or 0 for st in done),
    }
    sel = spieltag if spieltag in days else done[-1]
    return {"spieltage": done, "spieltag": sel, **days[sel], "saison": saison}
