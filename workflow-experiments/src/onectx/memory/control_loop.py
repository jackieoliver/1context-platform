from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from onectx.config import MemorySystem
from onectx.memory.ledger import Ledger, ledger_events_path
from onectx.memory.reconcile import WorkClassification, classify_hired_agent_work, parse_ts
from onectx.state_machines.queue import (
    ACTIVE_STATUSES,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_NEEDS_RETRY,
    STATUS_QUEUED,
    STATUS_RUNNING,
    StateMachineWorkQueue,
    WorkItem,
)
from onectx.state_machines.runtime import (
    StateMachineRuntimeError,
    load_scope_state,
    persist_scope_state,
    record_transition_execution,
)
from onectx.storage import LakeStore


EXTERNAL_RECONCILIATION_ADAPTERS = {"growth_role_route"}


@dataclass(frozen=True)
class QueueTickOutcome:
    queue_id: str
    status: str
    event_id: str = ""
    queue_event_id: str = ""
    classification: dict[str, Any] | None = None
    error: str = ""

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "queue_id": self.queue_id,
            "status": self.status,
            "event_id": self.event_id,
            "queue_event_id": self.queue_event_id,
        }
        if self.classification is not None:
            payload["classification"] = self.classification
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass(frozen=True)
class QueueTickResult:
    queue_path: Path
    claimed_count: int = 0
    completed_count: int = 0
    retryable_count: int = 0
    failed_count: int = 0
    reconciled_count: int = 0
    skipped_count: int = 0
    outcomes: tuple[QueueTickOutcome, ...] = ()

    @property
    def touched_count(self) -> int:
        return self.claimed_count + self.reconciled_count

    def to_payload(self) -> dict[str, Any]:
        return {
            "queue_path": str(self.queue_path),
            "claimed_count": self.claimed_count,
            "completed_count": self.completed_count,
            "retryable_count": self.retryable_count,
            "failed_count": self.failed_count,
            "reconciled_count": self.reconciled_count,
            "skipped_count": self.skipped_count,
            "outcomes": [outcome.to_payload() for outcome in self.outcomes],
        }


def state_machine_queue_path(system: MemorySystem) -> Path:
    return system.runtime_dir / "state-machines" / "queue"


def run_state_machine_queue_tick(
    system: MemorySystem,
    *,
    queue_path: Path | None = None,
    limit: int = 10,
    now: datetime | str | None = None,
    reconcile_hired_agents: bool = True,
) -> QueueTickResult:
    """Advance a bounded batch of persisted state-machine work."""

    queue = StateMachineWorkQueue.load(queue_path or state_machine_queue_path(system))
    store = LakeStore(system.storage_dir)
    store.ensure()
    reference_now = parse_ts(now) if now is not None else None
    if reference_now is None:
        reference_now = datetime.now(timezone.utc)
    outcomes: list[QueueTickOutcome] = []
    classifications: dict[str, WorkClassification] = {}
    claimed_count = 0
    completed_count = 0
    retryable_count = 0
    failed_count = 0
    reconciled_count = 0
    skipped_count = 0
    remaining = max(0, int(limit))

    if reconcile_hired_agents and remaining:
        classifications = classifications_by_work_key(system, store=store, now=reference_now)
        for item in active_items(queue):
            if remaining <= 0:
                break
            classification = matching_classification(item, classifications)
            if classification is None or classification.status == "needs_operator":
                continue
            outcome = reconcile_work_item(
                system,
                queue,
                store,
                item,
                classification,
            )
            outcomes.append(outcome)
            remaining -= 1
            reconciled_count += 1
            if outcome.status == STATUS_DONE:
                completed_count += 1
            elif outcome.status == STATUS_NEEDS_RETRY:
                retryable_count += 1
            elif outcome.status == STATUS_FAILED:
                failed_count += 1

    if remaining:
        queue.reload()
        for item in due_items(queue, now=reference_now):
            if remaining <= 0:
                break
            if item_work_keys(item):
                skipped_count += 1
                continue
            outcome = execute_queue_item(system, queue, store, item)
            outcomes.append(outcome)
            remaining -= 1
            claimed_count += 1
            if outcome.status == STATUS_DONE:
                completed_count += 1
            elif outcome.status == STATUS_NEEDS_RETRY:
                retryable_count += 1
            elif outcome.status == STATUS_FAILED:
                failed_count += 1

    return QueueTickResult(
        queue_path=queue.path,
        claimed_count=claimed_count,
        completed_count=completed_count,
        retryable_count=retryable_count,
        failed_count=failed_count,
        reconciled_count=reconciled_count,
        skipped_count=skipped_count,
        outcomes=tuple(outcomes),
    )


def active_items(queue: StateMachineWorkQueue) -> list[WorkItem]:
    return [item for item in queue.list(ACTIVE_STATUSES) if item.status in ACTIVE_STATUSES]


def due_items(queue: StateMachineWorkQueue, *, now: datetime) -> list[WorkItem]:
    return [
        item
        for item in queue.list((STATUS_QUEUED, STATUS_NEEDS_RETRY))
        if scheduled(item, now=now)
    ]


def scheduled(item: WorkItem, *, now: datetime) -> bool:
    scheduled_at = parse_ts(item.scheduled_at)
    return scheduled_at is None or scheduled_at <= now


def execute_queue_item(
    system: MemorySystem,
    queue: StateMachineWorkQueue,
    store: LakeStore,
    item: WorkItem,
    *,
    classification: WorkClassification | None = None,
) -> QueueTickOutcome:
    running = item if item.status == STATUS_RUNNING else queue.mark_running(item.queue_id)
    claimed_event = append_queue_event(store, "state_machine.work.claimed", running)
    if running.status == STATUS_FAILED:
        failed_event = append_queue_event(
            store,
            "state_machine.work.failed",
            running,
            payload={"error": "attempt budget exhausted before claim"},
        )
        return QueueTickOutcome(
            queue_id=running.queue_id,
            status=STATUS_FAILED,
            event_id=claimed_event["event_id"],
            queue_event_id=failed_event["event_id"],
            error="attempt budget exhausted before claim",
        )

    if should_complete_external_reconciled_work(running, classification):
        return complete_external_reconciled_work(
            system,
            queue,
            store,
            running,
            claimed_event_id=claimed_event["event_id"],
            classification=classification,
        )

    try:
        source_state = running.source_state or persisted_source_state(system, running)
        if not source_state:
            raise StateMachineRuntimeError("queue item requires source_state or persisted scope state")
        execution = record_transition_execution(
            system,
            machine_id=running.machine,
            scope=running.scope,
            source_state=source_state,
            event_name=running.event,
            target_state=running.target_state,
            status="passed",
            produced_evidence=string_tuple(running.payload.get("produced_evidence")),
            completed_steps=string_tuple(running.payload.get("completed_steps")),
            emitted_events=string_tuple(running.payload.get("emitted_events")),
            note=str(running.payload.get("note") or ""),
        )
        scope_state = persist_scope_state(
            system,
            machine_id=running.machine,
            scope=running.scope,
            key=running.key,
            initial_state=source_state,
            terminal_state=execution.target_state,
            transitions=(execution,),
            status="completed",
            dry_run=False,
            note=f"state-machine queue item {running.queue_id}",
        )
    except Exception as exc:  # noqa: BLE001 - failed queue work is routed through retry policy.
        updated = queue.mark_retryable(
            running.queue_id,
            payload={
                "error": str(exc),
                "error_type": type(exc).__name__,
                "retryable": running.retryable,
            },
        )
        event = append_queue_event(
            store,
            "state_machine.work.retryable" if updated.status == STATUS_NEEDS_RETRY else "state_machine.work.failed",
            updated,
            payload={"error": str(exc), "error_type": type(exc).__name__},
        )
        return QueueTickOutcome(
            queue_id=updated.queue_id,
            status=updated.status,
            queue_event_id=event["event_id"],
            error=str(exc),
            classification=classification.to_payload() if classification else None,
        )

    emitted_event_ids = [
        append_emitted_event(store, running, event_name, execution=execution.to_payload())["event_id"]
        for event_name in execution.plan.summary["emits"]
    ]
    done = queue.mark_done(
        running.queue_id,
        payload={
            "transition_execution": execution.to_payload(),
            "scope_state": {
                "path": scope_state.get("path", ""),
                "state": scope_state.get("state", ""),
            },
            "emitted_event_ids": emitted_event_ids,
            "classification": classification.to_payload() if classification else {},
        },
    )
    event = append_queue_event(
        store,
        "state_machine.work.completed",
        done,
        payload={
            "transition_execution": execution.to_payload(),
            "scope_state_path": scope_state.get("path", ""),
            "emitted_event_ids": emitted_event_ids,
        },
    )
    return QueueTickOutcome(
        queue_id=done.queue_id,
        status=done.status,
        event_id=emitted_event_ids[-1] if emitted_event_ids else "",
        queue_event_id=event["event_id"],
        classification=classification.to_payload() if classification else None,
    )


def should_complete_external_reconciled_work(
    item: WorkItem,
    classification: WorkClassification | None,
) -> bool:
    adapter = str(item.payload.get("queue_adapter") or "")
    return classification is not None and adapter in EXTERNAL_RECONCILIATION_ADAPTERS


def complete_external_reconciled_work(
    system: MemorySystem,
    queue: StateMachineWorkQueue,
    store: LakeStore,
    item: WorkItem,
    *,
    claimed_event_id: str,
    classification: WorkClassification | None,
) -> QueueTickOutcome:
    source_state = item.source_state or persisted_source_state(system, item) or "queued"
    target_state = str(item.payload.get("completion_target_state") or item.target_state or source_state)
    scope_state = persist_scope_state(
        system,
        machine_id=item.machine,
        scope=item.scope,
        key=item.key,
        initial_state=source_state,
        terminal_state=target_state,
        transitions=(),
        status="completed",
        dry_run=False,
        note=f"external reconciliation completed state-machine queue item {item.queue_id}",
    )
    execution = {
        "kind": "external_reconciled_work_completion",
        "adapter": str(item.payload.get("queue_adapter") or ""),
        "queue_id": item.queue_id,
        "source_state": source_state,
        "target_state": target_state,
        "classification": classification.to_payload() if classification else {},
    }
    emitted_event_ids = [
        append_emitted_event(store, item, event_name, execution=execution)["event_id"]
        for event_name in string_tuple(item.payload.get("completion_events"))
    ]
    done = queue.mark_done(
        item.queue_id,
        payload={
            "external_reconciliation": execution,
            "claimed_event_id": claimed_event_id,
            "scope_state": {
                "path": scope_state.get("path", ""),
                "state": scope_state.get("state", ""),
            },
            "emitted_event_ids": emitted_event_ids,
            "classification": classification.to_payload() if classification else {},
        },
    )
    event = append_queue_event(
        store,
        "state_machine.work.completed",
        done,
        payload={
            "external_reconciliation": execution,
            "scope_state_path": scope_state.get("path", ""),
            "emitted_event_ids": emitted_event_ids,
        },
    )
    return QueueTickOutcome(
        queue_id=done.queue_id,
        status=done.status,
        event_id=emitted_event_ids[-1] if emitted_event_ids else "",
        queue_event_id=event["event_id"],
        classification=classification.to_payload() if classification else None,
    )


def reconcile_work_item(
    system: MemorySystem,
    queue: StateMachineWorkQueue,
    store: LakeStore,
    item: WorkItem,
    classification: WorkClassification,
) -> QueueTickOutcome:
    append_queue_event(
        store,
        "state_machine.work.reconciled",
        item,
        payload={"classification": classification.to_payload()},
    )
    if classification.status == "done":
        return execute_queue_item(system, queue, store, item, classification=classification)
    if classification.status in {"retryable", "timeout", "stale_running"}:
        running = item if item.status == STATUS_RUNNING else queue.mark_running(item.queue_id)
        updated = queue.mark_retryable(
            running.queue_id,
            payload={"classification": classification.to_payload()},
        )
        event = append_queue_event(
            store,
            "state_machine.work.retryable",
            updated,
            payload={"classification": classification.to_payload()},
        )
        return QueueTickOutcome(
            queue_id=updated.queue_id,
            status=updated.status,
            queue_event_id=event["event_id"],
            classification=classification.to_payload(),
        )
    updated = queue.mark_failed(
        item.queue_id,
        payload={"classification": classification.to_payload()},
    )
    event = append_queue_event(
        store,
        "state_machine.work.failed",
        updated,
        payload={"classification": classification.to_payload()},
    )
    return QueueTickOutcome(
        queue_id=updated.queue_id,
        status=updated.status,
        queue_event_id=event["event_id"],
        classification=classification.to_payload(),
    )


def classifications_by_work_key(
    system: MemorySystem,
    *,
    store: LakeStore,
    now: datetime,
) -> dict[str, WorkClassification]:
    events = [
        *Ledger(ledger_events_path(system.runtime_dir), storage_path=system.storage_dir).read(),
        *store.rows("events", limit=0),
    ]
    return {
        classification.work_key: classification
        for classification in classify_hired_agent_work(events, now=now)
    }


def matching_classification(
    item: WorkItem,
    classifications: dict[str, WorkClassification],
) -> WorkClassification | None:
    for key in item_work_keys(item):
        classification = classifications.get(key)
        if classification is not None:
            return classification
    return None


def item_work_keys(item: WorkItem) -> tuple[str, ...]:
    keys: list[str] = []
    work_key = str(item.payload.get("work_key") or "").strip()
    if work_key:
        keys.append(work_key)
    hired_agent_uuid = str(item.payload.get("hired_agent_uuid") or "").strip()
    if hired_agent_uuid:
        keys.append(f"hired_agent:{hired_agent_uuid}")
    run_id = str(item.payload.get("run_id") or "").strip()
    if run_id:
        keys.append(f"run:{run_id}")
    if item.scope == "hire" and item.key:
        keys.append(f"hired_agent:{item.key}")
    return tuple(dict.fromkeys(keys))


def persisted_source_state(system: MemorySystem, item: WorkItem) -> str:
    state = load_scope_state(system, machine_id=item.machine, scope=item.scope, key=item.key)
    return str(state.get("state") or "")


def append_queue_event(
    store: LakeStore,
    event_name: str,
    item: WorkItem,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return store.append_event(
        event_name,
        source="memory.tick",
        kind="state_machine_queue",
        actor="memory_tick",
        subject=item.queue_id,
        state_machine=item.machine,
        scope=item.scope,
        text=f"State-machine queue item {item.queue_id} {event_name.rsplit('.', 1)[-1]}.",
        payload={
            "queue_id": item.queue_id,
            "machine": item.machine,
            "scope": item.scope,
            "key": item.key,
            "event": item.event,
            "source_state": item.source_state,
            "target_state": item.target_state,
            "status": item.status,
            "attempts": item.attempts,
            **(payload or {}),
        },
    )


def append_emitted_event(
    store: LakeStore,
    item: WorkItem,
    event_name: str,
    *,
    execution: dict[str, Any],
) -> dict[str, Any]:
    return store.append_event(
        event_name,
        source="memory.tick",
        kind="state_machine",
        actor="memory_tick",
        subject=item.key,
        state_machine=item.machine,
        scope=item.scope,
        text=f"Emitted {event_name} from state-machine queue item {item.queue_id}.",
        payload={
            "queue_id": item.queue_id,
            "machine": item.machine,
            "scope": item.scope,
            "key": item.key,
            "event": item.event,
            "transition_execution": execution,
        },
    )


def string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item))
    return (str(value),)
