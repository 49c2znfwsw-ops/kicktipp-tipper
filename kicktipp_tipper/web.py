"""Web-Oberfläche und JSON-API."""
from __future__ import annotations

import secrets
from fractions import Fraction
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from .engine import Engine

STATIC = Path(__file__).parent / "static"


class RefreshRequest(BaseModel):
    spieltag: int | None = None


class Tip(BaseModel):
    key: str
    heim: int = Field(ge=0, le=99)
    gast: int = Field(ge=0, le=99)


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

    @app.get("/api/releases", dependencies=[Depends(auth)])
    def releases():
        return engine.store.releases()

    return app
