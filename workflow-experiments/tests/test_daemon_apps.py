from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from onectx.daemon import apps as app_mod
from onectx.daemon.apps import AppError, app_status, load_apps, load_registry, start_app, stop_app


def system_for_tmp(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        root=root,
        runtime_dir=root / "memory" / "runtime",
        storage_dir=root / "storage" / "lakestore",
    )


def test_start_app_reports_immediate_startup_exit(tmp_path: Path) -> None:
    command = [sys.executable, "-c", "import sys; print('boom from app'); sys.exit(7)"]
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "apps.toml").write_text(
        "\n".join(
            [
                "[[apps]]",
                'id = "fail-fast"',
                'label = "Fail Fast"',
                'path = "."',
                f"command = {json.dumps(command)}",
                'url = "http://127.0.0.1:9/"',
                'health_url = "http://127.0.0.1:9/__health"',
            ]
        ),
        encoding="utf-8",
    )
    system = system_for_tmp(tmp_path)

    with pytest.raises(AppError) as excinfo:
        start_app(system, "fail-fast")

    assert "exited during startup with code 7" in str(excinfo.value)
    assert "boom from app" in str(excinfo.value)
    registry = load_registry(system)
    assert registry["apps"]["fail-fast"]["status"] == "failed"
    assert registry["apps"]["fail-fast"]["exit_code"] == 7
    assert app_status(system)[0]["status"] == "failed"


def test_repo_wiki_app_uses_strict_supervised_port() -> None:
    wiki = next(app for app in load_apps(Path.cwd()) if app.id == "wiki")

    assert "--no-port-fallback" in wiki.command


def test_stop_app_refuses_stale_reused_pid(tmp_path: Path) -> None:
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "apps.toml").write_text(
        "\n".join(
            [
                "[[apps]]",
                'id = "stale"',
                'label = "Stale"',
                'path = "."',
                'command = ["definitely-not-this-process"]',
                'url = "http://127.0.0.1:9/"',
            ]
        ),
        encoding="utf-8",
    )
    system = system_for_tmp(tmp_path)
    registry = system.runtime_dir / "processes" / "apps.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "version": "0.1",
                "apps": {
                    "stale": {
                        "pid": os.getpid(),
                        "status": "running",
                        "command": ["definitely-not-this-process"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = stop_app(system, "stale")

    assert result["status"] == "stale_pid_refused"
    assert load_registry(system)["apps"]["stale"]["status"] == "stale_pid"


def test_corrupt_app_registry_does_not_crash_status(tmp_path: Path) -> None:
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "apps.toml").write_text(
        "\n".join(
            [
                "[[apps]]",
                'id = "ok"',
                'label = "OK"',
                'path = "."',
                f"command = {json.dumps([sys.executable, '-c', 'import time; time.sleep(30)'])}",
                'url = "http://127.0.0.1:9/"',
            ]
        ),
        encoding="utf-8",
    )
    system = system_for_tmp(tmp_path)
    path = system.runtime_dir / "processes" / "apps.json"
    path.parent.mkdir(parents=True)
    path.write_text("{bad json", encoding="utf-8")

    assert app_status(system)[0]["status"] == "stopped"


def test_app_status_tolerates_wrong_registry_schema(tmp_path: Path) -> None:
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "apps.toml").write_text(
        "\n".join(
            [
                "[[apps]]",
                'id = "ok"',
                'label = "OK"',
                'path = "."',
                f"command = {json.dumps([sys.executable, '-c', 'import time; time.sleep(30)'])}",
                'url = "http://127.0.0.1:9/"',
            ]
        ),
        encoding="utf-8",
    )
    system = system_for_tmp(tmp_path)
    path = system.runtime_dir / "processes" / "apps.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": "0.1", "apps": []}), encoding="utf-8")

    status = app_status(system)

    assert status[0]["status"] == "stopped"
    assert load_registry(system).get("corrupt_registry_apps") is True


def test_app_command_must_be_array(tmp_path: Path) -> None:
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "apps.toml").write_text(
        "\n".join(
            [
                "[[apps]]",
                'id = "bad"',
                'path = "."',
                'command = "python -m http.server"',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(AppError, match="command must be an array"):
        load_apps(tmp_path)


def test_start_app_surfaces_spawn_errors_and_marks_failed(tmp_path: Path) -> None:
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "apps.toml").write_text(
        "\n".join(
            [
                "[[apps]]",
                'id = "missing-binary"',
                'label = "Missing Binary"',
                'path = "."',
                'command = ["definitely-not-a-real-executable-1context"]',
                'url = "http://127.0.0.1:9/"',
            ]
        ),
        encoding="utf-8",
    )
    system = system_for_tmp(tmp_path)

    with pytest.raises(AppError, match="failed to start app 'missing-binary'"):
        start_app(system, "missing-binary")

    record = load_registry(system)["apps"]["missing-binary"]
    assert record["status"] == "failed"
    assert record["pid"] == 0
    assert record["spawn_error"]["type"] in {"FileNotFoundError", "PermissionError"}


def test_stop_app_tolerates_invalid_pid_field(tmp_path: Path) -> None:
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "apps.toml").write_text(
        "\n".join(
            [
                "[[apps]]",
                'id = "bad-pid"',
                'label = "Bad PID"',
                'path = "."',
                f"command = {json.dumps([sys.executable, '-c', 'import time; time.sleep(30)'])}",
                'url = "http://127.0.0.1:9/"',
            ]
        ),
        encoding="utf-8",
    )
    system = system_for_tmp(tmp_path)
    registry = system.runtime_dir / "processes" / "apps.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "version": "0.1",
                "apps": {
                    "bad-pid": {
                        "pid": "not-a-number",
                        "status": "running",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = stop_app(system, "bad-pid")

    assert result["status"] == "already_stopped"
    assert load_registry(system)["apps"]["bad-pid"]["status"] == "stopped"


def test_app_process_matches_accepts_wrapper_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    app = app_mod.AppDefinition(
        id="mermaid",
        label="Mermaid",
        path=tmp_path,
        command=("npm", "run", "dev:server", "--", "--port", "4184"),
        url="http://127.0.0.1:4184",
        health_url="http://127.0.0.1:4184",
        purpose="test",
        source_path=tmp_path / "apps.toml",
    )
    record = {
        "command": ["npm", "run", "dev:server", "--", "--port", "4184"],
        "pgid": 777,
        "process_started_at": "Wed Apr 29 10:10:10 2026",
        "path": str(tmp_path),
    }

    monkeypatch.setattr(
        app_mod,
        "process_command",
        lambda _pid: "node /opt/homebrew/bin/npm run dev:server --port 4184",
    )
    monkeypatch.setattr(app_mod, "process_started_at", lambda _pid: "Wed Apr 29 10:10:10 2026")
    monkeypatch.setattr(app_mod, "process_cwd", lambda _pid: tmp_path.resolve())
    monkeypatch.setattr(app_mod.os, "getpgid", lambda _pid: 777)

    assert app_mod.app_process_matches(app, record, 12345)


def test_app_process_match_requires_identity_fingerprint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    app = app_mod.AppDefinition(
        id="wiki",
        label="Wiki",
        path=tmp_path,
        command=("uv", "run", "1context", "wiki", "serve"),
        url="http://127.0.0.1:17319",
        health_url="http://127.0.0.1:17319/__health",
        purpose="test",
        source_path=tmp_path / "apps.toml",
    )
    record = {
        "command": ["uv", "run", "1context", "wiki", "serve"],
        "path": str(tmp_path),
    }

    monkeypatch.setattr(app_mod, "process_command", lambda _pid: "uv run 1context wiki serve")
    monkeypatch.setattr(app_mod, "process_started_at", lambda _pid: "")
    monkeypatch.setattr(app_mod, "process_cwd", lambda _pid: None)

    assert not app_mod.app_process_matches(app, record, 12345)


def test_app_process_matches_handles_lossy_space_args(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    app = app_mod.AppDefinition(
        id="space-arg",
        label="Space Arg",
        path=tmp_path,
        command=("uv", "run", "python", "-c", "import time; time.sleep(30)"),
        url="http://127.0.0.1:0",
        health_url="",
        purpose="test",
        source_path=tmp_path / "apps.toml",
    )
    record = {
        "command": ["uv", "run", "python", "-c", "import time; time.sleep(30)"],
        "pgid": 88,
        "process_started_at": "Wed Apr 29 12:00:00 2026",
        "path": str(tmp_path),
    }

    monkeypatch.setattr(app_mod, "process_command", lambda _pid: "uv run python -c import time; time.sleep(30)")
    monkeypatch.setattr(app_mod, "process_started_at", lambda _pid: "Wed Apr 29 12:00:00 2026")
    monkeypatch.setattr(app_mod, "process_cwd", lambda _pid: tmp_path.resolve())
    monkeypatch.setattr(app_mod.os, "getpgid", lambda _pid: 88)

    assert app_mod.app_process_matches(app, record, 12345)


def test_app_process_matches_tolerates_unbalanced_quote_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    app = app_mod.AppDefinition(
        id="quote-arg",
        label="Quote Arg",
        path=tmp_path,
        command=("uv", "run", "python", "-c", "print('ok')"),
        url="http://127.0.0.1:0",
        health_url="",
        purpose="test",
        source_path=tmp_path / "apps.toml",
    )
    record = {
        "command": ["uv", "run", "python", "-c", "print('ok')"],
        "pgid": 99,
        "process_started_at": "Wed Apr 29 12:00:00 2026",
        "path": str(tmp_path),
    }

    monkeypatch.setattr(app_mod, "process_command", lambda _pid: 'uv run python -c "unterminated')
    monkeypatch.setattr(app_mod, "process_started_at", lambda _pid: "Wed Apr 29 12:00:00 2026")
    monkeypatch.setattr(app_mod, "process_cwd", lambda _pid: tmp_path.resolve())
    monkeypatch.setattr(app_mod.os, "getpgid", lambda _pid: 99)

    assert not app_mod.app_process_matches(app, record, 12345)
