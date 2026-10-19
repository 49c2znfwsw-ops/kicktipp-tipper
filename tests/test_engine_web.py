"""Engine und Web-API gegen einen nachgebauten Kicktipp-Server (keine echten Netzwerkzugriffe)."""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from kicktipp_tipper import model
from kicktipp_tipper.config import Config
from kicktipp_tipper.engine import Engine
from kicktipp_tipper.sources.kicktipp import KicktippClient
from kicktipp_tipper.store import Store
from kicktipp_tipper.web import create_app

FIX = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, text):
        self.text = text
        self.status_code = 200
        self.url = None

    def raise_for_status(self):
        pass


class FakeKicktipp:
    """Simuliert Login und Tippabgabe-Seite; merkt sich abgegebene Tipps."""

    LOGIN = '<form id="loginFormular" action="/info/profil/loginaction"><input id="kennung" name="kennung"><input id="passwort" name="passwort" type="password"><input type="submit"></form>'

    def __init__(self):
        self.headers = {}
        self.html = (FIX / "tippabgabe.html").read_text()
        self.logged_in = False
        self.posts = []

    def get(self, url, params=None, timeout=None):
        if url.endswith("/login"):
            return FakeResponse(self.LOGIN)
        return FakeResponse(self.html if self.logged_in else self.LOGIN)

    def post(self, url, data=None, timeout=None):
        if url.endswith("loginaction"):
            self.logged_in = data["kennung"] == "u" and data["passwort"] == "p"
            return FakeResponse("ok" if self.logged_in else self.LOGIN)
        self.posts.append(data)
        soup = BeautifulSoup(self.html, "html.parser")
        for name, value in data:
            if re.match(r"spieltippForms\[(\d+)\]\.(heim|gast)Tipp", name):
                soup.find("input", attrs={"name": name})["value"] = value
        self.html = str(soup)
        return FakeResponse(self.html)


def make_engine(tmp_path, **cfg_kw):
    session = FakeKicktipp()
    cfg = Config(runde="meine-runde", datenbank=str(tmp_path / "t.sqlite"), **cfg_kw)
    client = KicktippClient("meine-runde", "u", "p", session=session)
    results = [
        model.Result("Werder Bremen", "VfB Stuttgart", 1, 2, datetime(2026, 9, 1, tzinfo=timezone.utc)),
        model.Result("VfB Stuttgart", "1. FC Köln", 3, 0, datetime(2026, 9, 8, tzinfo=timezone.utc)),
        model.Result("Bayer 04 Leverkusen", "Werder Bremen", 2, 2, datetime(2026, 9, 15, tzinfo=timezone.utc)),
        model.Result("1. FC Köln", "Bayer 04 Leverkusen", 0, 1, datetime(2026, 9, 22, tzinfo=timezone.utc)),
    ]
    engine = Engine(cfg, client, Store(cfg.datenbank), fetch_results=lambda l, s: results if s == cfg.saison else [],
                    fetch_odds=lambda k, s: [])
    return engine, session


def test_refresh_builds_recommendations(tmp_path):
    engine, _ = make_engine(tmp_path)
    state = engine.refresh()
    assert state["spieltag"] == 7
    m = {x["home"]: x for x in state["matches"]}
    koeln = m["1. FC Köln"]
    assert koeln["open"] and koeln["tip"] is not None
    assert koeln["tip"][0] < koeln["tip"][1]  # Leverkusen favorisiert
    assert "Kicktipp-Wettquote" in koeln["sources"] and "Ergebnismodell (OpenLigaDB)" in koeln["sources"]
    assert m["Werder Bremen"]["current_tip"] == [1, 1]
    assert m["Werder Bremen"]["current_tip_points"] is not None
    assert not m["Bayern München"]["open"]


def test_release_submits_and_verifies(tmp_path):
    engine, session = make_engine(tmp_path)
    engine.refresh()
    engine.release(7, {"1002": (1, 2)})
    posted = dict(session.posts[-1])
    assert posted["spieltippForms[1002].heimTipp"] == "1"
    assert posted["spieltippForms[1002].gastTipp"] == "2"
    assert posted["spieltippForms[1001].heimTipp"] == "1"  # bestehender Tipp bleibt erhalten
    assert posted["submitbutton"] == "Tipps speichern"
    m = {x["key"]: x for x in engine.state["matches"]}
    assert m["1002"]["current_tip"] == [1, 2]
    log = engine.store.releases()
    assert log[0]["ok"] == 1 and log[0]["home"] == "1. FC Köln"


def test_release_rejects_closed_match(tmp_path):
    engine, _ = make_engine(tmp_path)
    state = engine.refresh()
    closed_key = next(x["key"] for x in state["matches"] if not x["open"])
    with pytest.raises(Exception, match="nicht mehr tippbar"):
        engine.release(7, {closed_key: (1, 0)})
    assert engine.store.releases()[0]["ok"] == 0


def test_auto_release_due(tmp_path):
    engine, _ = make_engine(tmp_path, auto_freigabe_minuten=60)
    engine.refresh()
    kickoff = datetime.fromisoformat(next(x for x in engine.state["matches"] if x["key"] == "1002")["kickoff"])
    assert engine.auto_release_due(kickoff - timedelta(hours=3)) == {}
    due = engine.auto_release_due(kickoff - timedelta(minutes=30))
    assert list(due) == ["1002"]  # 1001 ist schon getippt


def test_web_api(tmp_path):
    engine, _ = make_engine(tmp_path, ui_password="geheim")
    client = TestClient(create_app(engine, background=False))
    assert client.get("/api/state").status_code == 401
    auth = ("x", "geheim")
    assert client.get("/", auth=auth).status_code == 200
    state = client.post("/api/refresh", json={}, auth=auth).json()
    assert len(state["matches"]) == 3
    r = client.post("/api/release", json={"spieltag": 7, "tips": [{"key": "1002", "heim": 0, "gast": 2}]}, auth=auth)
    assert r.status_code == 200, r.text
    assert client.get("/api/releases", auth=auth).json()[0]["tip_away"] == 2
    bad = client.post("/api/release", json={"spieltag": 7, "tips": [{"key": "9999", "heim": 0, "gast": 2}]}, auth=auth)
    assert bad.status_code == 502


def test_quote_rule_uses_round_quote(tmp_path):
    from kicktipp_tipper.crowd import QuoteRule
    from kicktipp_tipper.scoring import ScoringRules

    engine, _ = make_engine(tmp_path, rules=ScoringRules(quote=QuoteRule(3, 10)), anzahl_tipper=20)
    state = engine.refresh()
    koeln = next(x for x in state["matches"] if x["key"] == "1002")
    assert koeln["round_quote"] == [10, 10, 3]
    assert "Rundenquote (Kicktipp)" in koeln["sources"]
    qh, qd, qa = koeln["expected_quote"]
    assert qa < qd and qa < qh  # Runde tippt Leverkusen -> Auswärtssieg bringt wenig
    # Bei 55 % Siegchance lohnt der Favorit trotz nur ~3 Quotenpunkten noch (0,55*3 > 0,25*8,4)
    assert koeln["tip"][0] < koeln["tip"][1]
    assert state["quellen"]["rundenquote"]


def test_release_blocked_by_strategy_ban(tmp_path):
    from kicktipp_tipper.optimizer import StrategyBan

    engine, session = make_engine(tmp_path, strategieverbot=StrategyBan(0.34, 3))  # höchstens 1 gleiches Ergebnis
    engine.refresh()
    posts = len(session.posts)
    with pytest.raises(ValueError, match="Strategieverbot"):
        engine.release(7, {"1002": (1, 2)})  # 2:1 ist schon getippt (Bayern)
    assert len(session.posts) == posts  # nichts an Kicktipp geschickt
    engine.release(7, {"1002": (0, 2)})
    # Empfehlungen halten das Verbot selbst ein
    tips = [tuple(m["tip"]) for m in engine.state["matches"] if m["open"]] + [(2, 1)]
    from kicktipp_tipper.optimizer import result_class
    assert len({result_class(t) for t in tips}) == len(tips)


def test_login_form_without_id_and_diagnosis(tmp_path, monkeypatch):
    from kicktipp_tipper.sources.kicktipp import KicktippError

    class Other(FakeKicktipp):
        LOGIN = '<form action="/info/profil/loginaction"><input type="email" name="email"><input type="password" name="pw"><input type="checkbox" name="angemeldetBleiben" value="true"></form>'

        def post(self, url, data=None, timeout=None):
            if url.endswith("loginaction"):
                assert data == {"email": "u", "pw": "p", "angemeldetBleiben": "true"}
                self.logged_in = True
                return FakeResponse("ok")
            return super().post(url, data, timeout)

    KicktippClient("r", "u", "p", session=Other()).login()

    class Wall(FakeKicktipp):
        def get(self, url, params=None, timeout=None):
            return FakeResponse("<html>Bitte Cookies akzeptieren</html>")

    monkeypatch.chdir(tmp_path)
    with pytest.raises(KicktippError, match="nicht gefunden"):
        KicktippClient("r", "u", "p", session=Wall()).login()
    assert "Cookies akzeptieren" in (tmp_path / "kicktipp-login-diagnose.html").read_text(encoding="utf-8")


def test_sliders_recompute_and_persist(tmp_path):
    from kicktipp_tipper.crowd import QuoteRule
    from kicktipp_tipper.scoring import ScoringRules

    engine, session = make_engine(tmp_path, rules=ScoringRules(quote=QuoteRule(3, 10)))
    client = TestClient(create_app(engine, background=False))
    client.post("/api/refresh", json={})
    gets_before = len(session.posts)
    before = {m["key"]: m["probs"] for m in engine.state["matches"]}
    r = client.post("/api/einstellungen", json={"werte": {"gewicht_quoten": 0.0, "gewicht_rundenquote": 1.5}}).json()
    assert r["werte"] == {"gewicht_quoten": 0.0, "gewicht_rundenquote": 1.0}  # auf 0..1 begrenzt
    after = {m["key"]: m["probs"] for m in r["state"]["matches"]}
    assert after != before  # nur noch Ergebnismodell
    assert len(session.posts) == gets_before  # kein neuer Abruf/Abgabe bei Kicktipp
    assert not any(m["aenderung"] for m in r["state"]["matches"])
    # Gespeichert: neue Engine mit derselben Datenbank übernimmt die Werte
    engine2 = Engine(engine.cfg.__class__(runde="meine-runde", datenbank=engine.cfg.datenbank), engine.client, Store(engine.cfg.datenbank))
    assert engine2.cfg.gewicht_quoten == 0.0
    back = client.post("/api/einstellungen", json={"werte": None}).json()
    assert back["werte"]["gewicht_quoten"] == 0.8
