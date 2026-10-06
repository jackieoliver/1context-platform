from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from onectx.config import MemorySystem
from onectx.io_utils import atomic_write_json
from onectx.memory.ledger import Ledger, ledger_events_path
from onectx.state_machines.growth import (
    GROWTH_PLAN_KIND,
    OUTCOME_JOB,
    GrowthDecision,
    GrowthPlan,
    PlannedWork,
)
from onectx.state_machines.queue import StateMachineWorkQueue
from onectx.storage import LakeStore, stable_id, utc_now


GROWTH_QUEUE_ADAPTER = "growth_role_route"
DEFAULT_CONCURRENCY_TARGET = 8
DEFAULT_ROUTE_BUDGET = {
    "model_family": "claude-opus",
    "max_prompt_tokens": 256000,
    "timeout_minutes": 30,
    "priority": "normal",
    "can_batch": False,
}


@dataclass(frozen=True)
class GrowthQueueResult:
    plan_id: str
    queue_path: Path
    artifact_path: Path
    artifact_id: str
    content_hash: str
    enqueued_count: int
    non_job_count: int
    event_count: int
    evidence_count: int
    route_plan: dict[str, list[dict[str, Any]]]
    queue_items: tuple[dict[str, Any], ...]
    events: tuple[dict[str, Any], ...]
    evidence: tuple[dict[str, Any], ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": "growth_queue_result",
            "plan_id": self.plan_id,
            "queue_path": str(self.queue_path),
            "artifact_path": str(self.artifact_path),
            "artifact_id": self.artifact_id,
            "content_hash": self.content_hash,
            "enqueued_count": self.enqueued_count,
            "non_job_count": self.non_job_count,
            "event_count": self.event_count,
            "evidence_count": self.evidence_count,
            "route_plan": self.route_plan,
            "queue_items": list(self.queue_items),
            "events": list(self.events),
            "evidence": list(self.evidence),
        }


def persist_growth_plan_to_queue(
    system: MemorySystem,
    plan: GrowthPlan,
    *,
    queue_path: Path | None = None,
    artifact_path: Path | None = None,
    now: str | None = None,
) -> GrowthQueueResult:
    """Persist a pure growth plan into the existing queue/evidence surfaces."""

    timestamp = now or utc_now()
    plan_id = plan.resolved_plan_id
    store = LakeStore(system.storage_dir)
    store.ensure()
    ledger = Ledger(ledger_events_path(system.runtime_dir), storage_path=system.storage_dir)
    artifact = write_growth_plan_artifact(
        system,
        plan,
        store=store,
        artifact_path=artifact_path,
        now=timestamp,
    )

    queue = StateMachineWorkQueue.load(queue_path or growth_queue_path(system))
    queue_items: list[dict[str, Any]] = []
    route_plan: dict[str, list[dict[str, Any]]] = {}
    events: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = [artifact["evidence"]]

    for decision in plan.decisions:
        if decision.outcome == OUTCOME_JOB:
            for work in decision.work:
                item, route_row = enqueue_growth_work(
                    queue,
                    plan_id=plan_id,
                    decision=decision,
                    work=work,
                    now=timestamp,
                )
                queue_items.append(item.to_payload())
                route_plan.setdefault(str(route_row.get("route_group") or decision.route_group or "growth_jobs"), []).append(route_row)
                events.append(
                    ledger.append(
                        "wiki.growth.job_enqueued",
                        plugin_id=system.active_plugin,
                        plan_id=plan_id,
                        decision_id=decision.decision_id,
                        work_id=work.work_id,
                        queue_id=item.queue_id,
                        job_id=str(route_row.get("job") or ""),
                        job_ids=list(item.payload.get("job_ids") or []),
                        job_key=str(route_row.get("job_key") or ""),
                        route_id=str(route_row.get("route_id") or ""),
                        route_group=str(route_row.get("route_group") or ""),
                        run_id=str(item.payload.get("run_id") or ""),
                        work_key=str(item.payload.get("work_key") or ""),
                        outcome="queued",
                        summary=f"Queued growth role job {route_row.get('job_key', item.key)}.",
                    )
                )
            continue

        recorded = record_non_job_decision(
            system,
            store,
            ledger,
            plan_id=plan_id,
            decision=decision,
            artifact_id=artifact["artifact_id"],
            now=timestamp,
        )
        events.extend(recorded["events"])
        evidence.extend(recorded["evidence"])

    return GrowthQueueResult(
        plan_id=plan_id,
        queue_path=queue.path,
        artifact_path=artifact["path"],
        artifact_id=artifact["artifact_id"],
        content_hash=artifact["content_hash"],
        enqueued_count=len(queue_items),
        non_job_count=len(plan.non_job_outcomes),
        event_count=len(events),
        evidence_count=len(evidence),
        route_plan=route_plan,
        queue_items=tuple(queue_items),
        events=tuple(events),
        evidence=tuple(evidence),
    )


def enqueue_growth_work(
    queue: StateMachineWorkQueue,
    *,
    plan_id: str,
    decision: GrowthDecision,
    work: PlannedWork,
    now: str,
) -> tuple[Any, dict[str, Any]]:
    route_row = growth_route_row(plan_id=plan_id, decision=decision, work=work)
    payload = growth_queue_payload(plan_id=plan_id, decision=decision, work=work, route_row=route_row)
    retry = payload.get("retry") if isinstance(payload.get("retry"), dict) else {}
    item = queue.upsert(
        machine=work.machine,
        scope=work.scope,
        key=work.key,
        event=work.event,
        source_state=work.source_state,
        target_state=work.target_state,
        queue_id=work.work_id,
        payload=payload,
        max_attempts=int(retry.get("max_attempts") or 3),
        retryable=bool(retry.get("retryable", True)),
        now=now,
    )
    return item, route_row


def growth_queue_payload(
    *,
    plan_id: str,
    decision: GrowthDecision,
    work: PlannedWork,
    route_row: dict[str, Any],
) -> dict[str, Any]:
    base = json_safe(work.payload)
    run_id = growth_run_id(plan_id=plan_id, decision=decision, work=work)
    job_id = str(route_row.get("job") or base.get("job") or "")
    job_ids = [job_id] if job_id else []
    work_key = f"run:{run_id}"
    return drop_empty(
        {
            **base,
            "kind": "wiki_growth_role_job",
            "queue_adapter": GROWTH_QUEUE_ADAPTER,
            "plan_id": plan_id,
            "decision_id": decision.decision_id,
            "work_id": work.work_id,
            "route_id": route_row["route_id"],
            "route_group": route_row["route_group"],
            "route_row": route_row,
            "job_id": job_id,
            "job_ids": job_ids,
            "run_id": run_id,
            "work_key": work_key,
            "completion_target_state": "done",
            "completion_events": ["wiki.growth.role_job.completed"],
            "classification_hooks": {
                "kind": "hired_agent_reconciliation",
                "work_key": work_key,
                "run_id": run_id,
                "job_ids": job_ids,
            },
            "concurrency": {
                "target": DEFAULT_CONCURRENCY_TARGET,
                "group": route_row["route_group"],
            },
        }
    )


def growth_route_row(*, plan_id: str, decision: GrowthDecision, work: PlannedWork) -> dict[str, Any]:
    payload = json_safe(work.payload)
    job_id = str(payload.get("job") or "")
    route_group = str(payload.get("route_group") or decision.route_group or "growth_jobs")
    route_id = stable_id(
        "wiki-growth-route",
        plan_id,
        decision.decision_id,
        work.work_id,
        job_id,
        payload.get("job_key") or work.key,
    )
    source_paths = list(payload.get("source_paths") or [])
    source_packet_text = json.dumps(
        {
            "source_signal": payload.get("source_signal", {}),
            "source_paths": source_paths,
        },
        sort_keys=True,
        default=str,
    )
    return drop_empty(
        {
            "route_id": route_id,
            "route_group": route_group,
            "job_key": str(payload.get("job_key") or work.key),
            "job": job_id,
            "outcome": "hire",
            "reason": str(payload.get("reason") or decision.reason),
            "ownership": payload.get("ownership", {}),
            "source_packet": {
                "kind": "wiki_growth_signal_packet",
                "mode": "growth_signal_metadata",
                "loaded_at_birth": True,
                "source_signal_id": str(payload.get("source_signal_id") or ""),
                "source_signal": payload.get("source_signal", {}),
                "source_paths": source_paths,
                "sha256": hashlib.sha256(source_packet_text.encode("utf-8")).hexdigest(),
            },
            "task_contract": f"{job_id} from dynamic wiki growth signal",
            "expected_outputs": list(payload.get("expected_outputs") or []),
            "validators": list(payload.get("validators") or []),
            "budget": dict(DEFAULT_ROUTE_BUDGET),
            "concurrency_group": route_group,
            "growth": {
                "plan_id": plan_id,
                "decision_id": decision.decision_id,
                "work_id": work.work_id,
            },
        }
    )


def record_non_job_decision(
    system: MemorySystem,
    store: LakeStore,
    ledger: Ledger,
    *,
    plan_id: str,
    decision: GrowthDecision,
    artifact_id: str,
    now: str,
) -> dict[str, tuple[dict[str, Any], ...]]:
    payload = {
        "plan_id": plan_id,
        "decision_id": decision.decision_id,
        "signal": decision.signal.to_payload(),
        "outcome": decision.outcome,
        "reason": decision.reason,
        "terminal": True,
        "artifact_id": artifact_id,
    }
    event_name = decision.events[0].name if decision.events else "wiki.growth.non_job_outcome_recorded"
    event = ledger.append(
        event_name,
        plugin_id=system.active_plugin,
        plan_id=plan_id,
        decision_id=decision.decision_id,
        signal_id=decision.signal.signal_id,
        signal_kind=decision.signal.kind,
        signal_key=decision.signal.key,
        outcome=decision.outcome,
        reason=decision.reason,
        terminal=True,
        summary=f"Growth signal {decision.signal.key} resolved as {decision.outcome}.",
    )
    evidence = store.append_evidence(
        "wiki_growth.non_job_outcome_recorded",
        artifact_id=artifact_id,
        status="passed",
        checker="memory.growth_runner",
        text=f"Growth decision {decision.decision_id} recorded terminal {decision.outcome} outcome.",
        checks=[
            "terminal_outcome_recorded",
            "signal_metadata_present",
            "reason_present",
        ],
        payload=payload,
    )
    store.append_event(
        "wiki.growth.non_job_outcome_recorded",
        ts=now,
        source="memory.growth_runner",
        kind="wiki_growth_decision",
        actor="growth_runner",
        subject=decision.decision_id,
        state_machine="wiki_growth_fabric",
        scope="agent_role",
        text=f"Growth signal {decision.signal.key} resolved as {decision.outcome}.",
        payload=payload,
    )
    return {"events": (event,), "evidence": (evidence,)}


def write_growth_plan_artifact(
    system: MemorySystem,
    plan: GrowthPlan,
    *,
    store: LakeStore,
    artifact_path: Path | None = None,
    now: str,
) -> dict[str, Any]:
    plan_id = plan.resolved_plan_id
    resolved_path = artifact_path or growth_plan_artifact_path(system, plan_id)
    payload = {
        **plan.to_payload(),
        "artifact_path": str(resolved_path),
        "written_at": now,
    }
    text = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    atomic_write_json(resolved_path, payload)
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    artifact_id = stable_id("artifact", GROWTH_PLAN_KIND, plan_id, content_hash)
    artifact_row = store.artifact_row(
        GROWTH_PLAN_KIND,
        artifact_id=artifact_id,
        uri=f"file://{resolved_path}",
        path=str(resolved_path),
        content_type="application/json",
        content_hash=content_hash,
        bytes=len(text.encode("utf-8")),
        source="memory.growth_runner",
        state="ready",
        text=f"state-machine growth plan {plan_id}",
        metadata={
            "plan_id": plan_id,
            "decision_count": len(plan.decisions),
            "work_count": len(plan.work),
            "non_job_count": len(plan.non_job_outcomes),
        },
    )
    store.replace_rows("artifacts", "artifact_id", [artifact_row])
    evidence = store.append_evidence(
        "dynamic_growth_plan.ready",
        artifact_id=artifact_id,
        status="passed",
        checker="memory.growth_runner",
        text="state-machine growth plan artifact written",
        checks=[
            "growth_plan_artifact_written",
            "decisions_are_typed",
            "planned_work_is_queue_compatible",
            "non_job_outcomes_are_first_class",
        ],
        payload={
            "plan_id": plan_id,
            "path": str(resolved_path),
            "decision_count": len(plan.decisions),
            "work_count": len(plan.work),
            "non_job_count": len(plan.non_job_outcomes),
        },
    )
    event = store.append_event(
        "wiki.growth.plan_persisted",
        ts=now,
        source="memory.growth_runner",
        actor="growth_runner",
        subject=plan_id,
        artifact_id=artifact_id,
        state_machine="wiki_growth_fabric",
        text=f"Persisted growth plan {plan_id}.",
        payload={
            "plan_id": plan_id,
            "path": str(resolved_path),
            "decision_count": len(plan.decisions),
            "work_count": len(plan.work),
            "non_job_count": len(plan.non_job_outcomes),
        },
    )
    return {
        "path": resolved_path,
        "artifact_id": artifact_id,
        "content_hash": content_hash,
        "event": event,
        "evidence": evidence,
    }


def growth_queue_path(system: MemorySystem) -> Path:
    return system.runtime_dir / "state-machines" / "queue"


def growth_plan_artifact_path(system: MemorySystem, plan_id: str) -> Path:
    return system.runtime_dir / "wiki" / "growth-plans" / f"{plan_id}.json"


def growth_run_id(*, plan_id: str, decision: GrowthDecision, work: PlannedWork) -> str:
    return stable_id("growth-run", plan_id, decision.decision_id, work.work_id)


def drop_empty(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value not in ("", None, [], {}, ())
    }


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, default=str))
