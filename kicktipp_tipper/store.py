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
