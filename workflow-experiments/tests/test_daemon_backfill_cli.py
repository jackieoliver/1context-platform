from __future__ import annotations

import argparse

from onectx import cli


class FakeIngestResult:
    def __init__(self, payload: dict):
        self._payload = payload

    def to_payload(self) -> dict:
        return dict(self._payload)


def test_daemon_backfill_uses_fast_ingest_path(monkeypatch, capsys) -> None:
    calls = 0

    def fake_daemon_run_ingest(**kwargs):
        nonlocal calls
        calls += 1
        assert kwargs["max_seconds"] == 30
        return FakeIngestResult(
            {
                "ts": f"ingest-{calls}",
                "lines_scanned": 10,
                "events_imported": 5,
                "sessions_imported": 1,
                "artifacts_imported": 1,
                "elapsed_seconds": 1.25,
                "limited": False,
                "port_results": [],
                "ingest_event_id": f"event-{calls}",
            }
        )

    monkeypatch.setattr(cli, "daemon_run_ingest", fake_daemon_run_ingest)

    rc = cli.cmd_daemon_backfill(
        argparse.Namespace(
            root=None,
            plugin=None,
            experience_source=None,
            max_ticks=2,
            max_seconds=30,
            sleep=0,
            nice=0,
            json=False,
        )
    )

    out = capsys.readouterr().out
    assert rc == 0
    assert calls == 1
    assert "daemon backfill ingest-1" in out
    assert "events=5" in out


def test_daemon_ingest_prints_summary(monkeypatch, capsys) -> None:
    def fake_daemon_run_ingest(**kwargs):
        return FakeIngestResult(
            {
                "ts": "ingest-now",
                "lines_scanned": 20,
                "events_imported": 12,
                "sessions_imported": 2,
                "artifacts_imported": 1,
                "elapsed_seconds": 2.5,
                "limited": True,
                "port_results": [],
                "ingest_event_id": "ingest-event",
            }
        )

    monkeypatch.setattr(cli, "daemon_run_ingest", fake_daemon_run_ingest)

    rc = cli.cmd_daemon_ingest(
        argparse.Namespace(
            root=None,
            plugin=None,
            experience_source=None,
            max_seconds=10,
            nice=0,
            json=False,
        )
    )

    out = capsys.readouterr().out
    assert rc == 0
    assert "daemon ingest ingest-now" in out
    assert "limited=true" in out
    assert "id=ingest-event" in out
