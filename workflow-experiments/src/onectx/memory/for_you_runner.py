from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from onectx.config import MemorySystem
from onectx.memory.day_hourlies import (
    MonthHourlyBlocksResult,
    MonthHourlyRetriesResult,
    fixed_four_hour_blocks,
    run_month_hourly_block_scribes,
    run_month_hourly_retries,
)
from onectx.memory.jobs import CONCEPT_SCOUT_JOB_ID, DAILY_EDITOR_JOB_ID, PreparedMemoryJob, prepare_memory_job
from onectx.memory.ledger import Ledger, ledger_events_path
from onectx.memory.runner import HiredAgentBatchResult, execute_hired_agents
from onectx.memory.talk import read_talk_entries, render_talk_folder, validate_talk_entry
from onectx.memory.tick import MemoryTickResult, load_memory_cycle, run_memory_tick, validate_memory_cycle


class ForYouRunnerError(RuntimeError):
    """Raised when the For You state-machine slice cannot be executed."""


@dataclass(frozen=True)
class DayReviewResult:
    date: str
    talk_folder: Path
    render_before: dict[str, Any]
    prepared_jobs: tuple[PreparedMemoryJob, ...]
    batch: HiredAgentBatchResult
    render_after: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "talk_folder": str(self.talk_folder),
            "render_before": self.render_before,
            "prepared_count": len(self.prepared_jobs),
            "batch": self.batch.to_payload(),
            "render_after": self.render_after,
        }


@dataclass(frozen=True)
class ForYouRunPhase:
    phase_id: str
    status: str
    started_at: str
    completed_at: str
    duration_ms: int
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.phase_id,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class ForYouWikiLayerResult:
    concept_dir: Path
    tick: MemoryTickResult
    cycle_payload: dict[str, Any]
    validation: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        invariant = self.cycle_payload.get("runtime_invariant_report")
        invariant_summary = invariant.get("summary", {}) if isinstance(invariant, dict) else {}
        return {
            "concept_dir": str(self.concept_dir),
            "tick": self.tick.to_payload(),
            "runtime_invariant_summary": invariant_summary,
            "cycle": {
                "cycle_id": self.cycle_payload.get("cycle_id", self.tick.cycle_id),
                "status": self.cycle_payload.get("status", self.tick.status),
                "path": str(self.tick.path / "cycle.json"),
                "steps": self.cycle_payload.get("steps", []),
                "recovery": self.cycle_payload.get("recovery", {}),
                "route_preview": self.cycle_payload.get("route_preview", {}),
                "route_hire_execution": self.cycle_payload.get("route_hire_execution", {}),
                "route_table": self.cycle_payload.get("route_table", {}),
            },
            "validation": self.validation,
        }


@dataclass(frozen=True)
class ForYouMonthResult:
    month: str
    state_machine: dict[str, Any]
    workspace: Path
    blocks: MonthHourlyBlocksResult
    retries: MonthHourlyRetriesResult
    day_reviews: tuple[DayReviewResult, ...]
    wiki: ForYouWikiLayerResult | None
    phases: tuple[ForYouRunPhase, ...]
    plan_summary: dict[str, Any]
    started_at: str
    completed_at: str
    duration_ms: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "month": self.month,
            "state_machine": self.state_machine,
            "workspace": str(self.workspace),
            "blocks": self.blocks.to_payload(),
            "retries": self.retries.to_payload(),
            "day_reviews": [review.to_payload() for review in self.day_reviews],
            "wiki": self.wiki.to_payload() if self.wiki else None,
            "phases": [phase.to_payload() for phase in self.phases],
            "plan_summary": self.plan_summary,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class ForYouDayResult:
    date: str
    month: ForYouMonthResult

    def to_payload(self) -> dict[str, Any]:
        payload = self.month.to_payload()
        payload["date"] = self.date
        payload["scope"] = "day"
        return payload


def run_for_you_day(
    system: MemorySystem,
    *,
    date: str,
    audience: str = "private",
    workspace: Path | None = None,
    concept_dir: Path | None = None,
    run_harness: bool = False,
    model: str | None = None,
    max_concurrent: int | None = None,
    skip_existing: bool = True,
    run_day_layer: bool = True,
    split_large_blocks: bool = False,
    max_prompt_tokens: int | None = None,
    max_prompt_bytes: int | None = None,
    run_wiki_layer: bool = True,
    execute_wiki_render: bool = True,
    execute_route_hires: bool = True,
    route_hire_limit: int = 0,
    promote_route_outputs: bool = False,
    route_promotion_operator_approval: str = "",
    render_family_ids: tuple[str, ...] = (),
    freshness_check: str = "skip",
    require_fresh: bool = False,
    max_source_age_hours: int | None = None,
    retry_budget: int = 0,
    cycle_id: str = "",
    process_state_machine_queue: bool = True,
    sources: tuple[str, ...] = ("codex", "claude-code"),
) -> ForYouDayResult:
    """Execute the proved For You state-machine slice for one calendar day."""
    month = month_from_date(date)
    result = run_for_you_month(
        system,
        month=month,
        audience=audience,
        workspace=workspace or Path("/tmp") / f"onecontext-for-you-day-{date}-machine",
        concept_dir=concept_dir,
        run_harness=run_harness,
        model=model,
        max_concurrent=max_concurrent,
        limit_blocks=None,
        limit_days=None,
        skip_existing=skip_existing,
        run_day_layer=run_day_layer,
        split_large_blocks=split_large_blocks,
        max_prompt_tokens=max_prompt_tokens,
        max_prompt_bytes=max_prompt_bytes,
        run_wiki_layer=run_wiki_layer,
        execute_wiki_render=execute_wiki_render,
        execute_route_hires=execute_route_hires,
        route_hire_limit=route_hire_limit,
        promote_route_outputs=promote_route_outputs,
        route_promotion_operator_approval=route_promotion_operator_approval,
        render_family_ids=render_family_ids,
        freshness_check=freshness_check,
        require_fresh=require_fresh,
        max_source_age_hours=max_source_age_hours,
        retry_budget=retry_budget,
        cycle_id=cycle_id or f"for-you-day-{date}-wiki",
        process_state_machine_queue=process_state_machine_queue,
        sources=sources,
        only_dates=(date,),
    )
    return ForYouDayResult(date=date, month=result)


def run_for_you_month(
    system: MemorySystem,
    *,
    month: str,
    audience: str = "private",
    workspace: Path | None = None,
    concept_dir: Path | None = None,
    run_harness: bool = False,
    model: str | None = None,
    max_concurrent: int | None = None,
    limit_blocks: int | None = None,
    limit_days: int | None = None,
    skip_existing: bool = True,
    run_day_layer: bool = True,
    split_large_blocks: bool = False,
    max_prompt_tokens: int | None = None,
    max_prompt_bytes: int | None = None,
    run_wiki_layer: bool = True,
    execute_wiki_render: bool = True,
    execute_route_hires: bool = True,
    route_hire_limit: int = 0,
    promote_route_outputs: bool = False,
    route_promotion_operator_approval: str = "",
    render_family_ids: tuple[str, ...] = (),
    freshness_check: str = "skip",
    require_fresh: bool = False,
    max_source_age_hours: int | None = None,
    retry_budget: int = 0,
    cycle_id: str = "",
    process_state_machine_queue: bool = True,
    sources: tuple[str, ...] = ("codex", "claude-code"),
    only_dates: tuple[str, ...] = (),
) -> ForYouMonthResult:
    """Execute the proved For You state-machine slice for one month."""
    state_machine = require_state_machine(system, "for_you_day")
    workspace = workspace or Path("/tmp") / f"onecontext-for-you-month-{month}-machine"
    if promote_route_outputs:
        raise ForYouRunnerError(
            "run_for_you_month builds a generated wiki route view; run memory tick on a canonical "
            "wiki workspace for guarded source promotion"
        )
    started_at = now_iso()
    start = time.perf_counter()
    run_id = f"for-you-month-{month}-machine"
    ledger = Ledger(ledger_events_path(system.runtime_dir), storage_path=system.storage_dir)
    ledger.append(
        "state_machine.run_started",
        ledger_schema_version="0.1",
        plugin_id=system.active_plugin,
        run_id=run_id,
        state_machine={"id": state_machine.get("id"), "version": state_machine.get("version")},
        summary=f"Started For You month run for {month}.",
        outcome="started",
    )

    phases: list[ForYouRunPhase] = []
    block_kwargs: dict[str, Any] = {}
    if max_prompt_tokens is not None:
        block_kwargs["max_prompt_tokens"] = max_prompt_tokens
    if max_prompt_bytes is not None:
        block_kwargs["max_prompt_bytes"] = max_prompt_bytes
    phase_started = now_iso()
    phase_start = time.perf_counter()
    blocks = run_month_hourly_block_scribes(
        system,
        month=month,
        audience=audience,
        workspace=workspace,
        run_harness=run_harness,
        model=model,
        max_concurrent=max_concurrent,
        limit_blocks=limit_blocks,
        skip_existing=skip_existing,
        split_large_blocks=split_large_blocks,
        sources=sources,
        only_dates=only_dates,
        **block_kwargs,
    )
    phases.append(
        phase_result(
            "month_block_scribes",
            phase_started=phase_started,
            phase_start=phase_start,
            status="passed" if blocks.batch.ok else "failed",
            payload=blocks.to_payload(),
        )
    )
    ensure_for_you_article_shells(workspace, month=month, audience=audience, active_days=blocks.active_days)

    phase_started = now_iso()
    phase_start = time.perf_counter()
    retries = run_month_hourly_retries(
        system,
        month=month,
        audience=audience,
        workspace=workspace,
        run_harness=run_harness,
        model=model,
        max_concurrent=max_concurrent,
        skip_existing=skip_existing,
        sources=sources,
        only_dates=only_dates,
    )
    phases.append(
        phase_result(
            "hourly_retries",
            phase_started=phase_started,
            phase_start=phase_start,
            status="passed" if retries.batch.ok else "failed",
            payload=retries.to_payload(),
        )
    )

    if run_day_layer:
        phase_started = now_iso()
        phase_start = time.perf_counter()
        day_reviews = run_day_reviews(
            system,
            month=month,
            audience=audience,
            workspace=workspace,
            run_harness=run_harness,
            model=model,
            max_concurrent=max_concurrent,
            limit_days=limit_days,
            only_dates=only_dates,
            run_id=run_id,
        )
        phases.append(
            phase_result(
                "day_layer",
                phase_started=phase_started,
                phase_start=phase_start,
                status=day_layer_phase_status(blocks=blocks, day_reviews=day_reviews),
                payload={
                    "review_count": len(day_reviews),
                    "reviews": [review.to_payload() for review in day_reviews],
                },
            )
        )
    else:
        day_reviews = ()
        skipped_at = now_iso()
        phases.append(
            ForYouRunPhase(
                phase_id="day_layer",
                status="skipped",
                started_at=skipped_at,
                completed_at=skipped_at,
                duration_ms=0,
                payload={"reason": "run_day_layer=false"},
            )
        )

    wiki: ForYouWikiLayerResult | None = None
    if run_wiki_layer:
        phase_started = now_iso()
        phase_start = time.perf_counter()
        resolved_concept_dir = concept_dir or workspace / "concept"
        resolved_concept_dir.mkdir(parents=True, exist_ok=True)
        route_workspace = prepare_wiki_route_workspace(workspace, month=month, audience=audience, only_dates=only_dates)
        tick = run_memory_tick(
            system,
            wiki_only=True,
            workspace=route_workspace,
            concept_dir=resolved_concept_dir,
            audience=audience,
            sources=sources,
            max_source_age_hours=max_source_age_hours,
            require_fresh=require_fresh,
            freshness_check=freshness_check,
            execute_render=execute_wiki_render,
            execute_route_hires=execute_route_hires,
            route_hire_limit=route_hire_limit,
            route_hire_run_harness=run_harness,
            route_hire_max_concurrent=max_concurrent,
            route_hire_model=model,
            promote_route_outputs=promote_route_outputs,
            route_promotion_operator_approval=route_promotion_operator_approval,
            render_family_ids=render_family_ids,
            retry_budget=retry_budget,
            process_state_machine_queue=process_state_machine_queue,
            cycle_id=cycle_id or f"{run_id}-wiki",
        )
        cycle_payload = load_memory_cycle(system, tick.cycle_id)
        validation = validate_memory_cycle(system, tick.cycle_id).to_payload()
        wiki = ForYouWikiLayerResult(
            concept_dir=resolved_concept_dir,
            tick=tick,
            cycle_payload=cycle_payload,
            validation=validation,
        )
        phases.append(
            phase_result(
                "wiki_route_render_invariants",
                phase_started=phase_started,
                phase_start=phase_start,
                status=tick.status,
                payload=wiki.to_payload(),
            )
        )
    else:
        skipped_at = now_iso()
        phases.append(
            ForYouRunPhase(
                phase_id="wiki_route_render_invariants",
                status="skipped",
                started_at=skipped_at,
                completed_at=skipped_at,
                duration_ms=0,
                payload={"reason": "run_wiki_layer=false"},
            )
        )

    completed_at = now_iso()
    duration_ms = int((time.perf_counter() - start) * 1000)
    plan_summary = build_month_plan_summary(
        system,
        blocks=blocks,
        retries=retries,
        day_reviews=day_reviews,
        wiki=wiki,
        phases=tuple(phases),
        max_concurrent=max_concurrent,
        split_large_blocks=split_large_blocks,
        run_harness=run_harness,
    )
    result = ForYouMonthResult(
        month=month,
        state_machine={"id": state_machine.get("id"), "version": state_machine.get("version")},
        workspace=workspace,
        blocks=blocks,
        retries=retries,
        day_reviews=day_reviews,
        wiki=wiki,
        phases=tuple(phases),
        plan_summary=plan_summary,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
    )
    ledger.append(
        "state_machine.run_completed",
        ledger_schema_version="0.1",
        plugin_id=system.active_plugin,
        run_id=run_id,
        state_machine=result.state_machine,
        summary=f"Completed For You month run for {month}.",
        result_summary={
            "block_jobs": len(blocks.prepared_jobs),
            "retry_jobs": len(retries.prepared_jobs),
            "day_reviews": len(day_reviews),
            "wiki_status": wiki.tick.status if wiki else "skipped",
            "planned_route_jobs": wiki.tick.planned_hire_count if wiki else 0,
            "validation_failures": plan_summary.get("validation_failures", {}),
            "duration_ms": duration_ms,
        },
        outcome="done" if run_ok(result) else "failure",
    )
    return result


def phase_result(
    phase_id: str,
    *,
    phase_started: str,
    phase_start: float,
    status: str,
    payload: dict[str, Any],
) -> ForYouRunPhase:
    return ForYouRunPhase(
        phase_id=phase_id,
        status=status,
        started_at=phase_started,
        completed_at=now_iso(),
        duration_ms=int((time.perf_counter() - phase_start) * 1000),
        payload=payload,
    )


def day_layer_phase_status(
    *,
    blocks: MonthHourlyBlocksResult,
    day_reviews: tuple[DayReviewResult, ...],
) -> str:
    if day_reviews:
        return "passed" if all(review.batch.ok for review in day_reviews) else "failed"
    if blocks.active_hour_count and blocks.prepared_jobs:
        return "waiting_for_hourly_outputs"
    if blocks.active_hour_count:
        return "skipped_no_reviewable_talk_entries"
    return "skipped_empty_input"


def ensure_for_you_article_shells(
    workspace: Path,
    *,
    month: str,
    audience: str,
    active_days: tuple[Any, ...],
) -> tuple[Path, ...]:
    """Create minimal source pages so route rows point at concrete artifacts."""
    written: list[Path] = []
    workspace.mkdir(parents=True, exist_ok=True)
    for active_day in active_days:
        date = str(active_day.date)
        if not date.startswith(month):
            continue
        text = (
            "---\n"
            f"title: For You {date}\n"
            f"access: {audience}\n"
            "status: draft\n"
            "---\n\n"
            f"# For You {date}\n\n"
            '<!-- section:"biography" -->\n'
            "## Biography\n"
            "<!-- empty: biography pending -->\n\n"
            f'<!-- section:"{date}" -->\n'
            f"## {date}\n"
            "<!-- empty: day-section pending curator -->\n"
        )
        for path in (workspace / f"for-you-{date}.md", workspace / f"{date}.md"):
            if path.exists():
                continue
            path.write_text(text, encoding="utf-8")
            written.append(path)
    your_context = workspace / "your-context.md"
    if not your_context.exists():
        your_context.write_text(
            "---\n"
            "title: Your Context\n"
            f"access: {audience}\n"
            "status: draft\n"
            "---\n\n"
            "# Your Context\n\n"
            '<!-- section:"working-style" -->\n'
            "## Working Style\n"
            "<!-- empty: agent-populated -->\n",
            encoding="utf-8",
        )
        written.append(your_context)
    for name, title in (
        ("index.md", "For You Index"),
        ("topics.md", "Topics"),
        ("projects.md", "Projects"),
        ("open-questions.md", "Open Questions"),
        ("this-week.md", "This Week"),
    ):
        path = workspace / name
        if path.exists():
            continue
        path.write_text(
            "---\n"
            f"title: {title}\n"
            "status: generated-placeholder\n"
            "---\n\n"
            f"# {title}\n\n"
            "<!-- generated placeholder for route-planning dry runs -->\n",
            encoding="utf-8",
        )
        written.append(path)
    return tuple(written)


def prepare_wiki_route_workspace(
    workspace: Path,
    *,
    month: str,
    audience: str,
    only_dates: tuple[str, ...] = (),
) -> Path:
    """Build a route-planner view without exposing runner-specific talk names."""
    date_filter = set(only_dates)
    route_workspace = workspace / ".wiki-route-workspace"
    if route_workspace.exists():
        shutil.rmtree(route_workspace)
    route_workspace.mkdir(parents=True, exist_ok=True)
    for source in sorted(workspace.glob("*.md")):
        if source.is_file():
            source_date = for_you_source_date(source)
            if date_filter and source_date and source_date not in date_filter:
                continue
            shutil.copy2(source, route_workspace / source.name)
    ensure_wiki_route_aliases(
        workspace,
        route_workspace=route_workspace,
        month=month,
        audience=audience,
        only_dates=only_dates,
    )
    return route_workspace


def ensure_wiki_route_aliases(
    workspace: Path,
    *,
    route_workspace: Path,
    month: str,
    audience: str,
    only_dates: tuple[str, ...] = (),
) -> tuple[Path, ...]:
    """Mirror runner talk folders into the ISO-date names the route planner expects."""
    aliases: list[Path] = []
    date_filter = set(only_dates)
    for source in sorted(workspace.glob(f"for-you-{month}-*.{audience}.talk")):
        if not source.is_dir():
            continue
        date = date_from_talk_folder(source)
        if not date:
            continue
        if date_filter and date not in date_filter:
            continue
        alias = route_workspace / f"{date}.{audience}.talk"
        alias.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            target = alias / child.name
            if child.is_dir():
                if target.exists():
                    continue
                shutil.copytree(child, target)
            elif child.is_file():
                shutil.copy2(child, target)
        aliases.append(alias)
    for source in sorted(workspace.glob(f"{month}-*.{audience}.talk")):
        if not source.is_dir():
            continue
        date = source.name.split(".", 1)[0]
        if date_filter and date not in date_filter:
            continue
        alias = route_workspace / source.name
        if alias.exists():
            continue
        shutil.copytree(source, alias)
        aliases.append(alias)
    return tuple(aliases)


def for_you_source_date(path: Path) -> str:
    stem = path.stem
    if stem.startswith("for-you-"):
        candidate = stem[len("for-you-") :]
    else:
        candidate = stem
    if len(candidate) == 10 and candidate[4] == "-" and candidate[7] == "-":
        return candidate
    return ""


def build_month_plan_summary(
    system: MemorySystem,
    *,
    blocks: MonthHourlyBlocksResult,
    retries: MonthHourlyRetriesResult,
    day_reviews: tuple[DayReviewResult, ...],
    wiki: ForYouWikiLayerResult | None,
    phases: tuple[ForYouRunPhase, ...],
    max_concurrent: int | None,
    split_large_blocks: bool,
    run_harness: bool,
) -> dict[str, Any]:
    effective_max = int(max_concurrent or system.runtime_policy["max_concurrent_agents"])
    fixed_blocks = fixed_four_hour_blocks(blocks.active_days)
    route_jobs = wiki.tick.planned_hire_count if wiki else 0
    route_hire_execution = wiki.cycle_payload.get("route_hire_execution", {}) if wiki else {}
    runtime_invariants = (
        wiki.cycle_payload.get("runtime_invariant_report", {}).get("summary", {})
        if wiki and isinstance(wiki.cycle_payload.get("runtime_invariant_report"), dict)
        else {}
    )
    validation_failures = {
        "block_scribes": int(blocks.batch.to_payload().get("validation_failure_count") or 0),
        "hourly_retries": int(retries.batch.to_payload().get("validation_failure_count") or 0),
        "day_layer": sum(int(review.batch.to_payload().get("validation_failure_count") or 0) for review in day_reviews),
        "wiki_route_hires": int(route_hire_execution.get("validation_failure_count") or 0),
        "runtime_invariants": int(runtime_invariants.get("silent_noops") or 0),
    }
    phase_durations = {phase.phase_id: phase.duration_ms for phase in phases}
    phase_statuses = {phase.phase_id: phase.status for phase in phases}
    block_waves = waves(len(fixed_blocks), effective_max)
    route_waves = waves(route_jobs, effective_max)
    return {
        "mode": "live" if run_harness else "dry_run",
        "target": {
            "month_minutes": 60,
            "fixed_four_hour_block_hires": len(fixed_blocks),
            "max_concurrent_agents": effective_max,
            "block_waves_at_max_concurrent": block_waves,
            "route_waves_at_max_concurrent": route_waves,
            "target_minutes_per_block_wave": round(60 / block_waves, 2) if block_waves else None,
            "automatic_split_default": False,
            "split_large_blocks_enabled": split_large_blocks,
            "split_large_blocks_mode": "explicit_escalation" if split_large_blocks else "disabled",
        },
        "work": {
            "active_days": len(blocks.active_days),
            "active_hours": blocks.active_hour_count,
            "covered_hours": blocks.prepared_hour_count + len(blocks.skipped_existing),
            "raw_events": blocks.event_count,
            "block_hires_prepared": len(blocks.prepared_jobs),
            "retry_hires_prepared": len(retries.prepared_jobs),
            "day_review_hires_prepared": sum(len(review.prepared_jobs) for review in day_reviews),
            "route_jobs_planned": route_jobs,
            "route_hires_completed": wiki.tick.route_hire_count if wiki else 0,
            "wiki_renders": wiki.tick.render_count if wiki else 0,
            "wiki_manifests": wiki.tick.manifest_count if wiki else 0,
            "wiki_routes": wiki.tick.route_count if wiki else 0,
        },
        "waves": {
            "block_hires": {
                "jobs": len(fixed_blocks),
                "max_concurrent": effective_max,
                "waves": block_waves,
            },
            "prepared_hires": {
                "jobs": len(blocks.prepared_jobs),
                "max_concurrent": effective_max,
                "waves": waves(len(blocks.prepared_jobs), effective_max),
            },
            "route_hires": {
                "jobs": route_jobs,
                "max_concurrent": effective_max,
                "waves": route_waves,
            },
        },
        "observed_durations_ms": {
            "phases": phase_durations,
            "block_batch": blocks.batch.duration_ms,
            "retry_batch": retries.batch.duration_ms,
            "day_review_batches": [review.batch.duration_ms for review in day_reviews],
        },
        "validation_failures": validation_failures,
        "status": {
            "blocks_ok": blocks.batch.ok,
            "retries_ok": retries.batch.ok,
            "day_layer_ok": all(review.batch.ok for review in day_reviews),
            "wiki_status": wiki.tick.status if wiki else "skipped",
            "memory_cycle_validation_passed": bool(wiki.validation.get("passed")) if wiki else None,
            "runtime_invariants_passed": runtime_invariants.get("passed") if runtime_invariants else None,
            "phase_statuses": phase_statuses,
            "day_layer_status": phase_statuses.get("day_layer", "missing"),
            "day_layer_materialized": bool(day_reviews),
        },
    }


def waves(job_count: int, max_concurrent: int) -> int:
    if job_count <= 0:
        return 0
    return (job_count + max(1, max_concurrent) - 1) // max(1, max_concurrent)


def run_ok(result: ForYouMonthResult) -> bool:
    wiki_ok = result.wiki is None or result.wiki.tick.status == "completed"
    return (
        result.blocks.batch.ok
        and result.retries.batch.ok
        and all(review.batch.ok for review in result.day_reviews)
        and wiki_ok
    )


def run_day_reviews(
    system: MemorySystem,
    *,
    month: str,
    audience: str,
    workspace: Path,
    run_harness: bool,
    model: str | None,
    max_concurrent: int | None,
    limit_days: int | None,
    only_dates: tuple[str, ...] = (),
    run_id: str,
) -> tuple[DayReviewResult, ...]:
    reviews: list[DayReviewResult] = []
    date_filter = set(only_dates)
    for talk_folder in month_talk_folders(workspace, month=month, audience=audience):
        date = date_from_talk_folder(talk_folder)
        if not date:
            continue
        if date_filter and date not in date_filter:
            continue
        entries = read_talk_entries(talk_folder)
        if not any(entry.kind == "conversation" for entry in entries):
            continue
        if limit_days is not None and len(reviews) >= limit_days:
            continue
        render_before = render_talk_folder(talk_folder)
        editor_output = talk_folder / f"{date}T23-59Z.proposal.editor-day-{date}.md"
        concept_output = talk_folder / f"{date}T23-59Z.proposal.concept-candidates.md"
        prepared = (
            prepare_memory_job(
                system,
                job_id=DAILY_EDITOR_JOB_ID,
                params={
                    "date": date,
                    "audience": audience,
                    "talk_folder": str(talk_folder),
                    "output_path": str(editor_output),
                },
                workspace=workspace,
                run_harness=run_harness,
                model=model,
                run_id=run_id,
                completed_event="memory.daily_editor.state_machine_completed",
                validator=lambda path: validate_talk_entry(path, expected_kind="proposal"),
            ),
            prepare_memory_job(
                system,
                job_id=CONCEPT_SCOUT_JOB_ID,
                params={
                    "date": date,
                    "audience": audience,
                    "talk_folder": str(talk_folder),
                    "output_path": str(concept_output),
                },
                workspace=workspace,
                run_harness=run_harness,
                model=model,
                run_id=run_id,
                completed_event="memory.concept_scout.state_machine_completed",
                validator=lambda path: validate_talk_entry(path, expected_kind=("proposal", "question", "concern")),
            ),
        )
        batch = execute_hired_agents(
            system,
            [item.execution_spec for item in prepared],
            max_concurrent=max_concurrent,
            run_id=run_id,
        )
        render_after = render_talk_folder(talk_folder)
        reviews.append(
            DayReviewResult(
                date=date,
                talk_folder=talk_folder,
                render_before=render_before,
                prepared_jobs=prepared,
                batch=batch,
                render_after=render_after,
            )
        )
    return tuple(reviews)


def month_talk_folders(workspace: Path, *, month: str, audience: str) -> tuple[Path, ...]:
    return tuple(
        path
        for path in sorted(workspace.glob(f"for-you-{month}-*.{audience}.talk"))
        if path.is_dir()
    )


def date_from_talk_folder(talk_folder: Path) -> str:
    prefix = "for-you-"
    suffix = "."
    name = talk_folder.name
    if not name.startswith(prefix):
        return ""
    return name[len(prefix) :].split(suffix, 1)[0]


def require_state_machine(system: MemorySystem, machine_id: str) -> dict[str, Any]:
    machine = system.state_machines.get(machine_id)
    if not machine:
        raise ForYouRunnerError(f"missing state machine {machine_id!r}")
    return machine


def month_from_date(date: str) -> str:
    from datetime import datetime

    try:
        parsed = datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise ForYouRunnerError(f"date must be YYYY-MM-DD, got {date!r}") from exc
    return parsed.strftime("%Y-%m")


def now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
