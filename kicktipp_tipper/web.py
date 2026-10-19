"""Web-Oberfläche und JSON-API."""
from __future__ import annotations

import secrets
from fractions import Fraction
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

import os

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from . import __version__
from .engine import Engine

STATIC = Path(__file__).parent / "static"


class RefreshRequest(BaseModel):
    spieltag: int | None = None


class Tip(BaseModel):
    key: str
    heim: int = Field(ge=0, le=99)
    gast: int = Field(ge=0, le=99)


class SeenRequest(BaseModel):
    keys: list[str] | None = None  # None = alle


class HistoryRequest(BaseModel):
    alles: bool = False  # auch abgeschlossene Spieltage neu laden


class ReleaseRequest(BaseModel):
    spieltag: int | None = None
    tips: list[Tip]


def create_app(engine: Engine, background: bool = True) -> FastAPI:
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if background:
            threading.Thread(target=engine.run_forever, args=(stop,), daemon=True).start()
        yield
        stop.set()

    app = FastAPI(title="Kicktipp-Tipper", lifespan=lifespan)
    basic = HTTPBasic(auto_error=False)

    def auth(creds: HTTPBasicCredentials | None = Depends(basic)) -> None:
        pw = engine.cfg.ui_password
        if not pw:
            return
        if creds is None or not secrets.compare_digest(creds.password.encode(), pw.encode()):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Basic"})

    @app.get("/", dependencies=[Depends(auth)])
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/version")
    def version():
        return {"version": __version__}

    @app.post("/api/beenden")
    def beenden(request: Request):
        """Beendet dieses Programm – damit eine neu gestartete Version übernehmen kann (nur vom eigenen Rechner)."""
        if request.client is None or request.client.host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            raise HTTPException(403, "Nur vom eigenen Rechner erlaubt.")
        stop.set()
        threading.Timer(0.5, os._exit, [0]).start()
        return {"ok": True}

    @app.get("/api/state", dependencies=[Depends(auth)])
    def state():
        return {
            "state": engine.state,
            "regeln": asdict(engine.cfg.rules),
            "auto_freigabe_minuten": engine.cfg.auto_freigabe_minuten,
            "strategieverbot": (
                f"{Fraction(engine.cfg.strategieverbot.max_share).limit_denominator(12)} gleiche Ergebnisse ab {engine.cfg.strategieverbot.min_matches} Spielen"
                if engine.cfg.strategieverbot
                else None
            ),
            "aktualisieren_minuten": engine.cfg.aktualisieren_minuten,
            "version": __version__,
        }

    @app.post("/api/refresh", dependencies=[Depends(auth)])
    def refresh(req: RefreshRequest):
        try:
            return engine.refresh(req.spieltag)
        except Exception as e:
            raise HTTPException(502, f"Aktualisierung fehlgeschlagen: {e}")

    @app.post("/api/release", dependencies=[Depends(auth)])
    def release(req: ReleaseRequest):
        if not req.tips:
            raise HTTPException(400, "Keine Tipps ausgewählt.")
        try:
            engine.release(req.spieltag, {t.key: (t.heim, t.gast) for t in req.tips})
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(502, f"Freigabe fehlgeschlagen: {e}")
        return engine.state

    @app.post("/api/seen", dependencies=[Depends(auth)])
    def seen(req: SeenRequest):
        return engine.mark_seen(req.keys)

    @app.get("/api/mitspieler", dependencies=[Depends(auth)])
    def mitspieler():
        return engine.mitspieler()

    @app.get("/api/verlauf", dependencies=[Depends(auth)])
    def verlauf():
        return engine.verlauf()

    @app.post("/api/historie", dependencies=[Depends(auth)])
    def historie(req: HistoryRequest):
        try:
            result = engine.load_history(force=req.alles)
            engine.refresh(None)  # Empfehlungen mit den neuen Profilen
        except Exception as e:
            raise HTTPException(502, f"Historie nicht geladen: {e}")
        return result

    @app.get("/api/releases", dependencies=[Depends(auth)])
    def releases():
        return engine.store.releases()

    return app
