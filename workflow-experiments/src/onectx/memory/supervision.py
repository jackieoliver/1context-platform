from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from onectx.config import MemorySystem
from onectx.memory.reconcile import WorkClassification, parse_ts
from onectx.memory.runner import parse_duration_seconds
from onectx.state_machines.queue import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_NEEDS_RETRY,
    STATUS_QUEUED,
    STATUS_RUNNING,
    WorkItem,
)


DEFAULT_MAX_CONCURRENT_AGENTS = 8
DEFAULT_STALE_AFTER_SECONDS = 10 * 60
DEFAULT_BACKOFF_INITIAL_SECONDS = 60
DEFAULT_BACKOFF_MULTIPLIER = 2.0
DEFAULT_BACKOFF_MAX_SECONDS = 30 * 60

SupervisionStatus = Literal[
    "queued",
    "running",
    "stuck",
    "timed_out",
    "failed",
    "retryable",
    "dead_letter",
    "completed",
]
SupervisionActionKind = Literal[
    "launch",
    "defer_for_capacity",
    "mark_done",
    "mark_retryable",
    "mark_failed",
    "dead_letter",
    "observe",
]

SUPERVISION_STATUSES = {
    "queued",
    "running",
    "stuck",
    "timed_out",
    "failed",
    "retryable",
    "dead_letter",
    "completed",
}
SUPERVISION_ACTION_KINDS = {
    "launch",
    "defer_for_capacity",
    "mark_done",
    "mark_retryable",
    "mark_failed",
    "dead_letter",
    "observe",
}


@dataclass(frozen=True)
class SupervisionPolicy:
    max_concurrent_agents: int = DEFAULT_MAX_CONCURRENT_AGENTS
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS
    timeout_seconds: int = 30 * 60
    backoff_initial_seconds: int = DEFAULT_BACKOFF_INITIAL_SECONDS
    backoff_multiplier: float = DEFAULT_BACKOFF_MULTIPLIER
    backoff_max_seconds: int = DEFAULT_BACKOFF_MAX_SECONDS

    def __post_init__(self) -> None:
        if self.max_concurrent_agents < 1:
            raise ValueError("max_concurrent_agents must be >= 1")
        if self.stale_after_seconds < 1:
            raise ValueError("stale_after_seconds must be >= 1")
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be >= 1")
        if self.backoff_initial_seconds < 1:
            raise ValueError("backoff_initial_seconds must be >= 1")
        if self.backoff_multiplier < 1:
            raise ValueError("backoff_multiplier must be >= 1")
        if self.backoff_max_seconds < 1:
            raise ValueError("backoff_max_seconds must be >= 1")

    def to_payload(self) -> dict[str, Any]:
        return {
            "max_concurrent_agents": self.max_concurrent_agents,
            "stale_after_seconds": self.stale_after_seconds,
            "timeout_seconds": self.timeout_seconds,
            "backoff_initial_seconds": self.backoff_initial_seconds,
            "backoff_multiplier": self.backoff_multiplier,
            "backoff_max_seconds": self.backoff_max_seconds,
        }


@dataclass(frozen=True)
class SupervisionAction:
    kind: SupervisionActionKind
    queue_id: str
    reason: str
    scheduled_at: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "queue_id": self.queue_id,
            "reason": self.reason,
            "scheduled_at": self.scheduled_at,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class SupervisionDecision:
    queue_id: str
    status: SupervisionStatus
    reason: str
    action: SupervisionAction
    attempts: int
    max_attempts: int
    attempts_remaining: int
    scheduled_at: str = ""
    age_seconds: int | None = None
    classification: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "queue_id": self.queue_id,
            "status": self.status,
            "reason": self.reason,
            "action": self.action.to_payload(),
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "attempts_remaining": self.attempts_remaining,
            "scheduled_at": self.scheduled_at,
            "age_seconds": self.age_seconds,
            "classification": dict(self.classification),
        }


@dataclass(frozen=True)
class SupervisionPlan:
    generated_at: str
    policy: SupervisionPolicy
    capacity_available: int
    running_count: int
    launch_count: int
    decisions: tuple[SupervisionDecision, ...]

    @property
    def stuck_count(self) -> int:
        return self.count("stuck")

    @property
    def timed_out_count(self) -> int:
        return self.count("timed_out")

    @property
    def dead_letter_count(self) -> int:
        return self.count("dead_letter")

    @property
    def retryable_count(self) -> int:
        return self.count("retryable")

    @property
    def failed_count(self) -> int:
        return self.count("failed")

    @property
    def completed_count(self) -> int:
        return self.count("completed")

    def count(self, status: str) -> int:
        return sum(1 for decision in self.decisions if decision.status == status)

    def to_payload(self) -> dict[str, Any]:
        status_counts = {
            status: self.count(status)
            for status in sorted(SUPERVISION_STATUSES)
            if self.count(status)
        }
        action_counts: dict[str, int] = {}
        for decision in self.decisions:
            action_counts[decision.action.kind] = action_counts.get(decision.action.kind, 0) + 1
        return {
            "kind": "memory_supervision_plan.v1",
            "generated_at": self.generated_at,
            "policy": self.policy.to_payload(),
            "capacity": {
                "max_concurrent_agents": self.policy.max_concurrent_agents,
                "running_count": self.running_count,
                "capacity_available": self.capacity_available,
                "launch_count": self.launch_count,
            },
            "summary": {
                "decision_count": len(self.decisions),
                "status_counts": status_counts,
                "action_counts": dict(sorted(action_counts.items())),
                "stuck_count": self.stuck_count,
                "timed_out_count": self.timed_out_count,
                "retryable_count": self.retryable_count,
                "dead_letter_count": self.dead_letter_count,
                "failed_count": self.failed_count,
                "completed_count": self.completed_count,
            },
            "decisions": [decision.to_payload() for decision in self.decisions],
        }


def supervision_policy_from_system(
    system: MemorySystem,
    *,
    max_concurrent_agents: int | None = None,
    stale_after_seconds: int | None = None,
    timeout_seconds: int | None = None,
) -> SupervisionPolicy:
    runtime_policy = system.runtime_policy if isinstance(system.runtime_policy, dict) else {}
    resolved_max = max_concurrent_agents
    if resolved_max is None:
        resolved_max = int(runtime_policy.get("max_concurrent_agents") or DEFAULT_MAX_CONCURRENT_AGENTS)
    resolved_timeout = timeout_seconds
    if resolved_timeout is None:
        resolved_timeout = parse_duration_seconds(runtime_policy.get("agent_timeout", "30m"))
    return SupervisionPolicy(
        max_concurrent_agents=max(1, int(resolved_max)),
        stale_after_seconds=max(1, int(stale_after_seconds or DEFAULT_STALE_AFTER_SECONDS)),
        timeout_seconds=max(1, int(resolved_timeout)),
    )


def plan_work_supervision(
    items: list[WorkItem] | tuple[WorkItem, ...],
    *,
    classifications: list[WorkClassification] | tuple[WorkClassification, ...] = (),
    policy: SupervisionPolicy | None = None,
    now: datetime | str | None = None,
) -> SupervisionPlan:
    reference_now = parse_ts(now) if now is not None else None
    if reference_now is None:
        reference_now = datetime.now(timezone.utc)
    resolved_policy = policy or SupervisionPolicy()
    classification_by_key = {classification.work_key: classification for classification in classifications}

    preliminary = [
        classify_queue_item(
            item,
            classification=classification_for_item(item, classification_by_key),
            policy=resolved_policy,
            now=reference_now,
        )
        for item in sorted(items, key=lambda item: (item.scheduled_at or "", item.created_at or "", item.queue_id))
    ]

    running_count = sum(1 for decision in preliminary if decision.status == "running")
    capacity_available = max(0, resolved_policy.max_concurrent_agents - running_count)
    launch_count = 0
    decisions: list[SupervisionDecision] = []
    for decision in preliminary:
        item = next(item for item in items if item.queue_id == decision.queue_id)
        if is_launch_candidate(item, decision, reference_now):
            if launch_count < capacity_available:
                launch_count += 1
                decisions.append(
                    replace_action(
                        decision,
                        action=SupervisionAction(
                            "launch",
                            decision.queue_id,
                            "capacity available for queued or retryable work",
                            payload=queue_action_payload(item, decision),
                        ),
                    )
                )
            else:
                decisions.append(
                    replace_action(
                        decision,
                        action=SupervisionAction(
                            "defer_for_capacity",
                            decision.queue_id,
                            "max concurrent hired-agent capacity is already allocated",
                            payload=queue_action_payload(item, decision),
                        ),
                    )
                )
        else:
            decisions.append(decision)

    return SupervisionPlan(
        generated_at=isoformat(reference_now),
        policy=resolved_policy,
        capacity_available=capacity_available,
        running_count=running_count,
        launch_count=launch_count,
        decisions=tuple(decisions),
    )


def classify_queue_item(
    item: WorkItem,
    *,
    classification: WorkClassification | None,
    policy: SupervisionPolicy,
    now: datetime,
) -> SupervisionDecision:
    artifact_failure = artifact_validation_failure(item, classification)
    if item.status == STATUS_DONE:
        return decision(item, "completed", "queue item is terminal done", "observe")
    if item.status == STATUS_FAILED:
        return decision(item, "dead_letter", "queue item is terminal failed", "observe")
    if artifact_failure:
        return retry_or_dead_letter(item, "failed", artifact_failure, policy, now, failure_kind="validation_failure")

    if classification is not None:
        payload = classification.to_payload()
        if classification.status == "done":
            return decision(
                item,
                "completed",
                "hired-agent reconciliation found terminal success",
                "mark_done",
                classification=payload,
                action_payload={"event": "memory.supervision.completed", "classification": payload},
            )
        if classification.status == "timeout":
            return retry_or_dead_letter(
                item,
                "timed_out",
                classification.reason,
                policy,
                now,
                failure_kind=classification.failure_kind or "timeout",
                classification=payload,
            )
        if classification.status == "stale_running":
            return retry_or_dead_letter(
                item,
                "stuck",
                classification.reason,
                policy,
                now,
                failure_kind=classification.failure_kind or "stale_running",
                classification=payload,
            )
        if classification.status == "retryable":
            return retry_or_dead_letter(
                item,
                "retryable",
                classification.reason,
                policy,
                now,
                failure_kind=classification.failure_kind or "retryable_failure",
                classification=payload,
            )
        if classification.status == "failed":
            return decision(
                item,
                "failed",
                classification.reason,
                "mark_failed",
                classification=payload,
                action_payload={"event": "memory.supervision.failed", "classification": payload},
            )

    if item.status == STATUS_RUNNING:
        age_seconds = item_age_seconds(item, now)
        if age_seconds >= policy.timeout_seconds:
            return retry_or_dead_letter(
                item,
                "timed_out",
                "running queue item exceeded the supervision timeout",
                policy,
                now,
                failure_kind="timeout",
                age_seconds=age_seconds,
            )
        if age_seconds >= policy.stale_after_seconds:
            return retry_or_dead_letter(
                item,
                "stuck",
                "running queue item exceeded the stale threshold",
                policy,
                now,
                failure_kind="stale_running",
                age_seconds=age_seconds,
            )
        return decision(item, "running", "running queue item is within supervision thresholds", "observe", age_seconds=age_seconds)

    if item.status == STATUS_NEEDS_RETRY:
        if not item.retryable or item.attempts_remaining <= 0:
            return decision(
                item,
                "dead_letter",
                "retry budget is exhausted",
                "dead_letter",
                action_payload={"event": "memory.supervision.dead_letter", "reason": "retry budget is exhausted"},
            )
        return decision(item, "retryable", "queue item is waiting for retry capacity", "observe")

    if item.status == STATUS_QUEUED:
        return decision(item, "queued", "queue item is waiting for launch capacity", "observe")

    return decision(item, "failed", f"unsupported queue status {item.status!r}", "mark_failed")


def retry_or_dead_letter(
    item: WorkItem,
    status: SupervisionStatus,
    reason: str,
    policy: SupervisionPolicy,
    now: datetime,
    *,
    failure_kind: str,
    classification: dict[str, Any] | None = None,
    age_seconds: int | None = None,
) -> SupervisionDecision:
    retryable = item.retryable and item.attempts_remaining > 0
    payload = {
        "event": "memory.supervision.retry_scheduled" if retryable else "memory.supervision.dead_letter",
        "failure_kind": failure_kind,
        "retryable": retryable,
        "state_outcome": "needs_retry" if retryable else "failed",
        "reason": reason,
    }
    if classification:
        payload["classification"] = classification
    if retryable:
        scheduled_at = isoformat(now + timedelta(seconds=backoff_seconds(item, policy)))
        return decision(
            item,
            status,
            reason,
            "mark_retryable",
            scheduled_at=scheduled_at,
            age_seconds=age_seconds,
            classification=classification or {},
            action_payload=payload,
        )
    return decision(
        item,
        "dead_letter",
        "retry budget is exhausted or the work is not retryable",
        "dead_letter",
        age_seconds=age_seconds,
        classification=classification or {},
        action_payload=payload,
    )


def decision(
    item: WorkItem,
    status: SupervisionStatus,
    reason: str,
    action_kind: SupervisionActionKind,
    *,
    scheduled_at: str = "",
    age_seconds: int | None = None,
    classification: dict[str, Any] | None = None,
    action_payload: dict[str, Any] | None = None,
) -> SupervisionDecision:
    return SupervisionDecision(
        queue_id=item.queue_id,
        status=status,
        reason=reason,
        action=SupervisionAction(
            action_kind,
            item.queue_id,
            reason,
            scheduled_at=scheduled_at,
            payload=queue_action_payload(item, None, extra=action_payload),
        ),
        attempts=item.attempts,
        max_attempts=item.max_attempts,
        attempts_remaining=item.attempts_remaining,
        scheduled_at=scheduled_at or item.scheduled_at,
        age_seconds=age_seconds,
        classification=classification or {},
    )


def replace_action(decision_value: SupervisionDecision, *, action: SupervisionAction) -> SupervisionDecision:
    return SupervisionDecision(
        queue_id=decision_value.queue_id,
        status=decision_value.status,
        reason=decision_value.reason,
        action=action,
        attempts=decision_value.attempts,
        max_attempts=decision_value.max_attempts,
        attempts_remaining=decision_value.attempts_remaining,
        scheduled_at=decision_value.scheduled_at,
        age_seconds=decision_value.age_seconds,
        classification=decision_value.classification,
    )


def classification_for_item(
    item: WorkItem,
    classification_by_key: dict[str, WorkClassification],
) -> WorkClassification | None:
    payload = item.payload
    keys = [
        str(payload.get("supervision_key") or ""),
        f"hired_agent:{payload.get('hired_agent_uuid')}" if payload.get("hired_agent_uuid") else "",
        f"run:{payload.get('run_id')}" if payload.get("run_id") else "",
    ]
    job_ids = payload.get("job_ids")
    if isinstance(job_ids, list | tuple):
        for classification in classification_by_key.values():
            if set(str(item) for item in job_ids) & set(classification.job_ids):
                return classification
    for key in keys:
        if key and key in classification_by_key:
            return classification_by_key[key]
    return None


def is_launch_candidate(item: WorkItem, decision_value: SupervisionDecision, now: datetime) -> bool:
    if decision_value.action.kind != "observe":
        return False
    if item.status not in {STATUS_QUEUED, STATUS_NEEDS_RETRY}:
        return False
    scheduled_at = parse_ts(item.scheduled_at)
    return scheduled_at is None or scheduled_at <= now


def artifact_validation_failure(item: WorkItem, classification: WorkClassification | None) -> str:
    for candidate in (
        item.payload.get("artifact_validation"),
        item.payload.get("validation"),
        item.payload.get("evidence"),
    ):
        if isinstance(candidate, dict) and candidate.get("ok") is False:
            failures = candidate.get("failures")
            if isinstance(failures, list) and failures:
                return "; ".join(str(failure) for failure in failures)
            return "final artifact validation failed"
    if classification is not None:
        evidence = classification.last_event.get("evidence")
        if isinstance(evidence, dict) and evidence.get("ok") is False:
            return "final artifact validation failed"
    return ""


def backoff_seconds(item: WorkItem, policy: SupervisionPolicy) -> int:
    exponent = max(0, item.attempts - 1)
    seconds = int(policy.backoff_initial_seconds * (policy.backoff_multiplier ** exponent))
    return max(1, min(policy.backoff_max_seconds, seconds))


def item_age_seconds(item: WorkItem, now: datetime) -> int:
    started = parse_ts(item.updated_at) or parse_ts(item.scheduled_at) or parse_ts(item.created_at)
    if started is None:
        return 0
    return max(0, int((now - started).total_seconds()))


def queue_action_payload(
    item: WorkItem,
    decision_value: SupervisionDecision | None,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "queue_id": item.queue_id,
        "machine": item.machine,
        "scope": item.scope,
        "key": item.key,
        "event": item.event,
        "queue_status": item.status,
        "attempts": item.attempts,
        "max_attempts": item.max_attempts,
        "attempts_remaining": item.attempts_remaining,
    }
    if decision_value is not None:
        payload["supervision_status"] = decision_value.status
    if extra:
        payload.update(extra)
    return payload


def isoformat(value: datetime) -> str:
    resolved = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
