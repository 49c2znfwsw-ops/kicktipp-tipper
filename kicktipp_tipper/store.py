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
                CREATE TABLE IF NOT EXISTS crowd_obs (
                    match_key TEXT PRIMARY KEY, created_at TEXT,
                    p_home REAL, p_draw REAL, p_away REAL, q_home INTEGER, q_draw INTEGER, q_away INTEGER);
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
