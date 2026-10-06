from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from onectx.config import load_system
from onectx.memory.control_loop import run_state_machine_queue_tick, state_machine_queue_path
from onectx.memory.ledger import Ledger, ledger_events_path
from onectx.memory.tick import load_memory_cycle, run_memory_tick
from onectx.state_machines.queue import (
    STATUS_DONE,
    STATUS_NEEDS_RETRY,
    StateMachineWorkQueue,
)
from onectx.state_machines.runtime import load_scope_state
from onectx.storage import LakeStore


def isolated_system(tmp_path: Path):
    return replace(
        load_system(Path.cwd()),
        runtime_dir=tmp_path / "runtime",
        storage_dir=tmp_path / "lakestore",
    )


def test_state_machine_queue_tick_claims_and_completes_transition(tmp_path: Path) -> None:
    system = isolated_system(tmp_path)
    queue = StateMachineWorkQueue.load(state_machine_queue_path(system))
    item = queue.enqueue(
        machine="memory_system_fabric",
        scope="cycle",
        key="queue-cycle-ok",
        event="memory.work_available",
        source_state="idle",
        target_state="ingesting",
        now="2026-04-29T12:00:00Z",
    )

    result = run_state_machine_queue_tick(system, limit=1, now="2026-04-29T12:01:00Z")

    assert result.claimed_count == 1
    assert result.completed_count == 1
    updated = StateMachineWorkQueue.load(state_machine_queue_path(system)).get(item.queue_id)
    assert updated.status == STATUS_DONE
    assert updated.attempts == 1
    assert updated.payload["transition_execution"]["target_state"] == "ingesting"
    scope_state = load_scope_state(
        system,
        machine_id="memory_system_fabric",
        scope="cycle",
        key="queue-cycle-ok",
    )
    assert scope_state["state"] == "ingesting"
    events = LakeStore(system.storage_dir).rows("events", limit=0)
    assert {row["event"] for row in events} >= {
        "state_machine.work.claimed",
        "state_machine.work.completed",
        "memory.events.ready",
    }


def test_state_machine_queue_tick_marks_failed_transition_retryable(tmp_path: Path) -> None:
    system = isolated_system(tmp_path)
    queue = StateMachineWorkQueue.load(state_machine_queue_path(system))
    item = queue.enqueue(
        machine="memory_system_fabric",
        scope="cycle",
        key="queue-cycle-retry",
        event="missing.transition",
        source_state="idle",
        target_state="ingesting",
        max_attempts=2,
        now="2026-04-29T12:00:00Z",
    )

    result = run_state_machine_queue_tick(system, limit=1, now="2026-04-29T12:01:00Z")

    assert result.claimed_count == 1
    assert result.retryable_count == 1
    updated = StateMachineWorkQueue.load(state_machine_queue_path(system)).get(item.queue_id)
    assert updated.status == STATUS_NEEDS_RETRY
    assert updated.attempts == 1
    assert updated.retryable is True
    assert updated.payload["error_type"] == "StateMachineRuntimeError"
    assert "transition not found" in updated.payload["error"]


def test_hired_agent_reconcile_classifies_finished_artifact_and_completes_queue_item(tmp_path: Path) -> None:
    system = isolated_system(tmp_path)
    queue = StateMachineWorkQueue.load(state_machine_queue_path(system))
    item = queue.enqueue(
        machine="memory_system_fabric",
        scope="cycle",
        key="queue-cycle-reconciled",
        event="memory.work_available",
        source_state="idle",
        target_state="ingesting",
        payload={"hired_agent_uuid": "agent-reconcile-1", "run_id": "run-reconcile-1"},
        now="2026-04-29T12:00:00Z",
    )
    ledger = Ledger(ledger_events_path(system.runtime_dir), storage_path=system.storage_dir)
    ledger.append(
        "harness.launch_started",
        hired_agent_uuid="agent-reconcile-1",
        run_id="run-reconcile-1",
        job_ids=["memory.hourly.scribe"],
        outcome="started",
        started_at="2026-04-29T12:01:00Z",
    )
    ledger.append(
        "artifact.validated",
        hired_agent_uuid="agent-reconcile-1",
        run_id="run-reconcile-1",
        job_ids=["memory.hourly.scribe"],
        outcome="done",
        evidence={"ok": True},
    )
    ledger.append(
        "hired_agent.execution_completed",
        hired_agent_uuid="agent-reconcile-1",
        run_id="run-reconcile-1",
        job_ids=["memory.hourly.scribe"],
        outcome="done",
    )

    result = run_state_machine_queue_tick(system, limit=1, now="2026-04-29T12:05:00Z")

    assert result.reconciled_count == 1
    assert result.completed_count == 1
    updated = StateMachineWorkQueue.load(state_machine_queue_path(system)).get(item.queue_id)
    assert updated.status == STATUS_DONE
    assert updated.payload["classification"]["status"] == "done"
    assert updated.payload["classification"]["work_key"] == "hired_agent:agent-reconcile-1"
    events = LakeStore(system.storage_dir).rows("events", limit=0)
    assert {row["event"] for row in events} >= {
        "state_machine.work.reconciled",
        "state_machine.work.completed",
        "memory.events.ready",
    }


def test_memory_tick_runs_persisted_state_machine_queue_batch(tmp_path: Path) -> None:
    system = isolated_system(tmp_path)
    queue = StateMachineWorkQueue.load(state_machine_queue_path(system))
    queue.enqueue(
        machine="memory_system_fabric",
        scope="cycle",
        key="queue-cycle-from-memory-tick",
        event="memory.work_available",
        source_state="idle",
        target_state="ingesting",
        now="2026-04-29T12:00:00Z",
    )

    result = run_memory_tick(
        system,
        wiki_only=True,
        execute_render=False,
        cycle_id="queue-control-loop-cycle",
    )

    assert result.status == "completed"
    payload = load_memory_cycle(system, "queue-control-loop-cycle")
    assert payload["state_machine_queue"]["claimed_count"] == 1
    assert payload["state_machine_queue"]["completed_count"] == 1
    assert any(step["id"] == "state_machine_queue" for step in payload["steps"])
    queue_payload = json.loads(
        (state_machine_queue_path(system) / "state_machine_work_queue.json").read_text(encoding="utf-8")
    )
    assert queue_payload["items"][0]["status"] == STATUS_DONE
