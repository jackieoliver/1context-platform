from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal


WorkStatus = Literal["done", "retryable", "timeout", "stale_running", "failed", "needs_operator"]

DONE_OUTCOMES = {"done", "dry_run", "skipped", "no_change", "completed", "passed"}
FAILED_OUTCOMES = {"failed", "failure", "error"}
OPERATOR_OUTCOMES = {
    "blocked",
    "needs_approval",
    "needs_operator",
    "operator_review",
    "operator_required",
}
STARTED_EVENTS = {
    "harness.launch_started",
    "hired_agent.execution_started",
    "hired_agent.batch_started",
}
DONE_EVENTS = {
    "hired_agent.execution_completed",
    "hired_agent.batch_completed",
}
TIMEOUT_EVENTS = {"harness.launch_timed_out"}


@dataclass(frozen=True)
class WorkClassification:
    """Pure reconciliation result for one hired-agent work stream."""

    work_key: str
    status: WorkStatus
    reason: str
    hired_agent_uuid: str = ""
    run_id: str = ""
    job_ids: tuple[str, ...] = ()
    retryable: bool = False
    failure_kind: str = ""
    state_outcome: str = ""
    started_at: str = ""
    last_event_at: str = ""
    age_seconds: int | None = None
    last_event: dict[str, Any] = field(default_factory=dict)
    events: tuple[dict[str, Any], ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "work_key": self.work_key,
            "status": self.status,
            "reason": self.reason,
            "hired_agent_uuid": self.hired_agent_uuid,
            "run_id": self.run_id,
            "job_ids": list(self.job_ids),
            "retryable": self.retryable,
            "failure_kind": self.failure_kind,
            "state_outcome": self.state_outcome,
            "started_at": self.started_at,
            "last_event_at": self.last_event_at,
            "age_seconds": self.age_seconds,
            "last_event": dict(self.last_event),
            "events": [dict(event) for event in self.events],
        }


def classify_hired_agent_work(
    events: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    now: datetime | str | None = None,
    stale_after: timedelta = timedelta(minutes=30),
) -> list[WorkClassification]:
    """Classify recent ledger/lakestore events by hired-agent or run identity.

    The function accepts direct ledger records and lakestore event rows. Lakestore
    rows are normalized by parsing `payload_json`, then merging that payload with
    the row fields.
    """

    normalized = [normalize_event(event) for event in events]
    groups: dict[str, list[dict[str, Any]]] = {}
    for event in normalized:
        key = work_key(event)
        if key:
            groups.setdefault(key, []).append(event)

    reference_now = parse_ts(now) if now is not None else None
    if reference_now is None:
        reference_now = datetime.now(timezone.utc)
    results = [
        classify_work_events(key, group, now=reference_now, stale_after=stale_after)
        for key, group in groups.items()
    ]
    return sorted(results, key=lambda item: (item.last_event_at, item.work_key))


def classify_work_events(
    work_key_value: str,
    events: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    now: datetime,
    stale_after: timedelta,
) -> WorkClassification:
    ordered = sort_events(events)
    last_event = ordered[-1] if ordered else {}
    started_event = latest_event_named(ordered, STARTED_EVENTS)
    age_seconds = None
    if started_event:
        started_at_dt = timestamp_for_age(started_event)
        if started_at_dt is not None:
            age_seconds = max(0, int((now - started_at_dt).total_seconds()))

    for event in reversed(ordered):
        event_name = event_type(event)
        outcome = event_outcome(event)
        state_outcome = str(event.get("state_outcome") or "")
        failure_kind = str(event.get("failure_kind") or "")
        retryable = bool(event.get("retryable")) or state_outcome == "needs_retry"

        if outcome in OPERATOR_OUTCOMES or state_outcome in OPERATOR_OUTCOMES:
            return classification(
                work_key_value,
                ordered,
                "needs_operator",
                "latest terminal event requires operator attention",
                event,
                retryable=retryable,
                failure_kind=failure_kind,
                state_outcome=state_outcome,
                age_seconds=age_seconds,
            )
        if event_name in TIMEOUT_EVENTS or failure_kind == "timeout" or outcome == "timeout":
            return classification(
                work_key_value,
                ordered,
                "timeout",
                "harness timeout recorded",
                event,
                retryable=True,
                failure_kind=failure_kind or "timeout",
                state_outcome=state_outcome or "needs_retry",
                age_seconds=age_seconds,
            )
        if retryable:
            return classification(
                work_key_value,
                ordered,
                "retryable",
                "retryable failure recorded",
                event,
                retryable=True,
                failure_kind=failure_kind or "retryable_failure",
                state_outcome=state_outcome or "needs_retry",
                age_seconds=age_seconds,
            )
        if is_done_event(event_name, outcome):
            return classification(
                work_key_value,
                ordered,
                "done",
                "terminal success event recorded",
                event,
                failure_kind=failure_kind,
                state_outcome=state_outcome,
                age_seconds=age_seconds,
            )
        if is_failed_event(event_name, outcome):
            return classification(
                work_key_value,
                ordered,
                "failed",
                "terminal failure event recorded",
                event,
                failure_kind=failure_kind or "failure",
                state_outcome=state_outcome or "failed",
                age_seconds=age_seconds,
            )

    if started_event:
        if age_seconds is not None and age_seconds >= int(stale_after.total_seconds()):
            return classification(
                work_key_value,
                ordered,
                "stale_running",
                "launch started but no terminal event arrived before the stale threshold",
                started_event,
                retryable=True,
                failure_kind="stale_running",
                state_outcome="needs_retry",
                age_seconds=age_seconds,
            )
        return classification(
            work_key_value,
            ordered,
            "needs_operator",
            "launch has started but is not old enough to mark stale",
            started_event,
            age_seconds=age_seconds,
        )

    return classification(
        work_key_value,
        ordered,
        "needs_operator",
        "no known terminal or started event found",
        last_event,
        age_seconds=age_seconds,
    )


def normalize_event(record: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    embedded = record.get("payload")
    if isinstance(embedded, dict):
        payload.update(embedded)
    payload_json = record.get("payload_json")
    if isinstance(payload_json, str) and payload_json.strip():
        try:
            decoded = json.loads(payload_json)
        except json.JSONDecodeError:
            decoded = {}
        if isinstance(decoded, dict):
            payload.update(decoded)
    merged = {**record, **payload}
    first_error = first_error_record(merged)
    if first_error:
        for key in ("failure_kind", "retryable", "state_outcome"):
            if key not in merged or merged.get(key) in ("", None):
                merged[key] = first_error.get(key)
    merged.pop("payload", None)
    return merged


def classification(
    work_key_value: str,
    events: list[dict[str, Any]],
    status: WorkStatus,
    reason: str,
    source_event: dict[str, Any],
    *,
    retryable: bool = False,
    failure_kind: str = "",
    state_outcome: str = "",
    age_seconds: int | None = None,
) -> WorkClassification:
    return WorkClassification(
        work_key=work_key_value,
        status=status,
        reason=reason,
        hired_agent_uuid=first_value(events, "hired_agent_uuid"),
        run_id=first_value(events, "run_id"),
        job_ids=job_ids(events),
        retryable=retryable,
        failure_kind=failure_kind,
        state_outcome=state_outcome,
        started_at=event_ts(latest_event_named(events, STARTED_EVENTS) or {}),
        last_event_at=event_ts(events[-1] if events else {}),
        age_seconds=age_seconds,
        last_event=source_event,
        events=tuple(events),
    )


def work_key(event: dict[str, Any]) -> str:
    hired_agent_uuid = str(event.get("hired_agent_uuid") or "").strip()
    if hired_agent_uuid:
        return f"hired_agent:{hired_agent_uuid}"
    run_id = str(event.get("run_id") or "").strip()
    if run_id:
        return f"run:{run_id}"
    return ""


def sort_events(events: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    return sorted(events, key=lambda event: (parse_ts(event_ts(event)) or datetime.min.replace(tzinfo=timezone.utc)))


def latest_event_named(events: list[dict[str, Any]], names: set[str]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event_type(event) in names:
            return event
    return None


def is_done_event(event_name: str, outcome: str) -> bool:
    if event_name == "harness.launch_completed":
        return outcome in DONE_OUTCOMES
    if event_name in DONE_EVENTS:
        return outcome in DONE_OUTCOMES
    return outcome in DONE_OUTCOMES and event_name.startswith("artifact.")


def is_failed_event(event_name: str, outcome: str) -> bool:
    if event_name == "harness.launch_completed":
        return outcome in FAILED_OUTCOMES
    if event_name in DONE_EVENTS:
        return outcome in FAILED_OUTCOMES
    return outcome in FAILED_OUTCOMES and event_name.startswith(("hired_agent.", "artifact.", "harness."))


def event_type(event: dict[str, Any]) -> str:
    return str(event.get("event") or event.get("kind") or "")


def event_outcome(event: dict[str, Any]) -> str:
    return str(event.get("outcome") or event.get("status") or "").strip()


def event_ts(event: dict[str, Any]) -> str:
    return str(event.get("ts") or event.get("completed_at") or event.get("started_at") or "")


def timestamp_for_age(event: dict[str, Any]) -> datetime | None:
    return parse_ts(event.get("started_at") or event_ts(event))


def parse_ts(value: datetime | str | Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def first_value(events: list[dict[str, Any]], key: str) -> str:
    for event in events:
        value = str(event.get(key) or "").strip()
        if value:
            return value
    return ""


def first_error_record(event: dict[str, Any]) -> dict[str, Any]:
    errors = event.get("errors")
    if isinstance(errors, list):
        for item in errors:
            if isinstance(item, dict):
                return item
    batch = event.get("batch")
    if isinstance(batch, dict):
        batch_errors = batch.get("errors")
        if isinstance(batch_errors, list):
            for item in batch_errors:
                if isinstance(item, dict):
                    return item
    return {}


def job_ids(events: list[dict[str, Any]]) -> tuple[str, ...]:
    values: list[str] = []
    for event in events:
        raw = event.get("job_ids")
        if isinstance(raw, str):
            candidates = [raw]
        elif isinstance(raw, list | tuple):
            candidates = [str(item) for item in raw]
        else:
            candidates = []
        for candidate in candidates:
            if candidate and candidate not in values:
                values.append(candidate)
    return tuple(values)
