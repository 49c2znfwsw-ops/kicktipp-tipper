"""Update-Logik der Startdatei (ohne Netzwerk)."""
import io
import zipfile

import start


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(f"kicktipp-tipper-main/{name}", content)
    return buf.getvalue()


def test_parse_version():
    assert start.parse_version('[project]\nname = "x"\nversion = "0.2.10"\n') == (0, 2, 10)
    assert (0, 2, 10) > (0, 2, 9)


def test_apply_update_keeps_local_files(tmp_path):
    (tmp_path / ".env").write_text("KICKTIPP_USER=me")
    (tmp_path / "config.toml").write_text('runde = "meine"')
    (tmp_path / "kicktipp-tipper.sqlite").write_text("db")
    (tmp_path / "pyproject.toml").write_text('version = "0.1.0"')
    (tmp_path / "unchanged.txt").write_text("same")
    archive = _zip({
        ".env": "BOESE",
        "config.toml": "BOESE",
        ".venv/x": "BOESE",
        "kicktipp-tipper.sqlite": "BOESE",
        "pyproject.toml": 'version = "0.2.0"',
        "kicktipp_tipper/neu.py": "print(1)",
        "unchanged.txt": "same",
        "Kicktipp-Tipper-starten-Mac.command": "#!/bin/bash",
    })
    changed = start.apply_update(archive, tmp_path)
    assert sorted(changed) == ["Kicktipp-Tipper-starten-Mac.command", "kicktipp_tipper/neu.py", "pyproject.toml"]
    assert (tmp_path / ".env").read_text() == "KICKTIPP_USER=me"
    assert (tmp_path / "config.toml").read_text() == 'runde = "meine"'
    assert (tmp_path / "kicktipp-tipper.sqlite").read_text() == "db"
    assert not (tmp_path / ".venv").exists()
    assert (tmp_path / "kicktipp_tipper" / "neu.py").read_text() == "print(1)"


def test_version_endpoint_and_shutdown_guard(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import test_engine_web as t
    from kicktipp_tipper import __version__

    engine, _ = t.make_engine(tmp_path)
    client = TestClient(t.create_app(engine, background=False))
    assert client.get("/api/version").json()["version"] == __version__ == ".".join(map(str, start.local_version()))
    calls = []
    monkeypatch.setattr("os._exit", lambda code: calls.append(code))
    assert client.post("/api/beenden").json()["ok"]


def test_take_over_running(monkeypatch):
    opened = []
    monkeypatch.setattr(start.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(start, "running_version", lambda: None)
    assert start.take_over_running() is False
    monkeypatch.setattr(start, "running_version", lambda: ".".join(map(str, start.local_version())))
    assert start.take_over_running() is True and opened
    # Ältere Version: wird beendet, danach ist der Port frei
    answers = iter(["0.2.0", None])
    monkeypatch.setattr(start, "running_version", lambda: next(answers))
    monkeypatch.setattr(start._local, "open", lambda *a, **k: None)
    assert start.take_over_running() is False


def test_newer_version_notice(monkeypatch):
    from kicktipp_tipper import updatecheck

    monkeypatch.setattr(updatecheck, "_cache", {"at": 1e18, "version": "99.0.0"})
    assert updatecheck.newer_version() == "99.0.0"
    monkeypatch.setattr(updatecheck, "_cache", {"at": 1e18, "version": "0.0.1"})
    assert updatecheck.newer_version() is None
