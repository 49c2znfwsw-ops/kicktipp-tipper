"""Profile der Mitspieler aus der Tipp-Historie und Prognose ihres Tippverhaltens.

Modell je Mitspieler m für ein Spiel mit (geschätzten) Tendenz-Anteilen der Runde S = (S_H, S_U, S_A):

    P_m(t) ∝ S_t ^ a_m · b_m,t · Vereinsfaktor

a_m  "Mitläufer-Exponent": > 1 folgt der Mehrheit stärker als der Schnitt, < 1 weicht eher ab
b_m  Vorliebe für Heim / Remis / Auswärts, unabhängig vom Spiel
Vereinsfaktor  tippt bestimmte Vereine häufiger (oder seltener) auf Sieg als die Runde

Historisch ist S der Anteil der übrigen Mitspieler (ohne m) – so lernt das Modell, wie m relativ
zur Runde tippt. Für ein neues Spiel kommt S aus Rundenquote bzw. Wettquoten.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field

import numpy as np

EPS = 0.02  # Untergrenze für Anteile, damit "niemand hat das getippt" nicht 0 wird


def tendency(tip) -> int:
    """0 = Heim, 1 = Remis, 2 = Gast"""
    return 0 if tip[0] > tip[1] else 1 if tip[0] == tip[1] else 2


@dataclass
class Profile:
    member_id: str
    name: str
    ich: bool
    platz: int | None
    punkte: int | None
    spiele: int  # Spiele mit abgegebenem Tipp
    teilnahme: float  # Wahrscheinlichkeit, dass ein Tipp abgegeben wird (jüngere Spieltage zählen mehr)
    anteile: list[float]  # eigene Tendenz-Anteile H/U/A
    anteile_runde: list[float]  # Anteile der übrigen Runde in denselben Spielen
    mitlaeufer: float  # a_m
    mehrheit: float  # Anteil der Tipps auf die Mehrheitstendenz der übrigen Runde
    vorliebe: list[float]  # b_m
    lieblingsergebnisse: list[dict]
    tore: float  # Ø Tore je Tipp
    punkte_je_spiel: float | None
    trefferquote: float | None  # richtige Tendenz
    exakt: float | None  # richtiges Ergebnis
    vereine: list[dict] = field(default_factory=list)  # [{"verein", "faktor", "spiele"}]
    typ: list[str] = field(default_factory=list)


def _shares(tips: list[tuple[int, int]]) -> np.ndarray:
    c = np.zeros(3)
    for t in tips:
        c[tendency(t)] += 1
    return c / c.sum() if c.sum() else np.full(3, 1 / 3)


def _probs(S: np.ndarray, a: float, b: np.ndarray, lift: np.ndarray | None = None) -> np.ndarray:
    p = np.maximum(S, EPS) ** a * b
    if lift is not None:
        p = p * lift
    return p / p.sum()


def _fit(samples: list[tuple[np.ndarray, int]], prior: float = 3.0) -> tuple[float, np.ndarray]:
    """Mitläufer-Exponent a und Vorliebe b per Maximum-Likelihood (Gitter + Fixpunkt für b).

    prior zieht a bei wenigen Spielen zu 1 (= tippt wie der Schnitt der Runde).
    """
    if not samples:
        return 1.0, np.ones(3)
    S = np.maximum(np.array([x for x, _ in samples]), EPS)
    t = np.array([y for _, y in samples])
    counts = np.bincount(t, minlength=3).astype(float)
    logS = np.log(S)
    best = (1.0, np.ones(3), -math.inf)
    for a in np.arange(0.0, 3.01, 0.1):
        base = np.exp(a * logS)
        b = np.ones(3)
        for _ in range(15):
            p = base * b
            p /= p.sum(axis=1, keepdims=True)
            b = b * (counts + 1) / (p.sum(axis=0) + 1)  # Glättung: je Tendenz 1 Pseudo-Beobachtung
            b = np.clip(b / b.mean(), 0.2, 5.0)
        p = base * b
        p /= p.sum(axis=1, keepdims=True)
        ll = float(np.log(p[np.arange(len(t)), t]).sum()) - prior * (a - 1.0) ** 2
        if ll > best[2]:
            best = (float(a), b, ll)
    return best[0], best[1]


class Profiles:
    def __init__(self, history: dict, me_id: str | None):
        self.me_id = me_id
        self.matches = {m["key"]: m for m in history["matches"]}
        self.spieltage = sorted({m["spieltag"] for m in history["matches"]})
        by_match: dict[str, dict[str, tuple[int, int]]] = defaultdict(dict)
        points: dict[str, list[int]] = defaultdict(list)
        for t in history["tips"]:
            by_match[t["match_key"]][t["member_id"]] = tuple(t["tip"])
            if t["points"] is not None:
                points[t["member_id"]].append(t["points"])
        self.by_match = by_match

        latest: dict[str, dict] = {}
        for row in history["members"]:
            latest[row["member_id"]] = row  # nach Spieltag sortiert -> letzter Stand gewinnt
        self.names = {mid: r["name"] for mid, r in latest.items()}
        ranking = sorted(latest.values(), key=lambda r: -(r["total"] or 0))
        platz, prev = 0, None
        self.platz = {}
        for i, r in enumerate(ranking, 1):
            if r["total"] != prev:
                platz, prev = i, r["total"]
            self.platz[r["member_id"]] = platz
        self.totals = {mid: r["total"] for mid, r in latest.items()}

        self.profiles: dict[str, Profile] = {}
        self._params: dict[str, tuple[float, np.ndarray, dict[str, float], float]] = {}
        for mid in self.names:
            self._build(mid, points.get(mid, []))
        active = [p for p in self.profiles.values() if p.spiele >= 5]
        avg_majority = float(np.mean([p.mehrheit for p in active])) if active else 0.7
        for p in self.profiles.values():
            p.typ = _types(p, avg_majority)

    # ---- Profil eines Mitspielers ----------------------------------------------------------
    def _build(self, mid: str, pts: list[int]) -> None:
        samples, own, crowd, team_obs = [], [], [], defaultdict(lambda: [0.0, 0.0, 0])
        majority_hits = 0
        hits = exact = judged = 0
        played_by_spieltag: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        for key, tips in self.by_match.items():
            m = self.matches.get(key)
            if m is None:
                continue
            played_by_spieltag[m["spieltag"]][1] += 1
            tip = tips.get(mid)
            others = [t for o, t in tips.items() if o != mid]
            if tip is None or not others:
                continue
            played_by_spieltag[m["spieltag"]][0] += 1
            S = _shares(others)
            t = tendency(tip)
            samples.append((S, t))
            own.append(t)
            crowd.append(S)
            majority_hits += int(t == int(np.argmax(S)))
            # Verein auf Sieg getippt? Vergleich mit dem Anteil der Runde
            for team, win in ((m["home"], 0), (m["away"], 2)):
                obs = team_obs[team]
                obs[0] += int(t == win)
                obs[1] += S[win]
                obs[2] += 1
            if m["result"]:
                judged += 1
                hits += int(t == tendency(m["result"]))
                exact += int(tuple(tip) == tuple(m["result"]))

        a, b = _fit(samples)
        lifts = {}
        for team, (k, e, n) in team_obs.items():
            if n >= 2:
                lifts[team] = (k + 0.5) / (e + 0.5)
        # Teilnahme: jüngere Spieltage zählen doppelt so viel wie der jeweils vorige
        w_sum = w_hit = 0.0
        for i, st in enumerate(sorted(played_by_spieltag)):
            played, total = played_by_spieltag[st]
            w = 2.0 ** i
            w_hit += w * played / max(total, 1)
            w_sum += w
        teilnahme = (w_hit + 0.1) / (w_sum + 0.2) if w_sum else 0.5
        tips_all = [t for t in (self.by_match[k].get(mid) for k in self.by_match) if t]
        tore = float(np.mean([sum(t) for t in tips_all])) if tips_all else 0.0
        fav = Counter(f"{max(t)}:{min(t)}" for t in tips_all).most_common(3)
        n = len(own)
        anteile = (np.bincount(own, minlength=3) / n).tolist() if n else [0.0, 0.0, 0.0]
        anteile_runde = (np.mean(crowd, axis=0)).tolist() if crowd else [0.0, 0.0, 0.0]
        vereine = sorted(
            ({"verein": tm, "faktor": round(f, 2), "spiele": team_obs[tm][2]} for tm, f in lifts.items()
             if f >= 1.6 or f <= 0.5),
            key=lambda x: -abs(math.log(x["faktor"])),
        )[:4]
        prof = Profile(
            member_id=mid,
            name=self.names[mid],
            ich=mid == self.me_id,
            platz=self.platz.get(mid),
            punkte=self.totals.get(mid),
            spiele=n,
            teilnahme=round(teilnahme, 2),
            anteile=[round(x, 3) for x in anteile],
            anteile_runde=[round(x, 3) for x in anteile_runde],
            mitlaeufer=round(a, 2),
            mehrheit=round(majority_hits / n, 2) if n else 0.0,
            vorliebe=[round(float(x), 2) for x in b],
            lieblingsergebnisse=[{"ergebnis": e, "anteil": round(c / len(tips_all), 2)} for e, c in fav],
            tore=round(tore, 2),
            punkte_je_spiel=round(float(np.mean(pts)), 2) if pts else None,
            trefferquote=round(hits / judged, 2) if judged else None,
            exakt=round(exact / judged, 2) if judged else None,
            vereine=vereine,
        )
        self.profiles[mid] = prof
        self._params[mid] = (a, b, lifts, teilnahme)

    # ---- Prognose --------------------------------------------------------------------------
    def predict(self, mid: str, home: str, away: str, S) -> np.ndarray:
        a, b, lifts, _ = self._params[mid]
        lift = np.ones(3)
        for team, win in ((home, 0), (away, 2)):
            f = lifts.get(team)
            if f:
                lift[win] *= min(max(f, 0.5), 2.0) ** 0.5  # gedämpft: wenige Spiele je Verein
        return _probs(np.asarray(S, dtype=float), a, b, lift)

    def participation(self, mid: str) -> float:
        return float(self._params[mid][3])

    def others(self) -> list[str]:
        return [mid for mid in self.profiles if mid != self.me_id]

    def crowd_forecast(self, home: str, away: str, S) -> tuple[tuple[float, float, float], float]:
        """Erwartete Tendenz-Anteile der übrigen Tipper und erwartete Zahl aktiver Tipper (inkl. mir)."""
        total = np.zeros(3)
        weight = 0.0
        for mid in self.others():
            p_tip = self._params[mid][3]
            total += p_tip * self.predict(mid, home, away, S)
            weight += p_tip
        if weight <= 0:
            return tuple(float(x) for x in np.asarray(S)), 1.0  # type: ignore[return-value]
        shares = total / weight
        return (float(shares[0]), float(shares[1]), float(shares[2])), 1.0 + weight

    def as_dict(self) -> list[dict]:
        return [asdict(p) for p in sorted(self.profiles.values(), key=lambda p: (p.platz or 999, p.name.lower()))]


def _types(p: Profile, avg_majority: float) -> list[str]:
    out = []
    if p.spiele < 5:
        return ["zu wenig Daten"]
    if p.teilnahme < 0.5:
        out.append("tippt selten")
    if p.mehrheit >= avg_majority + 0.1:
        out.append("Mitläufer")
    elif p.mehrheit <= avg_majority - 0.15:
        out.append("Querdenker")
    if p.anteile[1] >= max(0.25, 1.6 * p.anteile_runde[1]):
        out.append("Remis-Fan")
    elif p.anteile[1] <= 0.05 and p.anteile_runde[1] >= 0.1:
        out.append("tippt nie Remis")
    if p.anteile[0] >= p.anteile_runde[0] + 0.15:
        out.append("Heim-Fan")
    if p.anteile[2] >= p.anteile_runde[2] + 0.15:
        out.append("Auswärts-Fan")
    if p.tore >= 3.6:
        out.append("torreich")
    elif p.tore and p.tore <= 2.2:
        out.append("knapp")
    for v in p.vereine[:1]:
        if v["faktor"] >= 1.6:
            out.append(f"Herz für {v['verein']}")
    return out or ["unauffällig"]
