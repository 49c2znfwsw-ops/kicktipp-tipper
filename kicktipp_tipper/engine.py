"""Führt die Quellen zusammen, berechnet Empfehlungen und gibt Tipps frei."""
from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

import time

from . import crowd, model
from .profiles import Profiles
from .config import Config
from .optimizer import optimize, violations
from .scoring import TipValue, rank_tips
from .sources import oddsapi, openligadb
from .sources.kicktipp import KicktippClient, KicktippError, KicktippMatch, Matchday
from .store import Store
from .teams import best_match

import numpy as np

log = logging.getLogger(__name__)

RATINGS_TTL = timedelta(hours=6)
HISTORY_TTL = timedelta(hours=6)
SLIDERS = ("gewicht_quoten", "gewicht_rundenquote")  # in der Oberfläche einstellbar
MIN_PROFILES = 5  # ab so vielen profilierten Mitspielern ersetzen die Profile das grobe Rundenmodell
# So lange vor Anpfiff wird häufiger aktualisiert, weil sich die Rundenquote noch bewegt
HOT_WINDOW = timedelta(hours=3)
HOT_INTERVAL_S = 5 * 60


@dataclass
class Recommendation:
    key: str
    home: str
    away: str
    kickoff: str | None
    open: bool
    current_tip: list[int] | None
    tip: list[int] | None
    expected_points: float | None
    current_tip_points: float | None
    alternatives: list[dict] = field(default_factory=list)
    probs: dict | None = None
    lambdas: list[float] | None = None
    sources: list[str] = field(default_factory=list)
    # Quotenregel
    round_quote: list[int] | None = None  # aktuell bei Kicktipp angezeigt (Heim-Remis-Gast)
    expected_quote: list[float] | None = None  # erwartete Tendenzpunkte zum Tippschluss, falls ich so tippe
    runde_basis: list[float] | None = None
    abgegeben: int | None = None  # Tipps, die schon abgegeben sind (verdeckt "-:-"), inkl. meinem
    mitspieler_gesamt: int | None = None  # Tendenz-Anteile der Runde, auf denen die Mitspieler-Prognose aufsetzt
    erwartete_tipper: float | None = None
    # Strategieverbot
    tip_ohne_verbot: list[int] | None = None
    hinweis: str | None = None
    # Empfehlung weicht vom zuletzt bestätigten Stand ab: {"vorher", "seit", "gruende"}
    aenderung: dict | None = None


def _tendency(tip) -> int:
    """0 = Heim, 1 = Remis, 2 = Gast"""
    return 0 if tip[0] > tip[1] else 1 if tip[0] == tip[1] else 2


class Engine:
    def __init__(
        self,
        cfg: Config,
        client: KicktippClient,
        store: Store,
        fetch_results: Callable[[str, int], list[model.Result]] = openligadb.fetch_results,
        fetch_odds: Callable[[str, str], list[oddsapi.MarketOdds]] = oddsapi.fetch_odds,
    ):
        self.cfg = cfg
        self.client = client
        self.store = store
        self._fetch_results = fetch_results
        self._fetch_odds = fetch_odds
        self._ratings: model.TeamRatings | None = None
        self._ratings_at: datetime | None = None
        self._lock = threading.RLock()
        self._ranked: dict[str, list[TipValue]] = {}
        self.state: dict | None = store.latest_snapshot()
        self.me_id: str | None = (self.state or {}).get("ich")
        self._profiles: Profiles | None = None
        self._history_at: datetime | None = None
        self._last_md: Matchday | None = None
        self._submitted: dict[str, set[str]] = {}  # Spiel -> Mitspieler, die schon getippt haben
        self._submitted_quote: dict[str, tuple] = {}  # Rundenquote zum selben Zeitpunkt wie die Zählung
        self._defaults = {k: getattr(cfg, k) for k in SLIDERS}
        for k, v in store.settings().items():
            if k in SLIDERS and v is not None:
                setattr(cfg, k, float(v))

    # ---- Quellen -----------------------------------------------------------------
    def ratings(self) -> model.TeamRatings | None:
        now = datetime.now(timezone.utc)
        if self._ratings_at and now - self._ratings_at < RATINGS_TTL:
            return self._ratings
        results: list[model.Result] = []
        for season in (self.cfg.saison - 1, self.cfg.saison):
            try:
                results += self._fetch_results(self.cfg.openligadb_liga, season)
            except Exception as e:  # Quelle darf ausfallen
                log.warning("OpenLigaDB %s/%s nicht erreichbar: %s", self.cfg.openligadb_liga, season, e)
        self._ratings = model.fit_ratings(results, self.cfg.halbwertszeit_tage) if results else self._ratings
        self._ratings_at = now
        return self._ratings

    def crowd_model(self) -> crowd.CrowdModel:
        """Tippverhalten der Runde: aus beobachteten Rundenquoten gelernt, sonst Startwerte."""
        rule = self.cfg.rules.quote
        if rule and self.cfg.tippverhalten_lernen:
            obs = [(p, crowd.shares_from_quote(rule, q)) for p, q in self.store.crowd_obs()]
            fitted = crowd.fit_crowd(obs)
            if fitted:
                return fitted
        return self.cfg.crowd

    # ---- Empfehlung je Spiel -------------------------------------------------------
    def recommend(
        self,
        m: KicktippMatch,
        ratings: model.TeamRatings | None,
        market: list[oddsapi.MarketOdds],
        crowd_model: crowd.CrowdModel | None = None,
    ) -> Recommendation:
        cfg = self.cfg
        sources: list[str] = []

        lam_market = None
        ev = self._find_market(m, market)
        if ev:
            lam_market = model.lambdas_from_market(ev.p_home, ev.p_draw, ev.p_away, ev.p_over25, cfg.rho)
            sources.append(f"Quoten ({ev.bookmakers} Buchmacher{', inkl. Über/Unter' if ev.p_over25 else ''})")
        elif m.odds:
            ph, pd, pa = model.devig(list(m.odds))
            lam_market = model.lambdas_from_market(ph, pd, pa, None, cfg.rho)
            sources.append("Kicktipp-Wettquote")

        lam_model = None
        if ratings:
            names = list(ratings.attack)
            h, a = best_match(m.home, names), best_match(m.away, names)
            if h and a:
                lam_model = ratings.lambdas(h, a)
                sources.append("Ergebnismodell (OpenLigaDB)")

        lam = model.blend_lambdas(lam_market, lam_model, cfg.gewicht_quoten)
        current = [m.tip_home, m.tip_away] if m.tip_home is not None and m.tip_away is not None else None
        rec = Recommendation(
            key=m.key,
            home=m.home,
            away=m.away,
            kickoff=m.kickoff.isoformat() if m.kickoff else None,
            open=m.open,
            current_tip=current,
            tip=None,
            expected_points=None,
            current_tip_points=None,
            sources=sources,
            round_quote=list(m.round_quote) if m.round_quote else None,
        )
        if lam is None:
            return rec
        probs = model.score_matrix(lam[0], lam[1], cfg.rho)
        ph, pd, pa = model.outcome_probs(probs)

        quote_points = (0.0, 0.0, 0.0)
        rule = cfg.rules.quote
        if rule:
            model_shares = (crowd_model or cfg.crowd).shares(ph, pd, pa)
            profiles = self.profiles()
            have_profiles = profiles is not None and len(profiles.others()) >= MIN_PROFILES
            submitted = self._submitted.get(m.key)
            if submitted is not None:
                rec.abgegeben = len(submitted)
                rec.mitspieler_gesamt = (len(profiles.others()) + 1) if profiles else None
            if submitted is not None and have_profiles and m.round_quote:
                quote_points = self._exact_quote(m, rec, profiles, submitted, current, model_shares, rule)
                sources.append(f"Rundenquote exakt ({len(submitted)} Tipps abgegeben)")
                sources.append(f"Mitspieler-Profile (≈{rec.erwartete_tipper:.0f} Tipper bis Anpfiff)")
            else:
                n_tippers = cfg.anzahl_tipper
                if have_profiles:
                    # Prognose je Mitspieler, aufgesetzt auf den aktuellen Stand der Runde (sonst Wettquoten-Modell)
                    basis = crowd.shares_from_quote(rule, m.round_quote) if m.round_quote else model_shares
                    model_shares, expected_n = profiles.crowd_forecast(m.home, m.away, basis)
                    n_tippers = max(2, round(expected_n))
                    rec.runde_basis = [round(x, 3) for x in basis]
                    rec.erwartete_tipper = round(expected_n, 1)
                    sources.append(f"Mitspieler-Profile (≈{expected_n:.0f} aktive Tipper)")
                shares = crowd.others_shares(
                    rule,
                    m.round_quote,
                    model_shares,
                    n_tippers,
                    _tendency(current) if current else None,
                    cfg.gewicht_rundenquote if m.open else 1.0,
                )
                quote_points = crowd.expected_quote_points(rule, shares, n_tippers)
                sources.append("Rundenquote (Kicktipp)" if m.round_quote else "Rundenquote geschätzt (noch nicht angezeigt)")
            rec.expected_quote = [round(q, 2) for q in quote_points]

        ranked = rank_tips(probs, cfg.rules, cfg.max_tore_tipp, quote_points)
        self._ranked[m.key] = ranked
        best = ranked[0]
        rec.tip = [best.home, best.away]
        rec.expected_points = round(best.expected_points, 3)
        rec.alternatives = [{"tip": t.label, "ev": round(t.expected_points, 3)} for t in ranked[1:6]]
        rec.probs = {
            "heim": round(ph, 3),
            "remis": round(pd, 3),
            "gast": round(pa, 3),
            "top_ergebnisse": _top_scores(probs, 5),
        }
        rec.lambdas = [round(lam[0], 6), round(lam[1], 6)]
        if current:
            rec.current_tip_points = round(
                next(t.expected_points for t in ranked if [t.home, t.away] == current)
                if max(current) <= cfg.max_tore_tipp
                else float((probs * cfg.rules.points_matrix(*current, probs.shape[0] - 1, quote_points)).sum()),
                3,
            )
        return rec

    def _exact_quote(self, m, rec, profiles, submitted, current, model_shares, rule):
        """Rundenquote mit bekannter Zahl abgegebener Tipps: Bestand exakt, nur Fehlende werden prognostiziert."""
        n_now = len(submitted)
        quote = self._submitted_quote.get(m.key) or m.round_quote
        counts = crowd.exact_counts(rule, quote, n_now, prior=model_shares)
        if counts is None:  # Quote passt zu keiner Verteilung (z. B. Stand dazwischen geändert): Näherung
            counts = np.array(crowd.shares_from_quote(rule, quote)) * n_now
        others = counts.copy()
        if current and self.me_id in submitted:
            others[_tendency(current)] = max(0.0, others[_tendency(current)] - 1)  # mein Tipp zählt nicht doppelt
        now_shares = others / others.sum() if others.sum() > 0 else np.array(model_shares)
        w = self.cfg.gewicht_rundenquote
        remaining = []
        if m.open:
            for mid in profiles.others():
                if mid in submitted:
                    continue
                pred = profiles.predict(mid, m.home, m.away, now_shares)
                # Schieber: Fehlende tippen wie die, die schon getippt haben (links), oder wie ihr Profil (rechts)
                remaining.append((profiles.participation(mid), w * now_shares + (1 - w) * pred))
        rec.runde_basis = [round(float(x), 3) for x in now_shares]
        if hasattr(rec, "round_quote"):
            rec.round_quote = list(quote)  # die zur Zählung passende, aktuellere Quote anzeigen
        rec.erwartete_tipper = round(float(others.sum()) + sum(p for p, _ in remaining) + 1, 1)
        return crowd.expected_quote_points_exact(rule, others, remaining)

    @staticmethod
    def _find_market(m: KicktippMatch, market: list[oddsapi.MarketOdds]) -> oddsapi.MarketOdds | None:
        for ev in market:
            if best_match(m.home, [ev.home]) and best_match(m.away, [ev.away]):
                if m.kickoff is None or abs((ev.kickoff - m.kickoff).total_seconds()) < 36 * 3600:
                    return ev
        return None

    # ---- Spieltag gemeinsam (Strategieverbot) --------------------------------------
    def apply_strategy_ban(self, recs: list[Recommendation], keep_current: set[str] = frozenset()) -> None:
        """Tipps der offenen Spiele gemeinsam wählen. keep_current: offene Spiele, deren Tipp feststeht."""
        ban = self.cfg.strategieverbot
        fixed = [tuple(r.current_tip) for r in recs if r.current_tip and (not r.open or r.key in keep_current)]
        options = {
            r.key: [((t.home, t.away), t.expected_points) for t in self._ranked.get(r.key, [])]
            for r in recs
            if r.open and r.key not in keep_current
        }
        chosen = optimize(options, fixed, len(recs), ban)
        for r in recs:
            if r.key not in chosen or r.tip is None:
                continue
            tip = list(chosen[r.key])
            if tip != r.tip:
                ev = next(t.expected_points for t in self._ranked[r.key] if [t.home, t.away] == tip)
                r.tip_ohne_verbot = r.tip
                r.hinweis = (
                    f"Strategieverbot: {r.tip[0]}:{r.tip[1]} wäre {r.expected_points - ev:.2f} Pkt. besser, "
                    "aber dieses Ergebnis ist am Spieltag schon ausgeschöpft."
                )
                r.tip = tip
                r.expected_points = round(ev, 3)
                r.alternatives = [
                    {"tip": t.label, "ev": round(t.expected_points, 3)}
                    for t in self._ranked[r.key]
                    if [t.home, t.away] != tip
                ][:5]

    # ---- Historie und Mitspieler-Profile --------------------------------------------
    def profiles(self) -> Profiles | None:
        if self._profiles is None:
            history = self.store.history()
            if history["tips"]:
                self._profiles = Profiles(history, self.me_id)
        return self._profiles

    def load_history(self, force: bool = False, pause_s: float = 0.5) -> dict:
        """Tippübersichten aller bisherigen Spieltage laden; abgeschlossene nur einmal."""
        with self._lock:
            current = (self.state or {}).get("spieltag")
            if current is None:
                current = self.client.matchday().index
            loaded = self.store.loaded_spieltage()
            fetched, errors = [], []
            for idx in range(1, (current or 0) + 1):
                if not force and loaded.get(idx, {}).get("complete"):
                    continue
                try:
                    ov = self.client.tippuebersicht(idx)
                except KicktippError as e:
                    errors.append(f"Spieltag {idx}: {e}")
                    continue
                complete = bool(ov.matches) and all(m.result for m in ov.matches)
                self.store.save_overview(idx, ov, complete)
                fetched.append(idx)
                if pause_s:
                    time.sleep(pause_s)  # Kicktipp nicht mit Anfragen überhäufen
            self._profiles = None
            self._history_at = datetime.now(timezone.utc)
            return {"geladen": fetched, "fehler": errors, "spieltage": sorted(self.store.loaded_spieltage())}

    def _save_predictions(self, spieltag: int | None, recs: list[Recommendation]) -> None:
        """Mitspieler-Prognose offener Spiele merken – der letzte Stand vor Anpfiff wird später abgeglichen."""
        profiles = self.profiles()
        if profiles is None or len(profiles.others()) < MIN_PROFILES:
            return
        per_member, crowd_fc = {}, {}
        for r in recs:
            if not r.open or not r.runde_basis:
                continue
            for mid in profiles.others():
                per_member[(r.key, mid)] = [round(float(x), 4) for x in profiles.predict(mid, r.home, r.away, r.runde_basis)]
            shares, n = profiles.crowd_forecast(r.home, r.away, r.runde_basis)
            crowd_fc[r.key] = (list(shares), n)
        self.store.save_predictions(spieltag, per_member, crowd_fc)

    def abgleich(self, spieltag: int | None = None) -> dict:
        """Prognose je Mitspieler gegen die tatsächlich abgegebenen Tipps eines vergangenen Spieltags.

        Gespeicherte Prognosen (letzter Stand vor Anpfiff) haben Vorrang. Fehlen sie – etwa für Spieltage
        vor Einführung dieser Funktion –, wird nachträglich gerechnet: Profile nur aus den Spieltagen davor,
        als Ausgangslage die endgültige Rundenquote ohne den eigenen Tipp des Mitspielers.
        """
        history = self.store.history()
        loaded = self.store.loaded_spieltage()
        with_tips = sorted({t["spieltag"] for t in history["tips"]})
        if not with_tips:
            return {"spieltag": None, "spieltage": []}
        if spieltag is None:
            complete = [s for s in with_tips if loaded.get(s, {}).get("complete")]
            spieltag = (complete or with_tips)[-1]
        matches = [m for m in history["matches"] if m["spieltag"] == spieltag]
        tips: dict[str, dict[str, tuple]] = {}
        for t in history["tips"]:
            if t["spieltag"] == spieltag:
                tips.setdefault(t["match_key"], {})[t["member_id"]] = tuple(t["tip"])
        stored, stored_crowd = self.store.predictions([m["key"] for m in matches])
        prior = {
            "matches": [m for m in history["matches"] if m["spieltag"] < spieltag],
            "tips": [t for t in history["tips"] if t["spieltag"] < spieltag],
            "members": [r for r in history["members"] if r["spieltag"] < spieltag],
        }
        backtest = Profiles(prior, self.me_id) if prior["tips"] else None
        names = {r["member_id"]: r["name"] for r in history["members"] if r["spieltag"] == spieltag}
        member_ids = [mid for mid in names if mid != self.me_id]
        rule = self.cfg.rules.quote or crowd.QuoteRule()

        spiele, cells = [], {mid: [] for mid in member_ids}
        quellen = set()
        hits = base_hits = judged = 0
        p_actual = []
        for m in matches:
            actual = tips.get(m["key"], {})
            counts = np.zeros(3)
            for tip in actual.values():
                counts[_tendency(tip)] += 1
            fc = stored_crowd.get(m["key"])
            spiele.append({
                "key": m["key"], "home": m["home"], "away": m["away"], "ergebnis": m["result"],
                "quote_ist": m["round_quote"], "tipper_ist": len(actual),
                "quote_prognose": [int(rule.points(max(1, round(s * fc[1])), max(1, round(fc[1])))) for s in fc[0]] if fc else None,
                "tipper_prognose": round(fc[1], 1) if fc else None,
            })
            for mid in member_ids:
                tip = actual.get(mid)
                pred = stored.get((m["key"], mid))
                src = "gespeichert" if pred else None
                if pred is None and backtest and mid in backtest.profiles and tip is not None:
                    own = counts.copy()
                    own[_tendency(tip)] -= 1
                    if own.sum() > 0:
                        pred = backtest.predict(mid, m["home"], m["away"], own / own.sum()).tolist()
                        src = "nachträglich"
                cell = {"p": [round(float(x), 3) for x in pred] if pred else None, "tipp": list(tip) if tip else None,
                        "treffer": None, "mehrheit": None}
                if pred and tip:
                    t_idx = _tendency(tip)
                    others = counts.copy()
                    others[t_idx] -= 1
                    cell["treffer"] = int(np.argmax(pred)) == t_idx
                    cell["mehrheit"] = int(np.argmax(others)) == t_idx if others.sum() else None
                    hits += cell["treffer"]
                    base_hits += bool(cell["mehrheit"])
                    judged += 1
                    p_actual.append(pred[t_idx])
                    quellen.add(src)
                cells[mid].append(cell)

        tipper = []
        for mid in member_ids:
            row = cells[mid]
            j = [c for c in row if c["treffer"] is not None]
            tipper.append({"member_id": mid, "name": names[mid], "zellen": row,
                           "treffer": sum(c["treffer"] for c in j), "bewertet": len(j),
                           "getippt": sum(1 for c in row if c["tipp"]),
                           "teilnahme_prognose": backtest.profiles[mid].teilnahme if backtest and mid in backtest.profiles else None})
        # nach aktueller Gesamtplatzierung (letzter geladener Stand), bei Gleichstand nach Punkten des Spieltags
        current = self.profiles()
        for r in tipper:
            prof = current.profiles.get(r["member_id"]) if current else None
            r["platz"] = prof.platz if prof else None
        tipper.sort(key=lambda r: (r["platz"] if r["platz"] is not None else 999, r["name"].lower()))
        return {
            "spieltag": spieltag, "spieltage": with_tips,
            "quelle": "gemischt" if len(quellen) > 1 else (quellen.pop() if quellen else None),
            "spiele": spiele, "tipper": tipper,
            "zusammenfassung": {
                "bewertet": judged,
                "modell_treffer": hits / judged if judged else None,
                "mehrheit_treffer": base_hits / judged if judged else None,
                "p_tatsaechlich": float(np.mean(p_actual)) if p_actual else None,
            },
        }

    def history_due(self) -> bool:
        return self._history_at is None or datetime.now(timezone.utc) - self._history_at > HISTORY_TTL

    def mitspieler(self) -> dict:
        """Profile plus Prognose je Mitspieler für die offenen Spiele des aktuellen Spieltags."""
        profiles = self.profiles()
        if profiles is None:
            return {"profile": [], "prognosen": [], "spieltage": sorted(self.store.loaded_spieltage()), "ich": self.me_id}
        rule = self.cfg.rules.quote or crowd.QuoteRule()
        prognosen = []
        for m in (self.state or {}).get("matches", []):
            if not m["open"] or not m["probs"]:
                continue
            basis = m.get("runde_basis") or list(
                crowd.shares_from_quote(rule, m["round_quote"]) if m.get("round_quote")
                else self.cfg.crowd.shares(m["probs"]["heim"], m["probs"]["remis"], m["probs"]["gast"])
            )
            prognosen.append({
                "key": m["key"], "home": m["home"], "away": m["away"],
                "abgegeben": sorted(self._submitted.get(m["key"], set())),
                "tipper": {mid: [round(float(x), 3) for x in profiles.predict(mid, m["home"], m["away"], basis)]
                           for mid in profiles.others()},
            })
        return {"profile": profiles.as_dict(), "prognosen": prognosen,
                "spieltage": sorted(self.store.loaded_spieltage()), "ich": self.me_id}

    def verlauf(self) -> dict:
        """Kumulierte Punkte, Spieltagspunkte und Platz je Mitspieler und Spieltag."""
        rows = self.store.history()["members"]
        spieltage = sorted({r["spieltag"] for r in rows})
        series: dict[str, dict] = {}
        for r in rows:
            s = series.setdefault(r["member_id"], {"member_id": r["member_id"], "name": r["name"],
                                                   "ich": r["member_id"] == self.me_id,
                                                   "gesamt": [None] * len(spieltage),
                                                   "spieltag": [None] * len(spieltage),
                                                   "platz": [None] * len(spieltage)})
            i = spieltage.index(r["spieltag"])
            s["gesamt"][i], s["spieltag"][i], s["name"] = r["total"], r["day_points"], r["name"]
        for i in range(len(spieltage)):
            ranked = sorted((s for s in series.values() if s["gesamt"][i] is not None), key=lambda s: -s["gesamt"][i])
            pos, prev = 0, None
            for n, s in enumerate(ranked, 1):
                if s["gesamt"][i] != prev:
                    pos, prev = n, s["gesamt"][i]
                s["platz"][i] = pos
        return {"spieltage": spieltage, "reihen": sorted(series.values(), key=lambda s: s["platz"][-1] or 999) if spieltage else []}

    # ---- Odds API (nur auf Klick, Reiter "Quoten") ---------------------------------
    def odds_api_key(self) -> str | None:
        return self.store.settings().get("odds_api_key") or self.cfg.odds_api_key

    def set_odds_api_key(self, key: str | None) -> None:
        self.store.save_settings({"odds_api_key": (key or "").strip() or None})

    # ---- Einstellungen (Schieber) --------------------------------------------------
    def settings(self) -> dict:
        return {"werte": {k: getattr(self.cfg, k) for k in SLIDERS}, "standard": dict(self._defaults)}

    def update_settings(self, values: dict | None) -> dict:
        """values=None setzt auf die Werte aus config.toml zurück. Rechnet mit dem letzten Spieltag neu."""
        with self._lock:
            if values is None:
                self.store.clear_settings(SLIDERS)  # Schlüssel und Quotenabruf bleiben erhalten
                values = dict(self._defaults)
            else:
                values = {k: min(1.0, max(0.0, float(v))) for k, v in values.items() if k in SLIDERS}
                self.store.save_settings(values)
            for k, v in values.items():
                setattr(self.cfg, k, v)
            if self._last_md is not None:
                self._compute(self._last_md)
                self.mark_seen()  # geänderte Einstellung ist keine "neue" Empfehlung
            return self.settings()

    def refresh(self, index: int | None = None) -> dict:
        with self._lock:
            md = self.client.matchday(index)
            if md.tipper_id and md.tipper_id != self.me_id:
                self.me_id = md.tipper_id
                self._profiles = None
            self._submitted, self._submitted_quote = self._load_submitted(md)
            return self._compute(md)

    def _load_submitted(self, md: Matchday) -> tuple[dict[str, set[str]], dict[str, tuple]]:
        """Wer hat schon getippt? Die Tippübersicht zeigt abgegebene Tipps vor Anpfiff verdeckt als "-:-".

        Die Rundenquote kommt aus derselben Seite, damit Anzahl und Quote zum selben Zeitpunkt gehören.
        """
        if md.index is None or not any(m.open for m in md.matches):
            return {}, {}
        try:
            ov = self.client.tippuebersicht(md.index)
        except Exception as e:
            log.info("Abgegebene Tipps nicht ermittelbar: %s", e)
            return {}, {}
        submitted = {
            hm.key: {mb.member_id for mb in ov.members if i < len(mb.abgegeben) and mb.abgegeben[i]}
            for i, hm in enumerate(ov.matches)
        }
        quotes = {hm.key: hm.round_quote for hm in ov.matches if hm.round_quote}
        return submitted, quotes

    def _compute(self, md: Matchday) -> dict:
        with self._lock:
            self._last_md = md
            ratings = self.ratings()
            market: list = []  # Odds API nur auf Klick im Reiter "Quoten" – fließt nicht in die Prognose ein
            cm = self.crowd_model()
            recs = [self.recommend(m, ratings, market, cm) for m in md.matches]
            self.apply_strategy_ban(recs)
            self._mark_changes(recs)
            self._save_predictions(md.index, recs)
            self.store.save_recommendations(
                md.index, {r.key: {"tip": r.tip, "ev": r.expected_points} for r in recs if r.open and r.tip}
            )
            self._learn_crowd(md, recs)
            ban = self.cfg.strategieverbot
            self.state = {
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "runde": self.cfg.runde,
                "spieltag": md.index,
                "matches": [asdict(r) for r in recs],
                "quellen": {
                    "ergebnismodell": ratings is not None,
                    "odds_api": bool(market),
                    "kicktipp_quoten": any(m.odds for m in md.matches),
                    "rundenquote": any(m.round_quote for m in md.matches),
                },
                "tippverhalten": asdict(cm),
                "ich": self.me_id,
                "strategieverbot": {
                    "grenze": ban.cap(len(recs)) if ban else None,
                    "verstoesse_aktuell": violations(
                        [tuple(r.current_tip) for r in recs if r.current_tip], len(recs), ban
                    ),
                },
            }
            self.store.save_snapshot(md.index, self.state)
            return self.state

    # ---- Änderungen seit dem letzten Bestätigen ------------------------------------
    @staticmethod
    def _seen_record(r: Recommendation | dict) -> dict:
        get = r.get if isinstance(r, dict) else lambda k: getattr(r, k)
        return {"tip": get("tip"), "round_quote": get("round_quote"), "probs": get("probs"), "hinweis": get("hinweis")}

    def _mark_changes(self, recs: list[Recommendation]) -> None:
        seen = self.store.seen()
        baseline = {}
        for r in recs:
            if not r.tip:
                continue
            old = seen.get(r.key)
            if old is None:
                baseline[r.key] = self._seen_record(r)  # erstes Mal gesehen: Ausgangsstand
            elif old["tip"] != r.tip and r.open:
                r.aenderung = {"vorher": old["tip"], "seit": old["seen_at"], "gruende": _change_reasons(old, r)}
        if baseline:
            self.store.set_seen(baseline)

    def mark_seen(self, keys: list[str] | None = None) -> dict | None:
        """Aktuelle Empfehlungen als gesehen bestätigen (alle oder nur die genannten Spiele)."""
        with self._lock:
            if not self.state:
                return None
            items = {}
            for m in self.state["matches"]:
                if m["tip"] and (keys is None or m["key"] in keys):
                    items[m["key"]] = self._seen_record(m)
                    m["aenderung"] = None
            self.store.set_seen(items)
            self.store.save_snapshot(self.state.get("spieltag"), self.state)
            return self.state

    def _learn_crowd(self, md: Matchday, recs: list[Recommendation]) -> None:
        """Endgültige Rundenquoten abgelaufener Spiele merken, um das Tippverhalten der Runde zu lernen."""
        now = datetime.now(timezone.utc)
        for m, r in zip(md.matches, recs):
            if m.open or not m.round_quote or not r.probs or not m.kickoff or m.kickoff > now:
                continue
            key = f"{md.index}:{m.home}:{m.away}:{m.kickoff.date()}"
            self.store.save_crowd_obs(key, (r.probs["heim"], r.probs["remis"], r.probs["gast"]), m.round_quote)

    # ---- Freigabe ------------------------------------------------------------------
    def check_release(self, tips: dict[str, tuple[int, int]]) -> None:
        """Prüft das Strategieverbot für den Spieltag, wie er nach der Abgabe aussähe."""
        matches = (self.state or {}).get("matches", [])
        final = []
        for m in matches:
            t = tips.get(m["key"]) or m["current_tip"]
            if t:
                final.append(tuple(t))
        bad = violations(final, len(matches), self.cfg.strategieverbot)
        if bad:
            cap = self.cfg.strategieverbot.cap(len(matches))
            detail = ", ".join(f"{k} ({v}×)" for k, v in bad.items())
            raise ValueError(f"Strategieverbot: höchstens {cap} gleiche Ergebnisse erlaubt, wäre {detail}.")

    def release(self, index: int | None, tips: dict[str, tuple[int, int]], mode: str = "manuell") -> Matchday:
        with self._lock:
            names = {m["key"]: (m["home"], m["away"]) for m in (self.state or {}).get("matches", [])}
            try:
                self.check_release(tips)
                after = self.client.submit(index, tips)
            except Exception as e:
                for k, (h, a) in tips.items():
                    self.store.log_release(index, k, *names.get(k, ("?", "?")), h, a, mode, False, str(e))
                raise
            for k, (h, a) in tips.items():
                self.store.log_release(after.index, k, *names.get(k, ("?", "?")), h, a, mode, True)
            self.refresh(after.index)
            if mode == "manuell":
                self.mark_seen(list(tips))  # wer freigibt, hat die Empfehlung gesehen
            return after

    def auto_release_due(self, now: datetime | None = None) -> dict[str, tuple[int, int]]:
        """Tipps, die das Sicherheitsnetz jetzt abgeben würde (offen, ungetippt, Anpfiff naht)."""
        if not self.cfg.auto_freigabe_minuten or not self.state:
            return {}
        now = now or datetime.now(timezone.utc)
        horizon = now + timedelta(minutes=self.cfg.auto_freigabe_minuten)
        due = {}
        for m in self.state["matches"]:
            if not m["open"] or m["current_tip"] or not m["tip"] or not m["kickoff"]:
                continue
            if now < datetime.fromisoformat(m["kickoff"]) <= horizon:
                due[m["key"]] = tuple(m["tip"])
        if due:
            # Bereits getippte Spiele bleiben, wie sie sind -> Strategieverbot mit diesen als fest neu lösen
            matches = self.state["matches"]
            recs = [Recommendation(**m) for m in matches]
            keep = {m["key"] for m in matches if m["open"] and m["current_tip"]}
            self.apply_strategy_ban(recs, keep_current=keep)
            due = {k: tuple(r.tip) for r in recs for k in due if r.key == k and r.tip}
        return due

    def tick(self) -> None:
        # Ohne Index zeigt Kicktipp den aktuellen bzw. nächsten Spieltag
        self.refresh(None)
        if self.history_due():
            try:
                self.load_history()
                self.refresh(None)  # Empfehlungen mit frischen Profilen
            except Exception as e:
                log.warning("Tipp-Historie nicht geladen: %s", e)
        due = self.auto_release_due()
        if due:
            log.info("Automatische Freigabe für %d Spiel(e)", len(due))
            self.release(self.state["spieltag"], due, mode="automatisch")

    def next_interval(self, now: datetime | None = None) -> float:
        interval = self.cfg.aktualisieren_minuten * 60
        if self.cfg.auto_freigabe_minuten:
            interval = min(interval, max(60, self.cfg.auto_freigabe_minuten * 60 // 3))
        now = now or datetime.now(timezone.utc)
        for m in (self.state or {}).get("matches", []):
            if m["open"] and m["kickoff"] and now < datetime.fromisoformat(m["kickoff"]) <= now + HOT_WINDOW:
                interval = min(interval, HOT_INTERVAL_S)
        return interval

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.tick()
            except Exception as e:
                log.warning("Aktualisierung fehlgeschlagen: %s", e)
                log.debug("Details", exc_info=True)
            stop.wait(self.next_interval())


def _change_reasons(old: dict, r: Recommendation) -> list[str]:
    reasons = []
    oq, nq = old.get("round_quote"), r.round_quote
    if oq and nq and list(oq) != list(nq):
        reasons.append(f"Rundenquote {'-'.join(map(str, oq))} → {'-'.join(map(str, nq))}")
    op, np_ = old.get("probs"), r.probs
    if op and np_:
        before = [round(op[k] * 100) for k in ("heim", "remis", "gast")]
        after = [round(np_[k] * 100) for k in ("heim", "remis", "gast")]
        if max(abs(a - b) for a, b in zip(before, after)) >= 2:
            reasons.append(f"Wahrscheinlichkeiten H/U/A {'/'.join(map(str, before))} % → {'/'.join(map(str, after))} %")
    if bool(old.get("hinweis")) != bool(r.hinweis):
        reasons.append("Strategieverbot (andere Tipps des Spieltags)")
    return reasons or ["Neuberechnung (z. B. neue Ergebnisdaten)"]


def _top_scores(probs, n: int) -> list[dict]:
    flat = [(probs[h, a], h, a) for h in range(probs.shape[0]) for a in range(probs.shape[1])]
    flat.sort(reverse=True)
    return [{"ergebnis": f"{h}:{a}", "p": round(float(p), 3)} for p, h, a in flat[:n]]
