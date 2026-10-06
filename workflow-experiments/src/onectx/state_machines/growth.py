from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from onectx.storage import stable_id


GROWTH_PLAN_KIND = "state_machine_growth_plan"

OUTCOME_JOB = "job"
OUTCOME_SKIP = "skip"
OUTCOME_FORGET = "forget"
OUTCOME_DEFER = "defer"
OUTCOME_NO_JOB = "no_job"
OUTCOME_NO_CHANGE = "no_change"

TERMINAL_NON_JOB_OUTCOMES = {
    OUTCOME_SKIP,
    OUTCOME_FORGET,
    OUTCOME_DEFER,
    OUTCOME_NO_JOB,
    OUTCOME_NO_CHANGE,
}
VALID_OUTCOMES = {OUTCOME_JOB, *TERMINAL_NON_JOB_OUTCOMES}

WIKI_GROWTH_MACHINE = "wiki_growth_fabric"
DEFAULT_AGENT_SCOPE = "agent_role"
DEFAULT_JOB_EXPECTED_OUTPUTS = {
    "memory.wiki.biographer": ("biography.updated_or_skipped",),
    "memory.wiki.context_curator": ("your_context.updated_or_skipped",),
    "memory.wiki.contradiction_flagger": ("contradiction_entries.valid",),
    "memory.wiki.for_you_curator": ("article_section.updated_or_skipped",),
    "memory.wiki.historian": ("historian_entry.valid",),
    "memory.wiki.librarian": ("concept_page.created_expanded_or_deferred",),
    "memory.wiki.librarian_sweep": ("concept_sweep.decision_recorded",),
}
DEFAULT_JOB_VALIDATORS = {
    "memory.wiki.biographer": ("wiki_route_output.valid",),
    "memory.wiki.context_curator": ("wiki_route_output.valid",),
    "memory.wiki.contradiction_flagger": ("wiki_route_output.valid",),
    "memory.wiki.for_you_curator": ("wiki_route_output.valid",),
    "memory.wiki.historian": ("wiki_route_output.valid",),
    "memory.wiki.librarian": ("wiki_route_output.valid",),
    "memory.wiki.librarian_sweep": ("wiki_route_output.valid",),
}


@dataclass(frozen=True)
class GrowthSignal:
    """A fact that can reconfigure the wiki/memory fabric on the next tick."""

    kind: str
    key: str
    payload: dict[str, Any] = field(default_factory=dict)
    evidence: tuple[str, ...] = ()

    @classmethod
    def from_value(cls, value: Mapping[str, Any] | "GrowthSignal") -> "GrowthSignal":
        if isinstance(value, GrowthSignal):
            return value
        kind = str(value.get("kind") or value.get("type") or "").strip()
        key = str(value.get("key") or value.get("slug") or value.get("page_slug") or "").strip()
        if not kind:
            raise ValueError("growth signal requires kind")
        payload = {
            str(payload_key): payload_value
            for payload_key, payload_value in value.items()
            if payload_key not in {"kind", "type", "key", "evidence"}
        }
        evidence = string_tuple(value.get("evidence", ()))
        return cls(kind=kind, key=key or stable_id("growth-signal", kind, stable_json(payload)), payload=payload, evidence=evidence)

    @property
    def signal_id(self) -> str:
        return stable_id("growth-signal", self.kind, self.key, stable_json(self.payload), stable_json(self.evidence))

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "key": self.key,
            "signal_id": self.signal_id,
            "payload": json_safe(self.payload),
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class GrowthEvent:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {"name": self.name, "payload": json_safe(self.payload)}


@dataclass(frozen=True)
class PlannedWork:
    """Queue-friendly state-machine work produced by fabric growth."""

    machine: str
    scope: str
    key: str
    event: str
    source_state: str = ""
    target_state: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def work_id(self) -> str:
        return stable_id(
            "growth-work",
            self.machine,
            self.scope,
            self.key,
            self.event,
            self.source_state,
            self.target_state,
            stable_json(self.payload),
        )

    def to_queue_kwargs(self) -> dict[str, Any]:
        return {
            "machine": self.machine,
            "scope": self.scope,
            "key": self.key,
            "event": self.event,
            "source_state": self.source_state,
            "target_state": self.target_state,
            "payload": json_safe(self.payload),
            "queue_id": self.work_id,
        }

    def to_payload(self) -> dict[str, Any]:
        return {"work_id": self.work_id, **self.to_queue_kwargs()}


@dataclass(frozen=True)
class GrowthDecision:
    signal: GrowthSignal
    outcome: str
    reason: str
    work: tuple[PlannedWork, ...] = ()
    events: tuple[GrowthEvent, ...] = ()
    route_group: str = ""

    @property
    def decision_id(self) -> str:
        return stable_id(
            "growth-decision",
            self.signal.signal_id,
            self.outcome,
            self.reason,
            stable_json([item.to_payload() for item in self.work]),
            stable_json([item.to_payload() for item in self.events]),
        )

    @property
    def is_job(self) -> bool:
        return self.outcome == OUTCOME_JOB

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "decision_id": self.decision_id,
            "signal": self.signal.to_payload(),
            "outcome": self.outcome,
            "reason": self.reason,
            "work": [item.to_payload() for item in self.work],
            "events": [item.to_payload() for item in self.events],
        }
        if self.route_group:
            payload["route_group"] = self.route_group
        return payload


@dataclass(frozen=True)
class GrowthPlan:
    decisions: tuple[GrowthDecision, ...]
    plan_id: str = ""

    @property
    def work(self) -> tuple[PlannedWork, ...]:
        return tuple(item for decision in self.decisions for item in decision.work)

    @property
    def events(self) -> tuple[GrowthEvent, ...]:
        return tuple(item for decision in self.decisions for item in decision.events)

    @property
    def non_job_outcomes(self) -> tuple[GrowthDecision, ...]:
        return tuple(decision for decision in self.decisions if not decision.is_job)

    @property
    def resolved_plan_id(self) -> str:
        return self.plan_id or stable_id("growth-plan", stable_json([item.to_payload() for item in self.decisions]))

    def to_payload(self) -> dict[str, Any]:
        counts: dict[str, int] = {outcome: 0 for outcome in sorted(VALID_OUTCOMES)}
        for decision in self.decisions:
            counts[decision.outcome] = counts.get(decision.outcome, 0) + 1
        return {
            "kind": GROWTH_PLAN_KIND,
            "plan_id": self.resolved_plan_id,
            "decision_count": len(self.decisions),
            "work_count": len(self.work),
            "event_count": len(self.events),
            "outcome_counts": counts,
            "decisions": [item.to_payload() for item in self.decisions],
            "work": [item.to_payload() for item in self.work],
            "events": [item.to_payload() for item in self.events],
            "non_job_outcomes": [item.to_payload() for item in self.non_job_outcomes],
        }


def plan_wiki_memory_growth(
    signals: Iterable[Mapping[str, Any] | GrowthSignal],
    *,
    plan_id: str = "",
) -> GrowthPlan:
    """Derive dynamic wiki/memory fabric work from observed facts.

    The planner is intentionally pure and deterministic. It does not enqueue or
    execute anything; callers can feed `PlannedWork.to_queue_kwargs()` into the
    state-machine work queue when the tick layer is ready.
    """

    decisions = tuple(plan_wiki_growth_signal(GrowthSignal.from_value(signal)) for signal in signals)
    return GrowthPlan(decisions=decisions, plan_id=plan_id)


def plan_wiki_growth_signal(signal: GrowthSignal) -> GrowthDecision:
    explicit = explicit_outcome(signal)
    if explicit:
        return non_job_decision(signal, explicit, str(signal.payload.get("reason") or f"explicit {explicit} outcome"))

    if truthy(signal.payload.get("operator_touched")):
        return non_job_decision(signal, OUTCOME_DEFER, "operator-touched content blocks automatic mutation")

    kind = signal.kind.replace("_", ".")
    if kind in {"concept.discovered", "concept.appeared", "concept.candidate"}:
        return plan_concept_discovery(signal)
    if kind in {"page.discovered", "page.observed"}:
        return plan_page_discovery(signal)
    if kind in {"page.grew", "page.expanded"}:
        return plan_page_growth(signal)
    if kind in {"contradiction.appeared", "contradiction.detected"}:
        return contradiction_job(signal, reason="contradiction signal needs append-only follow-up")
    if kind in {"evidence.appeared", "evidence.discovered"}:
        return plan_evidence_growth(signal)
    return non_job_decision(signal, OUTCOME_NO_JOB, f"no wiki growth rule for {signal.kind}")


def plan_concept_discovery(signal: GrowthSignal) -> GrowthDecision:
    slug = slug_value(signal)
    if truthy(signal.payload.get("forget")) or str(signal.payload.get("status") or "") in {"fading", "stale"}:
        return non_job_decision(signal, OUTCOME_FORGET, "concept signal is fading or explicitly marked for forgetting")

    reinforcement = int_value(
        signal.payload.get("notability_score"),
        signal.payload.get("reinforcement_count"),
        signal.payload.get("evidence_count"),
    )
    if reinforcement < 2 and not truthy(signal.payload.get("existing_page")):
        return non_job_decision(signal, OUTCOME_DEFER, "concept has fewer than two reinforcement signals")

    reason = (
        "existing concept page should be expanded before creating a duplicate"
        if truthy(signal.payload.get("existing_page"))
        else "concept crossed the two-of-three notability threshold"
    )
    return job_decision(
        signal,
        job="memory.wiki.librarian",
        job_key=f"librarian:concept:{slug}",
        route_group="librarian_jobs",
        reason=reason,
        ownership={
            "kind": "concept_page",
            "slug": slug,
            "path": str(signal.payload.get("page_path") or ""),
            "existing_page": truthy(signal.payload.get("existing_page")),
        },
        event_name="wiki.concept.librarian_job_planned",
    )


def plan_page_discovery(signal: GrowthSignal) -> GrowthDecision:
    page_kind = str(signal.payload.get("page_kind") or signal.payload.get("kind") or "").strip()
    if page_kind in {"concept", "concept_page"}:
        return job_decision(
            signal,
            job="memory.wiki.librarian_sweep",
            job_key=f"librarian-sweep:page:{slug_value(signal)}",
            route_group="librarian_sweep_jobs",
            reason="new concept page should enter reinforcement/fading sweep jurisdiction",
            ownership={"kind": "concept_sweep", "slug": slug_value(signal), "path": str(signal.payload.get("path") or "")},
            event_name="wiki.concept.sweep_job_planned",
        )
    if truthy(signal.payload.get("has_hourly_conversations")) and not truthy(signal.payload.get("has_historian_output")):
        return job_decision(
            signal,
            job="memory.wiki.historian",
            job_key=f"historian:page:{slug_value(signal)}",
            route_group="historian_jobs",
            reason="page has hourly conversations without historian synthesis",
            ownership={"kind": "talk_folder_append", "path": str(signal.payload.get("talk_folder") or "")},
            event_name="wiki.historian_job_planned",
        )
    if int_value(signal.payload.get("pending_proposal_count"), signal.payload.get("pending_count")) > 0:
        return curator_job(signal, reason="new page has pending proposals that need curator adjudication")
    return non_job_decision(signal, OUTCOME_NO_JOB, "page discovery does not require a lane yet")


def plan_page_growth(signal: GrowthSignal) -> GrowthDecision:
    if int_value(signal.payload.get("pending_proposal_count"), signal.payload.get("pending_count")) > 0:
        return curator_job(signal, reason="page grew with pending proposals, so attach a curator lane")
    if int_value(signal.payload.get("filled_day_section_count")) >= 3 and not truthy(signal.payload.get("biography_filled")):
        return job_decision(
            signal,
            job="memory.wiki.biographer",
            job_key=f"biographer:{slug_value(signal)}",
            route_group="biographer_jobs",
            reason="page has enough filled day sections for biography continuity",
            ownership={"kind": "article_section", "section": "biography", "path": str(signal.payload.get("path") or "")},
            event_name="wiki.biographer_job_planned",
        )
    return non_job_decision(signal, OUTCOME_NO_CHANGE, "page growth did not cross a dynamic lane threshold")


def plan_evidence_growth(signal: GrowthSignal) -> GrowthDecision:
    evidence_kind = str(signal.payload.get("evidence_kind") or signal.payload.get("evidence_type") or "").strip()
    if evidence_kind in {"contradiction", "claim_drift", "conflict"}:
        return contradiction_job(signal, reason="new contradiction evidence needs follow-up")
    if evidence_kind in {"fresh_hourly", "hourly_conversation"}:
        return job_decision(
            signal,
            job="memory.wiki.historian",
            job_key=f"historian:evidence:{slug_value(signal)}",
            route_group="historian_jobs",
            reason="fresh hourly evidence can produce historian questions or synthesis",
            ownership={"kind": "talk_folder_append", "path": str(signal.payload.get("talk_folder") or "")},
            event_name="wiki.historian_job_planned",
        )
    return non_job_decision(signal, OUTCOME_NO_JOB, f"evidence kind {evidence_kind or 'unknown'} does not require a job")


def curator_job(signal: GrowthSignal, *, reason: str) -> GrowthDecision:
    page_kind = str(signal.payload.get("page_kind") or signal.payload.get("kind") or "").strip()
    if page_kind in {"your_context", "your-context"} or slug_value(signal) == "your-context":
        job = "memory.wiki.context_curator"
        route_group = "context_curator_jobs"
        event_name = "wiki.context_curator_job_planned"
    else:
        job = "memory.wiki.for_you_curator"
        route_group = "for_you_curator_jobs"
        event_name = "wiki.for_you_curator_job_planned"
    return job_decision(
        signal,
        job=job,
        job_key=f"{job.rsplit('.', 1)[-1]}:{slug_value(signal)}",
        route_group=route_group,
        reason=reason,
        ownership={
            "kind": "article_sections",
            "path": str(signal.payload.get("path") or ""),
            "sections": string_list(signal.payload.get("pending_sections", ())),
        },
        event_name=event_name,
    )


def contradiction_job(signal: GrowthSignal, *, reason: str) -> GrowthDecision:
    return job_decision(
        signal,
        job="memory.wiki.contradiction_flagger",
        job_key=f"contradiction:{slug_value(signal)}",
        route_group="contradiction_jobs",
        reason=reason,
        ownership={
            "kind": "talk_folder_append",
            "path": str(signal.payload.get("talk_folder") or signal.payload.get("target_page") or ""),
        },
        event_name="wiki.contradiction_job_planned",
    )


def job_decision(
    signal: GrowthSignal,
    *,
    job: str,
    job_key: str,
    route_group: str,
    reason: str,
    ownership: dict[str, Any],
    event_name: str,
) -> GrowthDecision:
    source_signal = signal.to_payload()
    expected_outputs = string_list(signal.payload.get("expected_outputs", ())) or list(DEFAULT_JOB_EXPECTED_OUTPUTS.get(job, ()))
    validators = string_list(signal.payload.get("validators", ())) or list(DEFAULT_JOB_VALIDATORS.get(job, ()))
    payload = {
        "job": job,
        "job_id": job,
        "job_ids": [job],
        "job_key": job_key,
        "route_group": route_group,
        "reason": reason,
        "ownership": ownership,
        "source_signal": source_signal,
        "source_signal_id": source_signal["signal_id"],
        "source_paths": string_list(signal.payload.get("source_paths", ())),
        "expected_outputs": expected_outputs,
        "validators": validators,
        "retry": {
            "max_attempts": int_value(signal.payload.get("max_attempts")) or 3,
            "retryable": not truthy(signal.payload.get("non_retryable")),
        },
    }
    payload = drop_empty(payload)
    work = PlannedWork(
        machine=WIKI_GROWTH_MACHINE,
        scope=DEFAULT_AGENT_SCOPE,
        key=job_key,
        event="wiki.growth.job_planned",
        source_state="eligible",
        target_state="queued",
        payload=payload,
    )
    event = GrowthEvent(event_name, {"job": job, "job_key": job_key, "route_group": route_group, "reason": reason})
    return GrowthDecision(signal=signal, outcome=OUTCOME_JOB, reason=reason, work=(work,), events=(event,), route_group=route_group)


def non_job_decision(signal: GrowthSignal, outcome: str, reason: str) -> GrowthDecision:
    if outcome not in TERMINAL_NON_JOB_OUTCOMES:
        raise ValueError(f"invalid non-job outcome {outcome!r}")
    event = GrowthEvent(
        "wiki.growth.non_job_outcome_recorded",
        {"outcome": outcome, "reason": reason, "signal_id": signal.signal_id, "key": signal.key},
    )
    return GrowthDecision(signal=signal, outcome=outcome, reason=reason, events=(event,))


def explicit_outcome(signal: GrowthSignal) -> str:
    outcome = str(signal.payload.get("outcome") or signal.payload.get("decision") or "").strip()
    if not outcome:
        return ""
    if outcome == "deferred":
        outcome = OUTCOME_DEFER
    if outcome == "skipped":
        outcome = OUTCOME_SKIP
    if outcome not in TERMINAL_NON_JOB_OUTCOMES:
        raise ValueError(f"unsupported explicit growth outcome {outcome!r}")
    return outcome


def slug_value(signal: GrowthSignal) -> str:
    return str(
        signal.payload.get("slug")
        or signal.payload.get("page_slug")
        or signal.payload.get("concept_slug")
        or signal.key
        or "default"
    ).strip()


def string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value if str(item))
    return (str(value),)


def string_list(value: Any) -> list[str]:
    return list(string_tuple(value))


def int_value(*values: Any) -> int:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def drop_empty(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value not in ("", None, [], {}, ())
    }


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
