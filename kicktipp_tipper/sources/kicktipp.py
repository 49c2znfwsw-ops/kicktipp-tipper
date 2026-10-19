"""Zugriff auf kicktipp.de über die normale Web-Oberfläche (es gibt keine offizielle API).

Liest die Tippabgabe-Seite eines Spieltags (Spiele, Anstoß, eigene Tipps, ggf. Wettquoten)
und schickt das Tippabgabe-Formular ab. Wenn Kicktipp sein HTML ändert, ist hier anzupassen.
"""
from __future__ import annotations

import re
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag

BASE = "https://www.kicktipp.de"
LOGIN_URL = f"{BASE}/info/profil/login"
BERLIN = ZoneInfo("Europe/Berlin")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}

_ODDS_RE = re.compile(r"(\d+[,.]\d+)\s*/\s*(\d+[,.]\d+)\s*/\s*(\d+[,.]\d+)")
# Rundenquote der Quoten-Punkteregel, angezeigt als "Heim-Remis-Gast", z. B. "3-7-10"
_ROUND_QUOTE_RE = re.compile(r"(?<![\d.,:/])(\d{1,2})\s*-\s*(\d{1,2})\s*-\s*(\d{1,2})(?![\d.,:/])")
_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{2,4})\s+(\d{1,2}):(\d{2})")


class KicktippError(RuntimeError):
    pass


@dataclass
class KicktippMatch:
    key: str  # Formular-Schlüssel des Spiels, z. B. "1234567" aus spieltippForms[1234567]
    home: str
    away: str
    kickoff: datetime | None
    open: bool  # noch tippbar
    tip_home: int | None = None
    tip_away: int | None = None
    odds: tuple[float, float, float] | None = None  # Buchmacher-Quote 1 / X / 2
    round_quote: tuple[int, int, int] | None = None  # aktuelle Rundenquote Heim-Remis-Gast (Punkte)
    home_field: str | None = None
    away_field: str | None = None


@dataclass
class Matchday:
    index: int | None
    matches: list[KicktippMatch]
    form_action: str | None
    form_fields: list[tuple[str, str]] = field(default_factory=list)  # alle übrigen Formularfelder


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def _parse_kickoff(text: str, last: datetime | None) -> datetime | None:
    m = _DATE_RE.search(text)
    if not m:
        return last  # Kicktipp lässt die Zeit weg, wenn sie der vorigen Zeile entspricht
    d, mo, y, h, mi = m.groups()
    year = int(y) + (2000 if len(y) == 2 else 0)
    return datetime(year, int(mo), int(d), int(h), int(mi), tzinfo=BERLIN)


def _form_key(name: str) -> str | None:
    m = re.match(r"spieltippForms\[(\d+)\]", name)
    return m.group(1) if m else None


# Spaltenpositionen, falls die Tabelle keine erkennbare Überschrift hat
_DEFAULT_COLUMNS = {"termin": 0, "heim": 1, "gast": 2, "punkteregel": 3, "tipp": 4}


def _column_map(table: Tag) -> dict[str, int]:
    """Spalten über die Überschrift erkennen (name-Attribut oder Text der <th>)."""
    head = table.find("thead") or table
    header_row = next((r for r in head.find_all("tr") if r.find("th")), None)
    if header_row is None:
        return dict(_DEFAULT_COLUMNS)
    found: dict[str, int] = {}
    for idx, th in enumerate(header_row.find_all(["th", "td"], recursive=False)):
        label = f"{th.get('name', '')} {th.get_text(' ', strip=True)}".lower()
        for role, words in (
            ("termin", ("tipptermin", "termin", "datum")),
            ("heim", ("heim",)),
            ("gast", ("gast", "auswärts")),
            ("punkteregel", ("punkteregel", "punkte")),
            ("tipp", ("tipp",)),
        ):
            if role not in found and any(w in label.split() or label.startswith(w) for w in words):
                found[role] = idx
                break
    if "heim" not in found or "gast" not in found:
        return dict(_DEFAULT_COLUMNS)
    return found


def _cell(row: Tag, tds: list[Tag], columns: dict[str, int], role: str, css_class: str) -> Tag | None:
    """Zelle per CSS-Klasse (vom Browser-JavaScript gesetzt) oder per Spaltenposition (Server-HTML)."""
    td = row.find("td", class_=css_class)
    if isinstance(td, Tag):
        return td
    idx = columns.get(role)
    return tds[idx] if idx is not None and idx < len(tds) else None


def parse_tippabgabe(html: str) -> Matchday:
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", id="tippabgabeForm")
    table = soup.find("table", id="tippabgabeSpiele")
    if table is None:
        raise KicktippError("Tippabgabe-Tabelle nicht gefunden – Runde falsch, nicht eingeloggt oder Layout geändert.")

    matches: list[KicktippMatch] = []
    used_fields: set[str] = set()
    last_kickoff = None
    columns = _column_map(table)
    for i, row in enumerate(table.find_all("tr")):
        tds = row.find_all("td", recursive=False)
        home_td = _cell(row, tds, columns, "heim", "col1")
        away_td = _cell(row, tds, columns, "gast", "col2")
        if not isinstance(home_td, Tag) or not isinstance(away_td, Tag):
            continue
        home, away = home_td.get_text(" ", strip=True), away_td.get_text(" ", strip=True)
        if not home or not away:
            continue
        row_text = row.get_text(" ", strip=True)
        last_kickoff = _parse_kickoff(row_text, last_kickoff)

        inputs = [inp for inp in row.find_all("input") if inp.get("name")]
        home_inp = next((x for x in inputs if x["name"].endswith("heimTipp")), None)
        away_inp = next((x for x in inputs if x["name"].endswith("gastTipp")), None)
        if home_inp is None or away_inp is None:
            # Fallback auf Reihenfolge der sichtbaren Eingabefelder
            visible = [x for x in inputs if x.get("type", "text") in ("text", "number", "tel")]
            if len(visible) >= 2:
                home_inp, away_inp = visible[0], visible[1]
        open_ = home_inp is not None and not home_inp.has_attr("disabled")

        key = None
        for x in inputs:
            key = _form_key(x["name"])
            if key:
                break
        key = key or f"row{i}"

        odds = None
        # Aktuelles Layout: <a class="quote quoteheim|quoteremis|quotegast"><span class="quote-text">1.41</span>
        cells = [row.select_one(f".quote{k} .quote-text") for k in ("heim", "remis", "gast")]
        if all(cells):
            try:
                odds = tuple(float(c.get_text(strip=True).replace(",", ".")) for c in cells)
            except ValueError:
                odds = None
        if odds is None:
            m = _ODDS_RE.search(row_text)
            if m:
                odds = tuple(float(v.replace(",", ".")) for v in m.groups())

        round_quote = None
        # Rundenquote steht in der Spalte "Punkteregel" (col3), z. B. "4 - 5 - 10"
        rule_td = _cell(row, tds, columns, "punkteregel", "col3")
        rq = _ROUND_QUOTE_RE.search(rule_td.get_text(" ", strip=True)) if isinstance(rule_td, Tag) else None
        rq = rq or _ROUND_QUOTE_RE.search(_DATE_RE.sub(" ", row_text))
        if rq:
            round_quote = tuple(int(v) for v in rq.groups())

        if open_:
            tip_home = _int_or_none(home_inp.get("value"))
            tip_away = _int_or_none(away_inp.get("value"))
            used_fields.update({home_inp["name"], away_inp["name"]})
        else:
            # Abgelaufene Spiele zeigen den Tipp als Text, z. B. "2:1"
            tip_td = row.find("td", class_="kicktipp-tippabgabe") or _cell(row, tds, columns, "tipp", "col4") or row
            tm = re.search(r"\b(\d+)\s*:\s*(\d+)\b", _DATE_RE.sub(" ", tip_td.get_text(" ", strip=True)))
            tip_home, tip_away = (int(tm.group(1)), int(tm.group(2))) if tm else (None, None)

        matches.append(
            KicktippMatch(
                key=key,
                home=home,
                away=away,
                kickoff=last_kickoff,
                open=open_,
                tip_home=tip_home,
                tip_away=tip_away,
                odds=odds,  # type: ignore[arg-type]
                round_quote=round_quote,  # type: ignore[arg-type]
                home_field=home_inp["name"] if open_ else None,
                away_field=away_inp["name"] if open_ else None,
            )
        )

    index = None
    action = None
    fields: list[tuple[str, str]] = []
    submit_seen = False
    if isinstance(form, Tag):
        action = form.get("action")
        for inp in form.find_all(["input", "select", "button"]):
            name = inp.get("name")
            if not name or name in used_fields or inp.has_attr("disabled"):
                continue
            typ = inp.get("type", "submit" if inp.name == "button" else "text")
            if inp.name == "select":
                opt = inp.find("option", selected=True) or inp.find("option")
                fields.append((name, opt.get("value", "") if opt else ""))
            elif typ in ("checkbox", "radio"):
                if inp.has_attr("checked"):
                    fields.append((name, inp.get("value", "on")))
            elif typ == "submit":
                # Nur den ersten Absende-Knopf mitschicken, wie ein Browser beim Klick
                if not submit_seen:
                    fields.append((name, inp.get("value", "")))
                    submit_seen = True
            else:
                fields.append((name, inp.get("value", "")))
        index = _int_or_none(dict(fields).get("spieltagIndex"))
    if index is None:
        sel = soup.select_one("[name=spieltagIndex] option[selected]")
        index = _int_or_none(sel.get("value")) if sel else None
    return Matchday(index=index, matches=matches, form_action=action, form_fields=fields)


def _save_debug(pages: list[tuple[str, int, str]], filename: str = "kicktipp-login-diagnose.html") -> str | None:
    """Erhaltene Seiten zur Fehlersuche ablegen (enthalten keine Zugangsdaten)."""
    try:
        path = Path(filename).resolve()
        parts = [f"<!-- {url} -> HTTP {code} -->\n{html}" for url, code, html in pages]
        path.write_text("\n\n".join(parts), encoding="utf-8")
        return str(path)
    except OSError:
        return None


class KicktippClient:
    def __init__(self, runde: str, user: str | None, password: str | None, session: requests.Session | None = None):
        self.runde = runde
        self.user = user
        self.password = password
        self.s = session or requests.Session()
        self.s.headers.update(BROWSER_HEADERS)
        self._logged_in = False

    def _login_urls(self) -> list[str]:
        return [LOGIN_URL, f"{BASE}/{self.runde}/profil/login"]

    @staticmethod
    def _find_login_form(html: str) -> Tag | None:
        """Das Formular mit Passwortfeld – unabhängig von seiner id."""
        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form", id="loginFormular")
        if isinstance(form, Tag):
            return form
        for form in soup.find_all("form"):
            if form.find("input", attrs={"type": "password"}):
                return form
        return None

    def login(self) -> None:
        if not self.user or not self.password:
            raise KicktippError("KICKTIPP_USER / KICKTIPP_PASSWORD fehlen.")
        form, page_url, pages = None, LOGIN_URL, []
        for url in self._login_urls():
            r = self.s.get(url, timeout=30)
            pages.append((url, r.status_code, r.text))
            form = self._find_login_form(r.text)
            if form is not None:
                page_url = r.url or url
                break
        if form is None:
            dump = _save_debug(pages)
            raise KicktippError(
                "Login-Formular nicht gefunden. Kicktipp hat eine andere Seite geliefert"
                + (f" – gespeichert in {dump}." if dump else ".")
            )

        data = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name or inp.get("type") in ("submit", "button", "image", "checkbox"):
                continue
            data[name] = inp.get("value", "")
        pw_field = form.find("input", attrs={"type": "password"})
        user_field = form.find("input", attrs={"name": "kennung"}) or next(
            (i for i in form.find_all("input") if i.get("type", "text") in ("text", "email") and i.get("name")), None
        )
        if pw_field is None or user_field is None or not pw_field.get("name"):
            raise KicktippError("Login-Formular hat unerwartete Felder.")
        data[user_field["name"]] = self.user
        data[pw_field["name"]] = self.password
        for cb in form.find_all("input", attrs={"type": "checkbox"}):  # z. B. "angemeldet bleiben"
            if cb.get("name"):
                data[cb["name"]] = cb.get("value", "on")
        r = self.s.post(urljoin(page_url, form.get("action") or page_url), data=data, timeout=30)
        r.raise_for_status()
        if self._find_login_form(r.text) is not None:
            raise KicktippError("Login fehlgeschlagen – E-Mail oder Passwort falsch.")
        self._logged_in = True

    def _get(self, url: str, **params) -> str:
        if not self._logged_in:
            self.login()
        r = self.s.get(url, params=params, timeout=30)
        if "tippabgabeSpiele" not in r.text and self._find_login_form(r.text) is not None:
            self.login()  # Sitzung abgelaufen
            r = self.s.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.text

    def tippabgabe_url(self) -> str:
        return f"{BASE}/{self.runde}/tippabgabe"

    def matchday(self, index: int | None = None) -> Matchday:
        params = {"spieltagIndex": index} if index is not None else {}
        html = self._get(self.tippabgabe_url(), **params)
        try:
            md = parse_tippabgabe(html)
        except KicktippError:
            md = None
        if md is None or not md.matches:
            dump = _save_debug([(self.tippabgabe_url(), 200, html)], "kicktipp-tippabgabe-diagnose.html")
            raise KicktippError(
                "Auf der Tippabgabe-Seite wurden keine Spiele erkannt"
                + (f" – Seite gespeichert in {dump}." if dump else ".")
            )
        return md

    def submit(self, index: int | None, tips: dict[str, tuple[int, int]]) -> Matchday:
        """Tipps abgeben. tips: Spiel-Schlüssel -> (heim, gast). Gibt den Spieltag nach dem Speichern zurück."""
        md = self.matchday(index)
        by_key = {m.key: m for m in md.matches}
        data: list[tuple[str, str]] = list(md.form_fields)
        for m in md.matches:
            if not m.open:
                continue
            h, a = tips.get(m.key, (m.tip_home, m.tip_away))
            data.append((m.home_field, "" if h is None else str(h)))  # type: ignore[arg-type]
            data.append((m.away_field, "" if a is None else str(a)))  # type: ignore[arg-type]
        closed = [k for k in tips if k in by_key and not by_key[k].open]
        if closed:
            raise KicktippError(f"Spiel(e) nicht mehr tippbar: {', '.join(closed)}")
        unknown = [k for k in tips if k not in by_key]
        if unknown:
            raise KicktippError(f"Spiel(e) nicht auf dem Spieltag gefunden: {', '.join(unknown)}")
        action = urljoin(self.tippabgabe_url(), md.form_action or self.tippabgabe_url())
        r = self.s.post(action, data=data, timeout=30)
        r.raise_for_status()
        after = self.matchday(md.index if md.index is not None else index)
        saved = {m.key: (m.tip_home, m.tip_away) for m in after.matches}
        wrong = [k for k, v in tips.items() if saved.get(k) != tuple(v)]
        if wrong:
            raise KicktippError(f"Kicktipp hat folgende Tipps nicht übernommen: {', '.join(wrong)}")
        return after
