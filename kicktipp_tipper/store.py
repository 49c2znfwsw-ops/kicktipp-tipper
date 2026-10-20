"""SQLite-Ablage für Empfehlungs-Stände und das Freigabe-Protokoll."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone


class Store:
    def __init__(self, path: str):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY, created_at TEXT, spieltag INTEGER, data TEXT);
                CREATE TABLE IF NOT EXISTS releases (
                    id INTEGER PRIMARY KEY, created_at TEXT, spieltag INTEGER, match_key TEXT,
                    home TEXT, away TEXT, tip_home INTEGER, tip_away INTEGER,
                    mode TEXT, ok INTEGER, message TEXT);
                CREATE TABLE IF NOT EXISTS seen (
                    match_key TEXT PRIMARY KEY, seen_at TEXT, data TEXT);
                CREATE TABLE IF NOT EXISTS hist_matches (
                    match_key TEXT PRIMARY KEY, spieltag INTEGER, pos INTEGER, home TEXT, away TEXT,
                    kickoff TEXT, result TEXT, round_quote TEXT);
                CREATE TABLE IF NOT EXISTS hist_tips (
                    match_key TEXT, member_id TEXT, spieltag INTEGER, tip TEXT, points INTEGER,
                    PRIMARY KEY (match_key, member_id));
                CREATE TABLE IF NOT EXISTS hist_members (
                    spieltag INTEGER, member_id TEXT, name TEXT, day_points INTEGER, total INTEGER,
                    PRIMARY KEY (spieltag, member_id));
                CREATE TABLE IF NOT EXISTS hist_loaded (
                    spieltag INTEGER PRIMARY KEY, loaded_at TEXT, complete INTEGER);
                CREATE TABLE IF NOT EXISTS prognosen (
                    match_key TEXT, member_id TEXT, spieltag INTEGER, probs TEXT, created_at TEXT,
                    PRIMARY KEY (match_key, member_id));
                CREATE TABLE IF NOT EXISTS prognose_runde (
                    match_key TEXT PRIMARY KEY, spieltag INTEGER, shares TEXT, tipper REAL, created_at TEXT);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS empfehlungen (
                    match_key TEXT PRIMARY KEY, spieltag INTEGER, tip TEXT, ev REAL, created_at TEXT);
                CREATE TABLE IF NOT EXISTS crowd_obs (
                    match_key TEXT PRIMARY KEY, created_at TEXT,
                    p_home REAL, p_draw REAL, p_away REAL, q_home INTEGER, q_draw INTEGER, q_away INTEGER);
                CREATE TABLE IF NOT EXISTS quoten_archiv (
                    abruf TEXT, event_id TEXT, anpfiff TEXT, heim TEXT, gast TEXT, buchmacher TEXT, stand TEXT,
                    quote_heim REAL, quote_remis REAL, quote_gast REAL, ueber25 REAL, unter25 REAL,
                    PRIMARY KEY (abruf, event_id, buchmacher));
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def save_snapshot(self, spieltag: int | None, data: dict) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO snapshots (created_at, spieltag, data) VALUES (?, ?, ?)",
                (self._now(), spieltag, json.dumps(data, default=str)),
            )

    def latest_snapshot(self) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT data FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else None

    def match_series(self, spieltag: int | None) -> dict[str, list[tuple[str, dict]]]:
        """Je Spiel alle gespeicherten Stände (Zeitpunkt, Spiel-Daten) des Spieltags, älteste zuerst."""
        with self._lock:
            rows = self._db.execute(
                "SELECT created_at, data FROM snapshots WHERE spieltag IS ? ORDER BY id", (spieltag,)
            ).fetchall()
        out: dict[str, list[tuple[str, dict]]] = {}
        for at, data in rows:
            snap = json.loads(data)
            for m in snap.get("matches", []):
                out.setdefault(m["key"], []).append((snap.get("updated_at") or at, m))
        return out

    def log_release(self, spieltag, key, home, away, tip_home, tip_away, mode, ok, message="") -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO releases (created_at, spieltag, match_key, home, away, tip_home, tip_away, mode, ok, message)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self._now(), spieltag, key, home, away, tip_home, tip_away, mode, int(ok), message),
            )

    def releases(self, limit: int = 50) -> list[dict]:
        with self._lock:
            cur = self._db.execute(
                "SELECT created_at, spieltag, home, away, tip_home, tip_away, mode, ok, message"
                " FROM releases ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def save_crowd_obs(self, key: str, probs: tuple[float, float, float], quote: tuple[int, int, int]) -> None:
        """Endgültige Rundenquote eines abgelaufenen Spiels zusammen mit den Marktwahrscheinlichkeiten."""
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO crowd_obs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (key, self._now(), *probs, *quote),
            )

    def crowd_obs(self) -> list[tuple[tuple[float, float, float], tuple[int, int, int]]]:
        with self._lock:
            rows = self._db.execute("SELECT p_home, p_draw, p_away, q_home, q_draw, q_away FROM crowd_obs").fetchall()
        return [((r[0], r[1], r[2]), (r[3], r[4], r[5])) for r in rows]

    def seen(self) -> dict[str, dict]:
        """Zuletzt bestätigter Stand je Spiel (Empfehlung, Rundenquote, Wahrscheinlichkeiten)."""
        with self._lock:
            rows = self._db.execute("SELECT match_key, seen_at, data FROM seen").fetchall()
        return {k: {**json.loads(d), "seen_at": at} for k, at, d in rows}

    def set_seen(self, items: dict[str, dict]) -> None:
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO seen (match_key, seen_at, data) VALUES (?, ?, ?)",
                [(k, self._now(), json.dumps(v)) for k, v in items.items()],
            )

    # ---- Historie aus der Tippübersicht ------------------------------------------------
    def save_overview(self, spieltag: int, ov, complete: bool) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM hist_matches WHERE spieltag = ?", (spieltag,))
            self._db.execute("DELETE FROM hist_tips WHERE spieltag = ?", (spieltag,))
            self._db.execute("DELETE FROM hist_members WHERE spieltag = ?", (spieltag,))
            for pos, m in enumerate(ov.matches):
                self._db.execute(
                    "INSERT OR REPLACE INTO hist_matches VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (m.key, spieltag, pos, m.home, m.away, m.kickoff.isoformat() if m.kickoff else None,
                     json.dumps(m.result), json.dumps(m.round_quote)),
                )
            for mb in ov.members:
                self._db.execute(
                    "INSERT OR REPLACE INTO hist_members VALUES (?, ?, ?, ?, ?)",
                    (spieltag, mb.member_id, mb.name, mb.matchday_points, mb.total),
                )
                for m, tip, pts in zip(ov.matches, mb.tips, mb.points):
                    if tip is not None:
                        self._db.execute(
                            "INSERT OR REPLACE INTO hist_tips VALUES (?, ?, ?, ?, ?)",
                            (m.key, mb.member_id, spieltag, json.dumps(tip), pts),
                        )
            self._db.execute(
                "INSERT OR REPLACE INTO hist_loaded VALUES (?, ?, ?)", (spieltag, self._now(), int(complete))
            )

    def loaded_spieltage(self) -> dict[int, dict]:
        with self._lock:
            rows = self._db.execute("SELECT spieltag, loaded_at, complete FROM hist_loaded").fetchall()
        return {r[0]: {"loaded_at": r[1], "complete": bool(r[2])} for r in rows}

    def history(self) -> dict:
        with self._lock:
            matches = self._db.execute(
                "SELECT match_key, spieltag, pos, home, away, kickoff, result, round_quote FROM hist_matches"
                " ORDER BY spieltag, pos"
            ).fetchall()
            tips = self._db.execute("SELECT match_key, member_id, spieltag, tip, points FROM hist_tips").fetchall()
            members = self._db.execute(
                "SELECT spieltag, member_id, name, day_points, total FROM hist_members ORDER BY spieltag"
            ).fetchall()
        return {
            "matches": [
                {"key": k, "spieltag": st, "pos": p, "home": h, "away": a, "kickoff": ko,
                 "result": json.loads(r) if r else None, "round_quote": json.loads(q) if q else None}
                for k, st, p, h, a, ko, r, q in matches
            ],
            "tips": [
                {"match_key": k, "member_id": mid, "spieltag": st, "tip": json.loads(t), "points": pts}
                for k, mid, st, t, pts in tips
            ],
            "members": [
                {"spieltag": st, "member_id": mid, "name": n, "day_points": dp, "total": tot}
                for st, mid, n, dp, tot in members
            ],
        }

    # ---- Gespeicherte Mitspieler-Prognosen (letzter Stand vor Anpfiff) -------------------
    def save_predictions(self, spieltag: int | None, per_member: dict, crowd: dict) -> None:
        """per_member: {(match_key, member_id): probs}; crowd: {match_key: (shares, tipper)}"""
        now = self._now()
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO prognosen VALUES (?, ?, ?, ?, ?)",
                [(k, mid, spieltag, json.dumps(p), now) for (k, mid), p in per_member.items()],
            )
            self._db.executemany(
                "INSERT OR REPLACE INTO prognose_runde VALUES (?, ?, ?, ?, ?)",
                [(k, spieltag, json.dumps(sh), n, now) for k, (sh, n) in crowd.items()],
            )

    def predictions(self, match_keys: list[str]) -> tuple[dict, dict]:
        if not match_keys:
            return {}, {}
        marks = ",".join("?" * len(match_keys))
        with self._lock:
            rows = self._db.execute(
                f"SELECT match_key, member_id, probs FROM prognosen WHERE match_key IN ({marks})", match_keys
            ).fetchall()
            crowd_rows = self._db.execute(
                f"SELECT match_key, shares, tipper FROM prognose_runde WHERE match_key IN ({marks})", match_keys
            ).fetchall()
        return ({(k, mid): json.loads(p) for k, mid, p in rows},
                {k: (json.loads(sh), n) for k, sh, n in crowd_rows})

    # ---- Einstellungen aus der Oberfläche (überschreiben config.toml) --------------------
    def settings(self) -> dict:
        with self._lock:
            rows = self._db.execute("SELECT key, value FROM settings").fetchall()
        return {k: json.loads(v) for k, v in rows}

    def save_settings(self, values: dict) -> None:
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO settings VALUES (?, ?)", [(k, json.dumps(v)) for k, v in values.items()]
            )

    def archive_odds(self, abruf: str, data: list[dict]) -> int:
        """Legt einen kompletten Odds-API-Abruf ab: alle Spiele, alle Buchmacher. Gibt die Zahl der Zeilen zurück."""
        rows = []
        for ev in data:
            home, away = ev.get("home_team"), ev.get("away_team")
            for bm in ev.get("bookmakers", []):
                h = d = a = o = u = None
                for mk in bm.get("markets", []):
                    prices = {x["name"]: x["price"] for x in mk.get("outcomes", [])}
                    if mk.get("key") == "h2h":
                        h, d, a = prices.get(home), prices.get("Draw"), prices.get(away)
                    elif mk.get("key") == "totals" and {x.get("point") for x in mk.get("outcomes", [])} == {2.5}:
                        o, u = prices.get("Over"), prices.get("Under")
                rows.append((abruf, ev.get("id") or f"{home}|{away}|{ev.get('commence_time')}", ev.get("commence_time"),
                             home, away, bm.get("title") or bm.get("key"), bm.get("last_update"), h, d, a, o, u))
        with self._lock, self._db:
            self._db.executemany("INSERT OR IGNORE INTO quoten_archiv VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        return len(rows)

    def odds_archive_stats(self) -> dict:
        with self._lock:
            n, abrufe, spiele, books, first, last = self._db.execute(
                "SELECT COUNT(*), COUNT(DISTINCT abruf), COUNT(DISTINCT event_id), COUNT(DISTINCT buchmacher), MIN(abruf), MAX(abruf)"
                " FROM quoten_archiv").fetchone()
        return {"quoten": n, "abrufe": abrufe, "spiele": spiele, "buchmacher": books, "erster": first, "letzter": last}

    def odds_archive(self) -> tuple[list[str], list[tuple]]:
        with self._lock:
            cur = self._db.execute("SELECT * FROM quoten_archiv ORDER BY abruf, anpfiff, heim, buchmacher")
            return [c[0] for c in cur.description], cur.fetchall()

    def clear_settings(self, keys: tuple[str, ...]) -> None:
        with self._lock, self._db:
            self._db.executemany("DELETE FROM settings WHERE key = ?", [(k,) for k in keys])

    # ---- Letzte Empfehlung vor Anpfiff (für den Rückblick) -------------------------------
    def save_recommendations(self, spieltag: int | None, recs: dict[str, dict]) -> None:
        now = self._now()
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO empfehlungen VALUES (?, ?, ?, ?, ?)",
                [(k, spieltag, json.dumps(v["tip"]), v["ev"], now) for k, v in recs.items()],
            )

    def recommendations(self) -> dict[str, dict]:
        with self._lock:
            rows = self._db.execute("SELECT match_key, spieltag, tip, ev, created_at FROM empfehlungen").fetchall()
        found = {k: {"spieltag": st, "tip": json.loads(t), "ev": ev, "at": at} for k, st, t, ev, at in rows}
        stored = set(found)
        # Spiele aus der Zeit vor dieser Tabelle: letzte Empfehlung aus den gespeicherten Ständen holen
        with self._lock:
            snaps = self._db.execute("SELECT created_at, spieltag, data FROM snapshots ORDER BY id").fetchall()
        for at, st, data in snaps:
            for m in json.loads(data).get("matches", []):
                if m.get("open") and m.get("tip") and m["key"] not in stored:
                    found[m["key"]] = {"spieltag": st, "tip": m["tip"], "ev": m.get("expected_points"), "at": at}
        return found
