from __future__ import annotations

import json
from pathlib import Path

import pytest

from onectx.daemon.cursors import CursorStore
from onectx.ports import PortDefinition
from onectx.ports import PortError, load_ports
from onectx.ports import sessions as session_import
from onectx.ports.sessions import import_session_port
from onectx.storage import LakeStore


def test_since_filters_sources_but_does_not_prune_existing_events(tmp_path: Path) -> None:
    """The port `since` value is an import horizon, not retention."""

    store = LakeStore(tmp_path / "lakestore")
    store.ensure()
    store.append_event(
        "session.codex.imported",
        event_id="old-already-imported",
        hash="old-hash",
        session_id="existing-session",
        ts="2000-01-01T00:00:00Z",
        source="codex",
        kind="user",
        text="This old imported row must stay in the lake.",
    )

    source = tmp_path / "source" / "rollout-test.jsonl"
    source.parent.mkdir()
    source.write_text(
        '{"timestamp":"2000-01-01T00:00:00Z","type":"response_item",'
        '"payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"old source row"}]}}\n',
        encoding="utf-8",
    )

    port = PortDefinition(
        id="codex_sessions",
        label="Codex Sessions",
        kind="session_log",
        adapter="codex_rollout_jsonl",
        enabled=True,
        directions=("input",),
        paths=(str(source),),
        stores=("storage.events", "storage.sessions", "storage.artifacts"),
        purpose="test",
        source_path=tmp_path / "codex_sessions.toml",
        since="2099-01-01T00:00:00Z",
    )
    cursors = CursorStore.load(tmp_path / "cursors" / "daemon.json")

    result = import_session_port(root=tmp_path, port=port, store=store, cursors=cursors)

    assert result.events_imported == 0
    assert store.counts()["events"] == 1
    rows = store.rows("events", limit=0)
    assert [row["event_id"] for row in rows] == ["old-already-imported"]


def test_unchanged_session_file_does_not_load_dedupe_sets(tmp_path: Path, monkeypatch) -> None:
    """A quiet daemon tick should not scan large Lance columns."""

    store = LakeStore(tmp_path / "lakestore")
    store.ensure()

    source = tmp_path / "source" / "rollout-test.jsonl"
    source.parent.mkdir()
    source.write_text(
        '{"timestamp":"2026-04-01T00:00:00Z","type":"response_item",'
        '"payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"hello"}]}}\n',
        encoding="utf-8",
    )

    port = PortDefinition(
        id="codex_sessions",
        label="Codex Sessions",
        kind="session_log",
        adapter="codex_rollout_jsonl",
        enabled=True,
        directions=("input",),
        paths=(str(source),),
        stores=("storage.events", "storage.sessions", "storage.artifacts"),
        purpose="test",
        source_path=tmp_path / "codex_sessions.toml",
        since="all",
    )
    cursors = CursorStore.load(tmp_path / "cursors" / "daemon.json")
    cursors.set(
        f"{port.id}:{source.resolve()}",
        {
            "offset": source.stat().st_size,
            "mtime_ns": source.stat().st_mtime_ns,
            "path": str(source.resolve()),
            "port_id": port.id,
            "adapter": port.adapter,
        },
    )

    def fail_existing_values(*args, **kwargs):
        raise AssertionError("quiet import should not load dedupe columns")

    monkeypatch.setattr(session_import, "existing_values", fail_existing_values)

    result = import_session_port(root=tmp_path, port=port, store=store, cursors=cursors)

    assert result.files_seen == 1
    assert result.events_imported == 0


def test_fast_ingest_can_ignore_tick_event_cap(tmp_path: Path) -> None:
    store = LakeStore(tmp_path / "lakestore")
    store.ensure()

    source = tmp_path / "source" / "rollout-test.jsonl"
    source.parent.mkdir()
    source.write_text(
        "\n".join(
            [
                '{"timestamp":"2026-04-01T00:00:00Z","type":"session_meta",'
                '"payload":{"id":"session-a","cwd":"/tmp"}}',
                '{"timestamp":"2026-04-01T00:00:01Z","type":"response_item",'
                '"payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"one"}]}}',
                '{"timestamp":"2026-04-01T00:00:02Z","type":"response_item",'
                '"payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"two"}]}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    port = PortDefinition(
        id="codex_sessions",
        label="Codex Sessions",
        kind="session_log",
        adapter="codex_rollout_jsonl",
        enabled=True,
        directions=("input",),
        paths=(str(source),),
        stores=("storage.events", "storage.sessions", "storage.artifacts"),
        purpose="test",
        source_path=tmp_path / "codex_sessions.toml",
        since="all",
        max_events_per_tick=1,
    )
    cursors = CursorStore.load(tmp_path / "cursors" / "daemon.json")

    result = import_session_port(
        root=tmp_path,
        port=port,
        store=store,
        cursors=cursors,
        respect_limits=False,
    )

    assert result.events_imported == 2
    assert not result.limited


def test_malformed_jsonl_line_is_skipped_without_blocking_later_events(tmp_path: Path) -> None:
    store = LakeStore(tmp_path / "lakestore")
    store.ensure()
    source = tmp_path / "source" / "rollout-test.jsonl"
    source.parent.mkdir()
    source.write_text(
        "\n".join(
            [
                '{"timestamp":"2026-04-01T00:00:00Z","type":"response_item",'
                '"payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"first"}]}}',
                "{bad json",
                '{"timestamp":"2026-04-01T00:01:00Z","type":"response_item",'
                '"payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"second"}]}}',
                "",
            ]
        ),
        encoding="utf-8",
    )
    port = PortDefinition(
        id="codex_sessions",
        label="Codex Sessions",
        kind="session_log",
        adapter="codex_rollout_jsonl",
        enabled=True,
        directions=("input",),
        paths=(str(source),),
        stores=("storage.events",),
        purpose="test",
        source_path=tmp_path / "codex_sessions.toml",
        since="all",
    )

    result = import_session_port(root=tmp_path, port=port, store=store, cursors=CursorStore.load(tmp_path / "cursors.json"))

    assert result.events_imported == 2
    assert result.events_skipped == 1
    assert store.counts()["events"] == 2


def test_bad_cursor_offset_is_treated_as_zero(tmp_path: Path) -> None:
    store = LakeStore(tmp_path / "lakestore")
    store.ensure()
    source = tmp_path / "source" / "rollout-test.jsonl"
    source.parent.mkdir()
    source.write_text(
        '{"timestamp":"2026-04-01T00:00:00Z","type":"response_item",'
        '"payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"hello"}]}}\n',
        encoding="utf-8",
    )
    port = PortDefinition(
        id="codex_sessions",
        label="Codex Sessions",
        kind="session_log",
        adapter="codex_rollout_jsonl",
        enabled=True,
        directions=("input",),
        paths=(str(source),),
        stores=("storage.events",),
        purpose="test",
        source_path=tmp_path / "codex_sessions.toml",
        since="all",
    )
    cursors = CursorStore.load(tmp_path / "cursors.json")
    cursors.set(f"{port.id}:{source}", {"offset": "not-an-int"})

    result = import_session_port(root=tmp_path, port=port, store=store, cursors=cursors)

    assert result.events_imported == 1


def test_corrupt_cursor_json_loads_empty_store(tmp_path: Path) -> None:
    path = tmp_path / "storage" / "cursors" / "daemon.json"
    path.parent.mkdir(parents=True)
    path.write_text("{bad json", encoding="utf-8")

    store = CursorStore.load(path)

    assert store.get("anything") == {}
    assert store.data["corrupt_cursor_path"] == str(path)


def test_port_paths_must_be_array(tmp_path: Path) -> None:
    ports = tmp_path / "ports"
    ports.mkdir()
    (ports / "codex.toml").write_text(
        "\n".join(
            [
                'id = "codex"',
                'adapter = "codex_rollout_jsonl"',
                "enabled = true",
                'directions = ["input"]',
                'paths = "not-an-array"',
                'stores = ["storage.events"]',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(PortError, match="paths must be an array"):
        load_ports(tmp_path)


def test_parser_state_resets_when_offset_rewinds_to_zero(tmp_path: Path) -> None:
    source = tmp_path / "source" / "rollout-test.jsonl"
    source.parent.mkdir()
    source.write_text(
        '{"timestamp":"2026-04-01T00:00:00Z","type":"response_item",'
        '"payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"hello"}]}}\n',
        encoding="utf-8",
    )
    port = PortDefinition(
        id="codex_sessions",
        label="Codex Sessions",
        kind="session_log",
        adapter="codex_rollout_jsonl",
        enabled=True,
        directions=("input",),
        paths=(str(source),),
        stores=("storage.events",),
        purpose="test",
        source_path=tmp_path / "codex_sessions.toml",
        since="all",
    )
    cursor = {"parser_state": {"session_id": "stale-session", "cwd": "/old/cwd"}}

    state = session_import.parser_state_from_cursor(cursor, port, source, 0)

    assert state["session_id"] == session_import.default_session_id(port.adapter, source)
    assert state["cwd"] == ""


def test_import_tolerates_source_file_removed_before_finalize(tmp_path: Path, monkeypatch) -> None:
    store = LakeStore(tmp_path / "lakestore")
    store.ensure()
    source = tmp_path / "source" / "rollout-test.jsonl"
    source.parent.mkdir()
    source.write_text(
        '{"timestamp":"2026-04-01T00:00:00Z","type":"response_item",'
        '"payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"first"}]}}\n',
        encoding="utf-8",
    )
    raw_line = source.read_bytes()
    parsed_row = json.loads(raw_line.decode("utf-8"))
    port = PortDefinition(
        id="codex_sessions",
        label="Codex Sessions",
        kind="session_log",
        adapter="codex_rollout_jsonl",
        enabled=True,
        directions=("input",),
        paths=(str(source),),
        stores=("storage.events", "storage.artifacts"),
        purpose="test",
        source_path=tmp_path / "codex_sessions.toml",
        since="all",
    )
    cursors = CursorStore.load(tmp_path / "cursors.json")

    def remove_before_finalize(path: Path, offset: int):
        del offset
        yield parsed_row, raw_line, len(raw_line)
        path.unlink(missing_ok=True)

    monkeypatch.setattr(session_import, "iter_jsonl", remove_before_finalize)

    result = import_session_port(root=tmp_path, port=port, store=store, cursors=cursors)

    assert result.events_imported == 1
    assert store.counts()["events"] == 1


def test_missing_cursor_mtime_rewinds_for_recreated_file(tmp_path: Path, monkeypatch) -> None:
    store = LakeStore(tmp_path / "lakestore")
    store.ensure()
    source = tmp_path / "source" / "rollout-test.jsonl"
    source.parent.mkdir()
    source.write_text(
        '{"timestamp":"2026-04-01T00:00:00Z","type":"response_item",'
        '"payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"first"}]}}\n',
        encoding="utf-8",
    )
    raw_line = source.read_bytes()
    parsed_row = json.loads(raw_line.decode("utf-8"))
    port = PortDefinition(
        id="codex_sessions",
        label="Codex Sessions",
        kind="session_log",
        adapter="codex_rollout_jsonl",
        enabled=True,
        directions=("input",),
        paths=(str(source),),
        stores=("storage.events",),
        purpose="test",
        source_path=tmp_path / "codex_sessions.toml",
        since="all",
    )
    key = f"{port.id}:{source.resolve()}"
    cursors = CursorStore.load(tmp_path / "cursors.json")
    original_iter_jsonl = session_import.iter_jsonl

    def remove_before_finalize(path: Path, offset: int):
        del offset
        yield parsed_row, raw_line, len(raw_line)
        path.unlink(missing_ok=True)

    monkeypatch.setattr(session_import, "iter_jsonl", remove_before_finalize)
    first = import_session_port(root=tmp_path, port=port, store=store, cursors=cursors)
    monkeypatch.setattr(session_import, "iter_jsonl", original_iter_jsonl)

    assert first.events_imported == 1
    assert cursors.get(key).get("mtime_ns") == 0

    source.write_text(
        '{"timestamp":"2026-04-01T00:10:00Z","type":"response_item",'
        '"payload":{"type":"message","role":"user","content":[{"type":"input_text",'
        '"text":"second message long long long long long long long long long"}]}}\n',
        encoding="utf-8",
    )

    second = import_session_port(root=tmp_path, port=port, store=store, cursors=cursors)

    assert second.events_imported == 1
    assert second.events_skipped == 0
    assert [row["text"] for row in store.rows("events", limit=0)] == [
        "first",
        "second message long long long long long long long long long",
    ]


def test_empty_file_truncation_persists_rewind_cursor(tmp_path: Path) -> None:
    store = LakeStore(tmp_path / "lakestore")
    store.ensure()
    source = tmp_path / "source" / "rollout-test.jsonl"
    source.parent.mkdir()
    source.write_text(
        '{"timestamp":"2026-04-01T00:00:00Z","type":"response_item",'
        '"payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"first"}]}}\n',
        encoding="utf-8",
    )
    old_size = source.stat().st_size
    port = PortDefinition(
        id="codex_sessions",
        label="Codex Sessions",
        kind="session_log",
        adapter="codex_rollout_jsonl",
        enabled=True,
        directions=("input",),
        paths=(str(source),),
        stores=("storage.events",),
        purpose="test",
        source_path=tmp_path / "codex_sessions.toml",
        since="all",
    )
    key = f"{port.id}:{source.resolve()}"
    cursors = CursorStore.load(tmp_path / "cursors.json")
    cursors.set(
        key,
        {
            "offset": old_size,
            "mtime_ns": source.stat().st_mtime_ns,
            "path": str(source.resolve()),
            "port_id": port.id,
            "adapter": port.adapter,
            "parser_state": {"session_id": "old-session", "cwd": "/old"},
        },
    )
    cursors.save()

    source.write_text("", encoding="utf-8")
    result = import_session_port(root=tmp_path, port=port, store=store, cursors=cursors)

    assert result.files_changed == 1
    assert cursors.get(key).get("offset") == 0
    assert cursors.get(key).get("parser_state", {}).get("cwd") == ""


def test_rotation_during_import_forces_cursor_rewind(tmp_path: Path, monkeypatch) -> None:
    store = LakeStore(tmp_path / "lakestore")
    store.ensure()
    source = tmp_path / "source" / "rollout-test.jsonl"
    source.parent.mkdir()
    source.write_text(
        '{"timestamp":"2026-04-01T00:00:00Z","type":"response_item",'
        '"payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"first"}]}}\n',
        encoding="utf-8",
    )
    raw_line = source.read_bytes()
    parsed_row = json.loads(raw_line.decode("utf-8"))
    port = PortDefinition(
        id="codex_sessions",
        label="Codex Sessions",
        kind="session_log",
        adapter="codex_rollout_jsonl",
        enabled=True,
        directions=("input",),
        paths=(str(source),),
        stores=("storage.events",),
        purpose="test",
        source_path=tmp_path / "codex_sessions.toml",
        since="all",
    )
    key = f"{port.id}:{source.resolve()}"
    cursors = CursorStore.load(tmp_path / "cursors.json")

    def rotate_after_first_line(path: Path, offset: int):
        del offset
        yield parsed_row, raw_line, len(raw_line)
        rotated = path.with_suffix(".rotated")
        rotated.write_text(
            '{"timestamp":"2026-04-01T00:01:00Z","type":"response_item",'
            '"payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"rotated"}]}}\n',
            encoding="utf-8",
        )
        rotated.replace(path)

    monkeypatch.setattr(session_import, "iter_jsonl", rotate_after_first_line)

    result = import_session_port(root=tmp_path, port=port, store=store, cursors=cursors)

    assert result.events_imported == 1
    assert cursors.get(key).get("offset") == 0


def test_cursor_store_accepts_lower_offset_for_newer_mtime(tmp_path: Path) -> None:
    path = tmp_path / "cursors" / "daemon.json"
    key = "codex_sessions:/tmp/source.jsonl"

    store = CursorStore.load(path)
    store.set(key, {"offset": 5000, "path": "/tmp/source.jsonl", "mtime_ns": 100})
    store.save()

    reloaded = CursorStore.load(path)
    reloaded.set(key, {"offset": 200, "path": "/tmp/source.jsonl", "mtime_ns": 200})
    reloaded.save()

    merged = CursorStore.load(path).get(key)
    assert int(merged.get("offset") or 0) == 200


def test_cursor_store_accepts_lower_offset_when_identity_changes(tmp_path: Path) -> None:
    path = tmp_path / "cursors" / "daemon.json"
    key = "codex_sessions:/tmp/source.jsonl"

    store = CursorStore.load(path)
    store.set(
        key,
        {
            "offset": 5000,
            "path": "/tmp/source.jsonl",
            "mtime_ns": 100,
            "dev": 1,
            "ino": 111,
        },
    )
    store.save()

    reloaded = CursorStore.load(path)
    reloaded.set(
        key,
        {
            "offset": 0,
            "path": "/tmp/source.jsonl",
            "mtime_ns": 100,
            "dev": 1,
            "ino": 222,
        },
    )
    reloaded.save()

    merged = CursorStore.load(path).get(key)
    assert int(merged.get("offset") or 0) == 0
