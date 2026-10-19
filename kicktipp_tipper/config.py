"""Konfiguration aus config.toml und Umgebungsvariablen."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .crowd import CrowdModel, QuoteRule
from .optimizer import StrategyBan
from .scoring import OutcomePoints, ScoringRules


@dataclass
class Config:
    runde: str
    openligadb_liga: str = "bl1"
    saison: int = 2026
    odds_api_sport: str = "soccer_germany_bundesliga"
    rules: ScoringRules = field(default_factory=ScoringRules)
    gewicht_quoten: float = 0.8
    rho: float = -0.08
    halbwertszeit_tage: float = 180
    max_tore_tipp: int = 5
    anzahl_tipper: int = 20
    crowd: CrowdModel = field(default_factory=CrowdModel)
    tippverhalten_lernen: bool = True
    gewicht_rundenquote: float = 0.75
    strategieverbot: StrategyBan | None = None
    aktualisieren_minuten: int = 30
    auto_freigabe_minuten: int = 0
    datenbank: str = "kicktipp-tipper.sqlite"
    kicktipp_user: str | None = None
    kicktipp_password: str | None = None
    odds_api_key: str | None = None
    ui_password: str | None = None


def _outcome(raw: dict | None, draw: bool = False) -> OutcomePoints:
    raw = raw or {}
    tendenz = float(raw.get("tendenz", 2))
    ergebnis = float(raw.get("ergebnis", 4))
    if "tordifferenz" in raw:
        tordifferenz = float(raw["tordifferenz"])
    else:
        # Bei Unentschieden kennt Kicktipp keine Tordifferenz-Wertung.
        tordifferenz = tendenz if draw else 3.0
    return OutcomePoints(tendenz=tendenz, tordifferenz=tordifferenz, ergebnis=ergebnis)


def _fraction(value) -> float:
    if isinstance(value, str) and "/" in value:
        a, b = value.split("/")
        return float(a) / float(b)
    return float(value)


def load_dotenv(path: Path) -> None:
    """Minimaler .env-Leser, damit keine Zusatzabhängigkeit nötig ist."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config(path: str | Path = "config.toml") -> Config:
    path = Path(path)
    load_dotenv(path.parent / ".env")
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    kt = data.get("kicktipp", {})
    wb = data.get("wettbewerb", {})
    pk = data.get("punkte", {})
    md = data.get("modell", {})
    bt = data.get("betrieb", {})
    tr = data.get("tipprunde", {})
    sv = data.get("strategieverbot", {})
    qr = pk.get("quote", {})

    runde = os.environ.get("KICKTIPP_RUNDE") or kt.get("runde")
    if not runde:
        raise SystemExit(f"Keine Tipprunde konfiguriert ([kicktipp] runde in {path} oder KICKTIPP_RUNDE).")

    return Config(
        runde=runde,
        openligadb_liga=wb.get("openligadb_liga", "bl1"),
        saison=int(wb.get("saison", 2026)),
        odds_api_sport=wb.get("odds_api_sport", "soccer_germany_bundesliga"),
        rules=ScoringRules(
            heimsieg=_outcome(pk.get("heimsieg")),
            unentschieden=_outcome(pk.get("unentschieden"), draw=True),
            auswaertssieg=_outcome(pk.get("auswaertssieg")),
            quote=QuoteRule(float(qr.get("min", 3)), float(qr.get("max", 10))) if qr.get("aktiv") else None,
        ),
        anzahl_tipper=int(tr.get("anzahl_tipper", 20)),
        crowd=CrowdModel(float(tr.get("favoriten_exponent", 1.5)), float(tr.get("remis_faktor", 0.7))),
        tippverhalten_lernen=bool(tr.get("tippverhalten_lernen", True)),
        gewicht_rundenquote=float(tr.get("gewicht_rundenquote", 0.75)),
        strategieverbot=StrategyBan(float(_fraction(sv.get("max_anteil", "2/3"))), int(sv.get("ab_spielen", 9)))
        if sv.get("aktiv")
        else None,
        gewicht_quoten=float(md.get("gewicht_quoten", 0.8)),
        rho=float(md.get("rho", -0.08)),
        halbwertszeit_tage=float(md.get("halbwertszeit_tage", 180)),
        max_tore_tipp=int(md.get("max_tore_tipp", 5)),
        aktualisieren_minuten=int(bt.get("aktualisieren_minuten", 30)),
        auto_freigabe_minuten=int(bt.get("auto_freigabe_minuten_vor_anpfiff", 0)),
        datenbank=os.environ.get("KICKTIPP_DB") or bt.get("datenbank", "kicktipp-tipper.sqlite"),
        kicktipp_user=os.environ.get("KICKTIPP_USER") or None,
        kicktipp_password=os.environ.get("KICKTIPP_PASSWORD") or None,
        odds_api_key=os.environ.get("ODDS_API_KEY") or None,
        ui_password=os.environ.get("UI_PASSWORD") or None,
    )
