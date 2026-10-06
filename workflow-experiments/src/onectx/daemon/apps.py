from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from onectx.config import MemorySystem, resolve_path
from onectx.io_utils import atomic_write_json, exclusive_file_lock, read_json_object
from onectx.storage import LakeStore, utc_now


class AppError(RuntimeError):
    """Raised when a supervised local app cannot be managed."""


STARTUP_EXIT_GRACE_SECONDS = 2.0
LOG_TAIL_BYTES = 4000


@dataclass(frozen=True)
class AppDefinition:
    id: str
    label: str
    path: Path
    command: tuple[str, ...]
    url: str
    health_url: str
    purpose: str
    source_path: Path

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "path": str(self.path),
            "command": list(self.command),
            "url": self.url,
            "health_url": self.health_url,
            "purpose": self.purpose,
            "source_path": str(self.source_path),
        }


def load_apps(root: Path | str) -> tuple[AppDefinition, ...]:
    root_path = Path(root)
    manifest = root_path / "apps" / "apps.toml"
    if not manifest.exists():
        return ()
    raw = tomllib.loads(manifest.read_text(encoding="utf-8"))
    raw_apps = raw.get("apps", [])
    if not isinstance(raw_apps, list):
        raise AppError(f"{manifest} apps must be an array of tables")
    apps: list[AppDefinition] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_apps):
        if not isinstance(item, dict):
            raise AppError(f"{manifest} app {index} must be a table")
        app_id = str(item.get("id", "")).strip()
        if not app_id:
            raise AppError(f"{manifest} app {index} is missing id")
        if app_id in seen:
            raise AppError(f"duplicate app id {app_id!r} in {manifest}")
        seen.add(app_id)
        command_value = item.get("command", [])
        if not isinstance(command_value, list):
            raise AppError(f"{manifest} app {app_id!r} command must be an array")
        command = tuple(str(part) for part in command_value)
        if not command:
            raise AppError(f"{manifest} app {app_id!r} is missing command")
        apps.append(
            AppDefinition(
                id=app_id,
                label=str(item.get("label", app_id)),
                path=resolve_path(root_path, item.get("path", ".")),
                command=command,
                url=str(item.get("url", "")),
                health_url=str(item.get("health_url", item.get("url", ""))),
                purpose=str(item.get("purpose", "")),
                source_path=manifest,
            )
        )
    return tuple(apps)


def app_by_id(system: MemorySystem, app_id: str) -> AppDefinition:
    for app in load_apps(system.root):
        if app.id == app_id:
            return app
    raise AppError(f"unknown app {app_id!r}")


def registry_path(system: MemorySystem) -> Path:
    return system.runtime_dir / "processes" / "apps.json"


def registry_lock_path(system: MemorySystem) -> Path:
    return system.runtime_dir / "processes" / "apps.lock"


def log_path(system: MemorySystem, app_id: str) -> Path:
    return system.runtime_dir / "processes" / "logs" / f"{app_id}.log"


def load_registry(system: MemorySystem) -> dict[str, Any]:
    path = registry_path(system)
    if not path.exists():
        return {"version": "0.1", "apps": {}}
    payload = read_json_object(path)
    if payload is None:
        return {"version": "0.1", "apps": {}, "corrupt_registry_path": str(path)}
    payload.setdefault("version", "0.1")
    if not isinstance(payload.get("apps"), dict):
        payload["apps"] = {}
        payload["corrupt_registry_apps"] = True
    return payload


def save_registry(system: MemorySystem, payload: dict[str, Any]) -> None:
    path = registry_path(system)
    atomic_write_json(path, payload)


def app_status(system: MemorySystem) -> list[dict[str, Any]]:
    registry = load_registry(system)
    records = registry_apps(registry)
    statuses: list[dict[str, Any]] = []
    for app in load_apps(system.root):
        record = registry_record(records.get(app.id))
        pid = parse_positive_int(record.get("pid"))
        running = pid_running(pid) and app_process_matches(app, record, pid)
        status = "running" if running else ("failed" if record.get("status") == "failed" else "stopped")
        if pid_running(pid) and not running:
            status = "stale_pid"
        if running and app.health_url:
            health = "ok" if url_ok(app.health_url) else "starting"
        else:
            health = "-"
        statuses.append(
            {
                **app.to_payload(),
                "pid": pid,
                "status": status,
                "health": health,
                "started_at": record.get("started_at", ""),
                "stopped_at": record.get("stopped_at", ""),
                "log_path": record.get("log_path", str(log_path(system, app.id))),
            }
        )
    return statuses


def start_app(system: MemorySystem, app_id: str, *, store: LakeStore | None = None) -> dict[str, Any]:
    with exclusive_file_lock(registry_lock_path(system)):
        app = app_by_id(system, app_id)
        registry = load_registry(system)
        records = registry_apps(registry)
        existing = registry_record(records.get(app.id))
        pid = parse_positive_int(existing.get("pid"))
        if pid_running(pid):
            if app_process_matches(app, existing, pid):
                return {"app": app.to_payload(), "pid": pid, "status": "already_running", "url": app.url}
            existing.update({"pid": 0, "status": "stale_pid", "stopped_at": utc_now()})
            records[app.id] = existing
            save_registry(system, registry)

        if not app.path.is_dir():
            raise AppError(f"app path does not exist: {app.path}")

        logs = log_path(system, app.id)
        logs.parent.mkdir(parents=True, exist_ok=True)
        log_start = logs.stat().st_size if logs.exists() else 0
        log_handle = logs.open("ab")
        try:
            try:
                process = subprocess.Popen(
                    list(app.command),
                    cwd=app.path,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except (OSError, ValueError) as exc:
                record = {
                    "pid": 0,
                    "pgid": 0,
                    "process_started_at": "",
                    "status": "failed",
                    "started_at": utc_now(),
                    "stopped_at": utc_now(),
                    "path": str(app.path),
                    "command": list(app.command),
                    "url": app.url,
                    "health_url": app.health_url,
                    "log_path": str(logs),
                    "spawn_error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                }
                records[app.id] = record
                save_registry(system, registry)
                if store:
                    store.append_event(
                        "app.start_failed",
                        source="1context-daemon",
                        actor="daemon",
                        subject=app.id,
                        text=f"Failed to start {app.label}.",
                        payload={
                            "app": app.to_payload(),
                            "pid": 0,
                            "log_path": str(logs),
                            "spawn_error": {
                                "type": type(exc).__name__,
                                "message": str(exc),
                            },
                        },
                    )
                raise AppError(f"failed to start app {app.id!r}: {exc}") from exc
        finally:
            log_handle.close()

        record = {
            "pid": process.pid,
            "pgid": process.pid,
            "process_started_at": process_started_at(process.pid),
            "status": "running",
            "started_at": utc_now(),
            "stopped_at": "",
            "path": str(app.path),
            "command": list(app.command),
            "url": app.url,
            "health_url": app.health_url,
            "log_path": str(logs),
        }
        records[app.id] = record
        save_registry(system, registry)

        time.sleep(STARTUP_EXIT_GRACE_SECONDS)
        exit_code = process.poll()
        if exit_code is not None:
            record.update(
                {
                    "pid": 0,
                    "status": "failed",
                    "exit_code": exit_code,
                    "stopped_at": utc_now(),
                }
            )
            records[app.id] = record
            save_registry(system, registry)
            tail = read_log_tail(logs, start=log_start)
            message = f"app {app.id!r} exited during startup with code {exit_code}"
            if store:
                store.append_event(
                    "app.start_failed",
                    source="1context-daemon",
                    actor="daemon",
                    subject=app.id,
                    text=f"Failed to start {app.label}.",
                    payload={
                        "app": app.to_payload(),
                        "pid": process.pid,
                        "exit_code": exit_code,
                        "log_path": str(logs),
                        "log_tail": tail,
                    },
                )
            raise AppError(f"{message}\n{tail}" if tail else message)

        if store:
            store.append_event(
                "app.started",
                source="1context-daemon",
                actor="daemon",
                subject=app.id,
                text=f"Started {app.label}.",
                payload={"app": app.to_payload(), "pid": process.pid, "log_path": str(logs)},
            )
        return {"app": app.to_payload(), **record}


def stop_app(system: MemorySystem, app_id: str, *, store: LakeStore | None = None) -> dict[str, Any]:
    with exclusive_file_lock(registry_lock_path(system)):
        app = app_by_id(system, app_id)
        registry = load_registry(system)
        records = registry_apps(registry)
        record = registry_record(records.get(app.id))
        pid = parse_positive_int(record.get("pid"))
        if not pid_running(pid):
            record.update({"pid": 0, "status": "stopped", "stopped_at": utc_now()})
            records[app.id] = record
            save_registry(system, registry)
            return {"app": app.to_payload(), "pid": pid, "status": "already_stopped"}

        if not app_process_matches(app, record, pid):
            record.update({"pid": 0, "status": "stale_pid", "stopped_at": utc_now()})
            records[app.id] = record
            save_registry(system, registry)
            return {"app": app.to_payload(), "pid": pid, "status": "stale_pid_refused"}

        terminate_process_group(pid)
        stopped = wait_until_stopped(pid, timeout=3.0)
        if not stopped:
            kill_process_group(pid)
            stopped = wait_until_stopped(pid, timeout=2.0)

        status = "stopped" if stopped else "failed_to_stop"
        record.update({"pid": 0 if stopped else pid, "status": status, "stopped_at": utc_now()})
        records[app.id] = record
        save_registry(system, registry)
        if store:
            store.append_event(
                "app.stopped" if stopped else "app.stop_failed",
                source="1context-daemon",
                actor="daemon",
                subject=app.id,
                text=f"{'Stopped' if stopped else 'Failed to stop'} {app.label}.",
                payload={"app": app.to_payload(), "pid": pid, "status": status},
            )
        return {"app": app.to_payload(), "pid": pid, "status": status}


def open_app(system: MemorySystem, app_id: str) -> dict[str, Any]:
    app = app_by_id(system, app_id)
    if not app.url:
        raise AppError(f"app {app.id!r} has no url")
    subprocess.run(["open", app.url], check=True)
    return {"app": app.to_payload(), "status": "opened", "url": app.url}


def pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_process_group(pid: int) -> None:
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        return


def kill_process_group(pid: int) -> None:
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except ProcessLookupError:
        return


def wait_until_stopped(pid: int, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_running(pid):
            return True
        time.sleep(0.1)
    return not pid_running(pid)


def app_process_matches(app: AppDefinition, record: dict[str, Any], pid: int) -> bool:
    expected_raw = record.get("command") or list(app.command)
    if not isinstance(expected_raw, list) or not expected_raw:
        return False
    actual_command = process_command(pid)
    if not actual_command:
        return False
    actual = normalize_command_tokens(split_command_tokens(actual_command))
    if not actual:
        return False
    expected = normalize_command_tokens([str(part) for part in expected_raw])
    if not expected:
        return False
    if not command_tokens_match(expected, actual) and not command_phrase_matches(expected, actual):
        return False

    identity_verified = False
    expected_pgid = parse_positive_int(record.get("pgid"))
    if expected_pgid:
        try:
            current_pgid = os.getpgid(pid)
        except ProcessLookupError:
            return False
        if current_pgid != expected_pgid:
            return False
        identity_verified = True
    expected_started = str(record.get("process_started_at") or "").strip()
    if expected_started:
        current_started = process_started_at(pid)
        if not current_started or current_started != expected_started:
            return False
        identity_verified = True
    try:
        expected_path = Path(str(record.get("path") or app.path)).resolve()
    except (OSError, RuntimeError, ValueError):
        expected_path = app.path.resolve()
    current_cwd = process_cwd(pid)
    if current_cwd and current_cwd != expected_path:
        return False
    if current_cwd:
        identity_verified = True
    if not identity_verified:
        return False
    return True


def normalize_command_tokens(tokens: list[str]) -> list[str]:
    return [token for token in tokens if token and token != "--"]


def split_command_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def command_tokens_match(expected: list[str], actual: list[str]) -> bool:
    if len(actual) < len(expected):
        return False
    expected_exe = expected[0]
    expected_rest = expected[1:]
    max_start = len(actual) - len(expected)
    for start in range(max_start + 1):
        if not executable_tokens_match(expected_exe, actual[start]):
            continue
        if all(actual[start + index] == token for index, token in enumerate(expected_rest, start=1)):
            return True
    return False


def executable_tokens_match(expected: str, actual: str) -> bool:
    if expected == actual:
        return True
    return Path(expected).name == Path(actual).name


def command_phrase_matches(expected: list[str], actual: list[str]) -> bool:
    if not expected or not actual:
        return False
    expected_phrase = " ".join(expected)
    actual_phrase = " ".join(actual)
    return expected_phrase in actual_phrase


def parse_positive_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def registry_apps(payload: dict[str, Any]) -> dict[str, Any]:
    apps = payload.get("apps")
    if isinstance(apps, dict):
        return apps
    payload["apps"] = {}
    payload["corrupt_registry_apps"] = True
    return payload["apps"]


def registry_record(record: Any) -> dict[str, Any]:
    return record if isinstance(record, dict) else {}


def process_command(pid: int) -> str:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def process_started_at(pid: int) -> str:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def process_cwd(pid: int) -> Path | None:
    try:
        result = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("n"):
            try:
                return Path(line[1:]).resolve()
            except (OSError, RuntimeError, ValueError):
                return None
    return None


def url_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=0.5) as response:
            return 200 <= response.status < 500
    except (OSError, urllib.error.URLError):
        return False


def read_log_tail(path: Path, *, max_bytes: int = LOG_TAIL_BYTES, start: int = 0) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            lower_bound = max(0, start)
            handle.seek(max(lower_bound, size - max_bytes))
            text = handle.read().decode("utf-8", errors="replace")
            if size - lower_bound > max_bytes and "\n" in text:
                text = text.split("\n", 1)[1]
            return text.strip()
    except OSError:
        return ""
