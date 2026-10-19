"""Kommandozeile: Oberfläche starten, Empfehlungen anzeigen, Tipps freigeben."""
from __future__ import annotations

import argparse
import logging

from .config import load_config
from .engine import Engine
from .sources.kicktipp import KicktippClient
from .store import Store


def build_engine(config_path: str) -> Engine:
    cfg = load_config(config_path)
    client = KicktippClient(cfg.runde, cfg.kicktipp_user, cfg.kicktipp_password)
    return Engine(cfg, client, Store(cfg.datenbank))


def _print(state: dict) -> None:
    print(f"Tipprunde {state['runde']} – Spieltag {state['spieltag']} (Stand {state['updated_at']})\n")
    print(f"{'Spiel':44} {'Anpfiff':17} {'Tipp':>5} {'E[Pkt]':>6} {'H/U/A %':>12} {'Rundenquote':>11}  {'aktuell':>7}")
    for m in state["matches"]:
        game = f"{m['home']} – {m['away']}"[:44]
        kick = (m["kickoff"] or "")[:16].replace("T", " ")
        tip = ":".join(map(str, m["tip"])) if m["tip"] else "–"
        ev = f"{m['expected_points']:.2f}" if m["expected_points"] is not None else "–"
        p = m["probs"]
        hua = f"{p['heim']*100:.0f}/{p['remis']*100:.0f}/{p['gast']*100:.0f}" if p else "–"
        cur = ":".join(map(str, m["current_tip"])) if m["current_tip"] else "–"
        rq = "-".join(map(str, m["round_quote"])) if m.get("round_quote") else "–"
        lock = "" if m["open"] else " (zu)"
        print(f"{game:44} {kick:17} {tip:>5} {ev:>6} {hua:>12} {rq:>11}  {cur:>7}{lock}")
        if m.get("hinweis"):
            print(f"    {m['hinweis']}")


def _ask_password(prompt: str) -> str:
    """Passwort abfragen und dabei Sternchen zeigen (Windows); sonst verdeckt wie üblich."""
    try:
        import msvcrt
    except ImportError:
        import getpass

        return getpass.getpass(prompt.rstrip(": ") + " (wird beim Tippen nicht angezeigt): ")
    print(prompt, end="", flush=True)
    chars: list[str] = []
    while True:
        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            print()
            return "".join(chars)
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\b":
            if chars:
                chars.pop()
                print("\b \b", end="", flush=True)
        elif ch in ("\x00", "\xe0"):
            msvcrt.getwch()  # Pfeiltasten u. Ä. ignorieren
        else:
            chars.append(ch)
            print("*", end="", flush=True)


def einrichten(config_path: str) -> None:
    """Fragt den Kicktipp-Login ab, prüft ihn und legt ihn in .env neben der config.toml ab."""
    from pathlib import Path

    from .sources.kicktipp import KicktippError

    env = Path(config_path).resolve().parent / ".env"
    cfg = load_config(config_path)
    print(f"Einrichtung für die Tipprunde „{cfg.runde}“.")
    print("Dein Login wird nur auf diesem Computer in der Datei .env gespeichert.\n")
    checked = True
    while True:
        user = input("Kicktipp-E-Mail: ").strip()
        password = _ask_password("Kicktipp-Passwort: ")
        try:
            KicktippClient(cfg.runde, user, password).login()
        except KicktippError as e:
            print(f"\n{e}")
            if "nicht gefunden" in str(e) or "unerwartete" in str(e):
                # Liegt nicht am Passwort – erneutes Eintippen hilft nicht
                print("Bitte die genannte Datei an den Entwickler schicken.\n")
                raise SystemExit(1)
            print("Bitte noch einmal.\n")
            continue
        except Exception as e:
            print(f"\nKicktipp ist gerade nicht erreichbar ({e}). Login wird trotzdem gespeichert.")
            checked = False
        break
    lines = [f"KICKTIPP_USER={user}", f"KICKTIPP_PASSWORD={password}"]
    if env.exists():  # übrige Einträge (z. B. ODDS_API_KEY) behalten
        lines += [l for l in env.read_text(encoding="utf-8").splitlines() if not l.startswith(("KICKTIPP_USER=", "KICKTIPP_PASSWORD="))]
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\nLogin geprüft und gespeichert.\n" if checked else "\nLogin gespeichert (noch nicht geprüft).\n")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="kicktipp-tipper")
    ap.add_argument("-c", "--config", default="config.toml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("serve", help="Web-Oberfläche mit laufender Aktualisierung starten")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8000)
    sp.add_argument("--browser", action="store_true", help="Oberfläche nach dem Start im Browser öffnen")

    sub.add_parser("einrichten", help="Kicktipp-Login abfragen, prüfen und in .env speichern")

    sr = sub.add_parser("empfehlen", help="Empfehlungen für einen Spieltag ausgeben")
    sr.add_argument("--spieltag", type=int)

    sf = sub.add_parser("freigeben", help="Empfehlungen aller offenen Spiele eines Spieltags abgeben")
    sf.add_argument("--spieltag", type=int)
    sf.add_argument("--nur-leere", action="store_true", help="nur Spiele ohne bisherigen Tipp")
    sf.add_argument("--ja", action="store_true", help="ohne Rückfrage abgeben")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.cmd == "einrichten":
        einrichten(args.config)
        return
    engine = build_engine(args.config)
    try:
        _run(args, engine)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        if args.verbose:
            raise
        raise SystemExit(f"Fehler: {e}")


def _run(args: argparse.Namespace, engine: Engine) -> None:
    if args.cmd == "serve":
        import uvicorn

        from .web import create_app

        if args.browser:
            import threading
            import webbrowser

            threading.Timer(2.0, webbrowser.open, [f"http://{args.host}:{args.port}/"]).start()
        print(f"\nKicktipp-Tipper läuft: http://{args.host}:{args.port}/  (Beenden: dieses Fenster schließen)\n")
        uvicorn.run(create_app(engine), host=args.host, port=args.port, log_level="warning")
    elif args.cmd == "empfehlen":
        _print(engine.refresh(args.spieltag))
    elif args.cmd == "freigeben":
        state = engine.refresh(args.spieltag)
        _print(state)
        tips = {
            m["key"]: tuple(m["tip"])
            for m in state["matches"]
            if m["open"] and m["tip"] and not (args.nur_leere and m["current_tip"]) and m["tip"] != m["current_tip"]
        }
        if not tips:
            print("\nNichts abzugeben.")
            return
        if not args.ja and input(f"\n{len(tips)} Tipp(s) bei Kicktipp abgeben? [j/N] ").strip().lower() not in ("j", "ja", "y"):
            print("Abgebrochen.")
            return
        engine.release(state["spieltag"], tips)
        print(f"{len(tips)} Tipp(s) abgegeben und von Kicktipp bestätigt.")


if __name__ == "__main__":
    main()
