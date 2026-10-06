from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from onectx.config import load_system
from onectx.memory.control_loop import run_state_machine_queue_tick
from onectx.memory.growth_runner import persist_growth_plan_to_queue
from onectx.memory.ledger import Ledger, ledger_events_path
from onectx.state_machines.growth import (
    OUTCOME_DEFER,
    OUTCOME_FORGET,
    OUTCOME_NO_CHANGE,
    plan_wiki_memory_growth,
)
from onectx.state_machines.queue import STATUS_DONE, StateMachineWorkQueue
from onectx.state_machines.runtime import load_scope_state
from onectx.storage import LakeStore


def isolated_system(tmp_path: Path):
    return replace(
        load_system(Path.cwd()),
        runtime_dir=tmp_path / "runtime",
        storage_dir=tmp_path / "lakestore",
    )


def test_growth_runner_enqueues_jobs_with_route_and_reconciliation_metadata(tmp_path: Path) -> None:
    system = isolated_system(tmp_path)
    plan = plan_wiki_memory_growth(
        [
            {
                "kind": "concept.discovered",
                "slug": "bt10",
                "existing_page": True,
                "page_path": "/wiki/concepts/bt10.md",
                "reinforcement_count": 1,
            }
        ],
        plan_id="growth-plan-jobs",
    )

    result = persist_growth_plan_to_queue(system, plan, now="2026-04-29T12:00:00Z")

    assert result.enqueued_count == 1
    assert result.non_job_count == 0
    assert result.route_plan["librarian_jobs"][0]["job"] == "memory.wiki.librarian"
    queue = StateMachineWorkQueue.load(result.queue_path)
    item = queue.list()[0]
    payload = item.payload
    assert item.queue_id == plan.work[0].work_id
    assert item.created_at == "2026-04-29T12:00:00Z"
    assert payload["kind"] == "wiki_growth_role_job"
    assert payload["queue_adapter"] == "growth_role_route"
    assert payload["job_id"] == "memory.wiki.librarian"
    assert payload["job_ids"] == ["memory.wiki.librarian"]
    assert payload["route_group"] == "librarian_jobs"
    assert payload["source_signal_id"] == plan.decisions[0].signal.signal_id
    assert payload["ownership"]["slug"] == "bt10"
    assert payload["expected_outputs"] == ["concept_page.created_expanded_or_deferred"]
    assert payload["work_key"] == f"run:{payload['run_id']}"
    assert payload["classification_hooks"]["work_key"] == payload["work_key"]
    assert payload["route_row"]["route_id"] == payload["route_id"]
    assert payload["route_row"]["outcome"] == "hire"
    assert payload["route_row"]["source_packet"]["loaded_at_birth"] is True

    second = persist_growth_plan_to_queue(system, plan, now="2026-04-29T12:01:00Z")
    reloaded = StateMachineWorkQueue.load(second.queue_path)
    assert len(reloaded.list()) == 1
    assert reloaded.list()[0].queue_id == item.queue_id
    assert reloaded.list()[0].payload["run_id"] == payload["run_id"]
    assert reloaded.list()[0].payload["route_id"] == payload["route_id"]


def test_growth_runner_records_non_job_outcomes_as_auditable_terminal_events(tmp_path: Path) -> None:
    system = isolated_system(tmp_path)
    plan = plan_wiki_memory_growth(
        [
            {
                "kind": "concept.discovered",
                "slug": "half-seen-tool",
                "reinforcement_count": 1,
            },
            {
                "kind": "page.discovered",
                "page_slug": "archive-me",
                "outcome": "forget",
                "reason": "fading concept",
            },
            {
                "kind": "page.grew",
                "page_slug": "already-current",
                "filled_day_section_count": 1,
            },
        ],
        plan_id="growth-plan-non-jobs",
    )

    result = persist_growth_plan_to_queue(system, plan, now="2026-04-29T12:00:00Z")

    assert result.enqueued_count == 0
    assert result.non_job_count == 3
    assert {event["event"] for event in result.events} == {"wiki.growth.non_job_outcome_recorded"}
    assert {event["outcome"] for event in result.events} == {OUTCOME_DEFER, OUTCOME_FORGET, OUTCOME_NO_CHANGE}
    assert all(event["terminal"] is True for event in result.events)
    assert StateMachineWorkQueue.load(result.queue_path).list() == []

    ledger_rows = Ledger(ledger_events_path(system.runtime_dir), storage_path=system.storage_dir).read()
    assert {row["outcome"] for row in ledger_rows} == {OUTCOME_DEFER, OUTCOME_FORGET, OUTCOME_NO_CHANGE}
    evidence_payloads = [
        json.loads(row["payload_json"])
        for row in LakeStore(system.storage_dir).rows("evidence", limit=0)
        if row["check_id"] == "wiki_growth.non_job_outcome_recorded"
    ]
    assert {payload["outcome"] for payload in evidence_payloads} == {OUTCOME_DEFER, OUTCOME_FORGET, OUTCOME_NO_CHANGE}
    assert all(payload["terminal"] is True for payload in evidence_payloads)


def test_growth_queue_item_reconciles_completed_external_role_work(tmp_path: Path) -> None:
    system = isolated_system(tmp_path)
    plan = plan_wiki_memory_growth(
        [
            {
                "kind": "page.grew",
                "page_slug": "2026-04-20",
                "page_kind": "for_you",
                "path": "/wiki/2026-04-20.md",
                "pending_proposal_count": 1,
            }
        ],
        plan_id="growth-plan-reconcile",
    )
    persist_growth_plan_to_queue(system, plan, now="2026-04-29T12:00:00Z")
    queue = StateMachineWorkQueue.load(system.runtime_dir / "state-machines" / "queue")
    item = queue.list()[0]
    ledger = Ledger(ledger_events_path(system.runtime_dir), storage_path=system.storage_dir)
    ledger.append(
        "harness.launch_started",
        run_id=item.payload["run_id"],
        job_ids=item.payload["job_ids"],
        outcome="started",
        started_at="2026-04-29T12:01:00Z",
    )
    ledger.append(
        "artifact.validated",
        run_id=item.payload["run_id"],
        job_ids=item.payload["job_ids"],
        outcome="done",
        evidence={"ok": True},
    )
    ledger.append(
        "hired_agent.execution_completed",
        run_id=item.payload["run_id"],
        job_ids=item.payload["job_ids"],
        outcome="done",
    )

    result = run_state_machine_queue_tick(system, limit=1, now="2026-04-29T12:05:00Z")

    assert result.reconciled_count == 1
    assert result.completed_count == 1
    updated = StateMachineWorkQueue.load(system.runtime_dir / "state-machines" / "queue").get(item.queue_id)
    assert updated.status == STATUS_DONE
    assert updated.payload["classification"]["work_key"] == item.payload["work_key"]
    assert updated.payload["external_reconciliation"]["adapter"] == "growth_role_route"
    scope_state = load_scope_state(
        system,
        machine_id=item.machine,
        scope=item.scope,
        key=item.key,
    )
    assert scope_state["state"] == "done"
    events = {row["event"] for row in LakeStore(system.storage_dir).rows("events", limit=0)}
    assert {"state_machine.work.completed", "wiki.growth.role_job.completed"} <= events
