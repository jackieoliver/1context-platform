from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from onectx.memory.health import build_memory_health_payload
from onectx.memory.reconcile import classify_hired_agent_work
from onectx.memory.supervision import (
    SupervisionPolicy,
    plan_work_supervision,
    supervision_policy_from_system,
)
from onectx.config import load_system
from onectx.state_machines.queue import StateMachineWorkQueue
from onectx.storage import LakeStore


NOW = datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc)


def test_supervision_marks_stuck_running_work_retryable_with_backoff(tmp_path: Path) -> None:
    queue = StateMachineWorkQueue.load(tmp_path / "queue.json")
    item = queue.enqueue(
        machine="memory_system_fabric",
        scope="cycle",
        key="cycle-1",
        event="tick",
        max_attempts=3,
        now="2026-04-29T17:00:00Z",
    )
    running = queue.mark_running(item.queue_id, now="2026-04-29T17:40:00Z")

    plan = plan_work_supervision(
        [running],
        policy=SupervisionPolicy(stale_after_seconds=600, timeout_seconds=3600, backoff_initial_seconds=120),
        now=NOW,
    )

    [decision] = plan.decisions
    assert decision.status == "stuck"
    assert decision.action.kind == "mark_retryable"
    assert decision.action.scheduled_at == "2026-04-29T18:02:00Z"
    assert decision.action.payload["failure_kind"] == "stale_running"
    assert decision.action.payload["state_outcome"] == "needs_retry"


def test_supervision_dead_letters_timed_out_work_after_retry_budget_exhausted(tmp_path: Path) -> None:
    queue = StateMachineWorkQueue.load(tmp_path / "queue.json")
    item = queue.enqueue(
        machine="memory_system_fabric",
        scope="cycle",
        key="cycle-1",
        event="tick",
        max_attempts=1,
        now="2026-04-29T17:00:00Z",
    )
    running = queue.mark_running(item.queue_id, now="2026-04-29T17:01:00Z")

    plan = plan_work_supervision(
        [running],
        policy=SupervisionPolicy(stale_after_seconds=60, timeout_seconds=300),
        now=NOW,
    )

    [decision] = plan.decisions
    assert decision.status == "dead_letter"
    assert decision.action.kind == "dead_letter"
    assert decision.action.payload["failure_kind"] == "timeout"
    assert decision.action.payload["retryable"] is False


def test_supervision_classification_timeout_plans_retry_without_process_kill(tmp_path: Path) -> None:
    queue = StateMachineWorkQueue.load(tmp_path / "queue.json")
    item = queue.enqueue(
        machine="memory_system_fabric",
        scope="cycle",
        key="cycle-1",
        event="tick",
        payload={"run_id": "run-timeout"},
        max_attempts=2,
        now="2026-04-29T17:00:00Z",
    )
    running = queue.mark_running(item.queue_id, now="2026-04-29T17:01:00Z")
    [classification] = classify_hired_agent_work(
        [
            event("harness.launch_started", "2026-04-29T17:01:00Z", run_id="run-timeout", outcome="started"),
            event(
                "harness.launch_timed_out",
                "2026-04-29T17:04:00Z",
                run_id="run-timeout",
                outcome="timeout",
                failure_kind="timeout",
                retryable=True,
                state_outcome="needs_retry",
            ),
        ],
        now=NOW,
    )

    plan = plan_work_supervision(
        [running],
        classifications=[classification],
        policy=SupervisionPolicy(backoff_initial_seconds=30),
        now=NOW,
    )

    [decision] = plan.decisions
    assert decision.status == "timed_out"
    assert decision.action.kind == "mark_retryable"
    assert decision.action.payload["event"] == "memory.supervision.retry_scheduled"
    assert "kill" not in decision.action.kind


def test_supervision_respects_max_concurrency_capacity(tmp_path: Path) -> None:
    queue = StateMachineWorkQueue.load(tmp_path / "queue.json")
    running = queue.enqueue(
        machine="memory_system_fabric",
        scope="cycle",
        key="running",
        event="tick",
        now="2026-04-29T17:55:00Z",
    )
    running = queue.mark_running(running.queue_id, now="2026-04-29T17:59:00Z")
    queued_one = queue.enqueue(
        machine="memory_system_fabric",
        scope="cycle",
        key="queued-1",
        event="tick",
        now="2026-04-29T17:58:00Z",
    )
    queued_two = queue.enqueue(
        machine="memory_system_fabric",
        scope="cycle",
        key="queued-2",
        event="tick",
        now="2026-04-29T17:58:01Z",
    )

    plan = plan_work_supervision(
        [running, queued_one, queued_two],
        policy=SupervisionPolicy(max_concurrent_agents=2, stale_after_seconds=600, timeout_seconds=1800),
        now=NOW,
    )

    actions = {decision.queue_id: decision.action.kind for decision in plan.decisions}
    assert plan.running_count == 1
    assert plan.capacity_available == 1
    assert plan.launch_count == 1
    assert actions[queued_one.queue_id] == "launch"
    assert actions[queued_two.queue_id] == "defer_for_capacity"


def test_supervision_represents_final_artifact_validation_failure_as_retryable(tmp_path: Path) -> None:
    queue = StateMachineWorkQueue.load(tmp_path / "queue.json")
    item = queue.enqueue(
        machine="memory_system_fabric",
        scope="cycle",
        key="cycle-1",
        event="tick",
        payload={"artifact_validation": {"ok": False, "failures": ["missing summary.md"]}},
        max_attempts=2,
        now="2026-04-29T17:00:00Z",
    )
    running = queue.mark_running(item.queue_id, now="2026-04-29T17:01:00Z")

    plan = plan_work_supervision(
        [running],
        policy=SupervisionPolicy(backoff_initial_seconds=60),
        now=NOW,
    )

    [decision] = plan.decisions
    assert decision.status == "failed"
    assert decision.action.kind == "mark_retryable"
    assert decision.action.payload["failure_kind"] == "validation_failure"
    assert decision.action.payload["reason"] == "missing summary.md"


def test_supervision_defaults_to_eight_max_concurrent_agents() -> None:
    system = load_system(Path.cwd())

    policy = supervision_policy_from_system(system)

    assert policy.max_concurrent_agents == system.runtime_policy.get("max_concurrent_agents", 8)


def test_health_payload_reports_degraded_supervision_dead_letters(tmp_path: Path) -> None:
    queue = StateMachineWorkQueue.load(tmp_path / "queue.json")
    item = queue.enqueue(
        machine="memory_system_fabric",
        scope="cycle",
        key="cycle-1",
        event="tick",
        max_attempts=1,
        now="2026-04-29T17:00:00Z",
    )
    failed = queue.mark_failed(item.queue_id, now="2026-04-29T17:02:00Z")
    plan = plan_work_supervision([failed], now=NOW)
    store = LakeStore(tmp_path / "storage")

    payload = build_memory_health_payload(store, supervision_plan=plan)

    assert payload["status"] == "degraded"
    assert payload["summary"]["supervision_dead_letter_count"] == 1
    assert payload["supervision"]["summary"]["dead_letter_count"] == 1


def event(name: str, ts: str, *, run_id: str, **values: object) -> dict[str, object]:
    return {
        "ts": ts,
        "event": name,
        "run_id": run_id,
        "job_ids": ["memory.hourly.scribe"],
        **values,
    }
