"""Führt die Quellen zusammen, berechnet Empfehlungen und gibt Tipps frei."""
from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import crowd, model
from .config import Config
from .optimizer import optimize, violations
from .scoring import TipValue, rank_tips
from .sources import oddsapi, openligadb
from .sources.kicktipp import KicktippClient, KicktippMatch, Matchday
from .store import Store
from .teams import best_match

log = logging.getLogger(__name__)

RATINGS_TTL = timedelta(hours=6)
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
    # Strategieverbot
    tip_ohne_verbot: list[int] | None = None
    hinweis: str | None = None


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

    def market(self) -> list[oddsapi.MarketOdds]:
        if not self.cfg.odds_api_key:
            return []
        try:
            return self._fetch_odds(self.cfg.odds_api_key, self.cfg.odds_api_sport)
        except Exception as e:
            log.warning("The Odds API nicht erreichbar: %s", e)
            return []

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
            shares = crowd.others_shares(
                rule,
                m.round_quote,
                model_shares,
                cfg.anzahl_tipper,
                _tendency(current) if current else None,
                cfg.gewicht_rundenquote if m.open else 1.0,
            )
            quote_points = crowd.expected_quote_points(rule, shares, cfg.anzahl_tipper)
            rec.expected_quote = [round(q, 2) for q in quote_points]
            sources.append("Rundenquote (Kicktipp)" if m.round_quote else "Rundenquote geschätzt (noch nicht angezeigt)")

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
        rec.lambdas = [round(lam[0], 2), round(lam[1], 2)]
        if current:
            rec.current_tip_points = round(
                next(t.expected_points for t in ranked if [t.home, t.away] == current)
                if max(current) <= cfg.max_tore_tipp
                else float((probs * cfg.rules.points_matrix(*current, probs.shape[0] - 1, quote_points)).sum()),
                3,
            )
        return rec

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

    def refresh(self, index: int | None = None) -> dict:
        with self._lock:
            md = self.client.matchday(index)
            ratings = self.ratings()
            market = self.market()
            cm = self.crowd_model()
            recs = [self.recommend(m, ratings, market, cm) for m in md.matches]
            self.apply_strategy_ban(recs)
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
                "strategieverbot": {
                    "grenze": ban.cap(len(recs)) if ban else None,
                    "verstoesse_aktuell": violations(
                        [tuple(r.current_tip) for r in recs if r.current_tip], len(recs), ban
                    ),
                },
            }
            self.store.save_snapshot(md.index, self.state)
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


def _top_scores(probs, n: int) -> list[dict]:
    flat = [(probs[h, a], h, a) for h in range(probs.shape[0]) for a in range(probs.shape[1])]
    flat.sort(reverse=True)
    return [{"ergebnis": f"{h}:{a}", "p": round(float(p), 3)} for p, h, a in flat[:n]]
