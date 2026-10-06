from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from onectx.memory.reconcile import WorkClassification, classify_hired_agent_work


NOW = datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc)


def test_reconcile_classifies_completed_hired_agent_run() -> None:
    results = classify_hired_agent_work(
        [
            event("hired_agent.created", "2026-04-29T17:00:00Z", outcome="done"),
            event("harness.launch_started", "2026-04-29T17:01:00Z", outcome="started", started_at="2026-04-29T17:01:00Z"),
            event("harness.launch_completed", "2026-04-29T17:02:00Z", outcome="done", returncode=0),
            event("artifact.validated", "2026-04-29T17:03:00Z", outcome="done", evidence={"ok": True}),
            event("hired_agent.execution_completed", "2026-04-29T17:04:00Z", outcome="done"),
        ],
        now=NOW,
        stale_after=timedelta(minutes=20),
    )

    [result] = results
    assert_result(result, status="done", retryable=False)
    assert result.reason == "terminal success event recorded"
    assert result.last_event["event"] == "hired_agent.execution_completed"
    assert result.job_ids == ("memory.hourly.scribe",)


def test_reconcile_classifies_timeout_as_retryable_timeout() -> None:
    results = classify_hired_agent_work(
        [
            event("hired_agent.created", "2026-04-29T17:00:00Z"),
            event("harness.launch_started", "2026-04-29T17:01:00Z", outcome="started", started_at="2026-04-29T17:01:00Z"),
            event(
                "harness.launch_timed_out",
                "2026-04-29T17:02:00Z",
                outcome="timeout",
                failure_kind="timeout",
                retryable=True,
                state_outcome="needs_retry",
                timeout_seconds=60,
            ),
        ],
        now=NOW,
    )

    [result] = results
    assert_result(result, status="timeout", retryable=True)
    assert result.failure_kind == "timeout"
    assert result.state_outcome == "needs_retry"
    assert result.last_event["event"] == "harness.launch_timed_out"


def test_reconcile_classifies_failed_harness_run() -> None:
    results = classify_hired_agent_work(
        [
            event("hired_agent.created", "2026-04-29T17:00:00Z"),
            event("harness.launch_started", "2026-04-29T17:01:00Z", outcome="started", started_at="2026-04-29T17:01:00Z"),
            event("harness.launch_completed", "2026-04-29T17:02:00Z", outcome="failure", returncode=2),
        ],
        now=NOW,
    )

    [result] = results
    assert_result(result, status="failed", retryable=False)
    assert result.failure_kind == "failure"
    assert result.state_outcome == "failed"


def test_reconcile_classifies_stale_started_launch_without_terminal_event() -> None:
    results = classify_hired_agent_work(
        [
            event("hired_agent.created", "2026-04-29T17:00:00Z"),
            event("harness.launch_started", "2026-04-29T17:01:00Z", outcome="started", started_at="2026-04-29T17:01:00Z"),
        ],
        now=NOW,
        stale_after=timedelta(minutes=30),
    )

    [result] = results
    assert_result(result, status="stale_running", retryable=True)
    assert result.failure_kind == "stale_running"
    assert result.state_outcome == "needs_retry"
    assert result.age_seconds == 3540


def test_reconcile_classifies_lakestore_retryable_batch_error_payload() -> None:
    ledger_payload = {
        "ts": "2026-04-29T17:05:00Z",
        "event": "hired_agent.batch_completed",
        "run_id": "run-retry",
        "outcome": "failure",
        "errors": [
            {
                "failure_kind": "exception",
                "retryable": True,
                "state_outcome": "needs_retry",
                "message": "temporary provider failure",
            }
        ],
    }
    lakestore_row = {
        "ts": "2026-04-29T17:05:00Z",
        "event": "hired_agent.batch_completed",
        "run_id": "run-retry",
        "payload_json": json.dumps(ledger_payload),
    }

    [result] = classify_hired_agent_work([lakestore_row], now=NOW)

    assert result.work_key == "run:run-retry"
    assert_result(result, status="retryable", retryable=True)
    assert result.failure_kind == "exception"
    assert result.state_outcome == "needs_retry"


def test_reconcile_classifies_operator_outcome() -> None:
    [result] = classify_hired_agent_work(
        [
            event(
                "hired_agent.execution_completed",
                "2026-04-29T17:10:00Z",
                outcome="needs_approval",
                state_outcome="needs_approval",
            )
        ],
        now=NOW,
    )

    assert_result(result, status="needs_operator", retryable=False)
    assert result.state_outcome == "needs_approval"


def event(name: str, ts: str, **values: object) -> dict[str, object]:
    return {
        "ts": ts,
        "event": name,
        "hired_agent_uuid": "urn:uuid:test-agent",
        "run_id": "run-test",
        "job_ids": ["memory.hourly.scribe"],
        **values,
    }


def assert_result(result: WorkClassification, *, status: str, retryable: bool) -> None:
    assert result.work_key == "hired_agent:urn:uuid:test-agent" or result.work_key == "run:run-retry"
    assert result.status == status
    assert result.retryable is retryable
