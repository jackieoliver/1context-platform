from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from onectx.config import load_system
from onectx.memory.for_you_runner import run_for_you_day, run_for_you_month
from onectx.storage import LakeStore


def test_for_you_month_dry_run_orchestrates_to_invariant_report(tmp_path: Path) -> None:
    system = isolated_system(tmp_path)
    workspace = tmp_path / "workspace"
    concept_dir = tmp_path / "concepts"
    concept_dir.mkdir()
    seed_session_events(system, hours=("09", "10"))
    write_valid_hourly(workspace, date="2026-04-20", hour="09")
    write_pending_editor_proposal(workspace, date="2026-04-20")

    result = run_for_you_month(
        system,
        month="2026-04",
        workspace=workspace,
        concept_dir=concept_dir,
        run_harness=False,
        max_concurrent=8,
        limit_blocks=1,
        execute_wiki_render=False,
        freshness_check="skip",
        process_state_machine_queue=False,
        cycle_id="test-for-you-month-e2e",
    )

    payload = result.to_payload()
    assert [phase["id"] for phase in payload["phases"]] == [
        "month_block_scribes",
        "hourly_retries",
        "day_layer",
        "wiki_route_render_invariants",
    ]
    assert payload["plan_summary"]["mode"] == "dry_run"
    assert payload["plan_summary"]["target"]["fixed_four_hour_block_hires"] == 1
    assert payload["plan_summary"]["target"]["block_waves_at_max_concurrent"] == 1
    assert payload["plan_summary"]["work"]["active_hours"] == 2
    assert payload["plan_summary"]["work"]["block_hires_prepared"] == 1
    assert payload["plan_summary"]["work"]["day_review_hires_prepared"] == 2
    assert payload["plan_summary"]["work"]["route_jobs_planned"] >= 1
    assert payload["wiki"]["tick"]["status"] == "completed"
    assert payload["wiki"]["tick"]["dry_run"] is True
    assert payload["wiki"]["runtime_invariant_summary"]["passed"] is True
    assert payload["wiki"]["validation"]["passed"] is True
    assert payload["wiki"]["cycle"]["route_hire_execution"]["spec_count"] >= 1
    assert Path(payload["wiki"]["tick"]["files"]["cycle"]).is_file()


def test_for_you_month_skip_existing_resumes_without_repreparing_blocks(tmp_path: Path) -> None:
    system = isolated_system(tmp_path)
    workspace = tmp_path / "workspace"
    concept_dir = tmp_path / "concepts"
    concept_dir.mkdir()
    seed_session_events(system, hours=("09", "10"))
    write_valid_hourly(workspace, date="2026-04-20", hour="09")
    write_valid_hourly(workspace, date="2026-04-20", hour="10")

    result = run_for_you_month(
        system,
        month="2026-04",
        workspace=workspace,
        concept_dir=concept_dir,
        run_harness=False,
        max_concurrent=8,
        limit_blocks=1,
        execute_wiki_render=False,
        execute_route_hires=False,
        freshness_check="skip",
        process_state_machine_queue=False,
        cycle_id="test-for-you-month-resume",
    )

    payload = result.to_payload()
    assert payload["blocks"]["prepared_count"] == 0
    assert payload["blocks"]["skipped_existing_count"] == 2
    assert payload["plan_summary"]["work"]["covered_hours"] == 2
    assert payload["plan_summary"]["work"]["block_hires_prepared"] == 0
    assert payload["wiki"]["tick"]["status"] == "completed"


def test_for_you_day_scopes_month_fabric_to_one_date(tmp_path: Path) -> None:
    system = isolated_system(tmp_path)
    workspace = tmp_path / "workspace"
    concept_dir = tmp_path / "concepts"
    concept_dir.mkdir()
    seed_session_events(system, date="2026-04-20", hours=("09",))
    seed_session_events(system, date="2026-04-21", hours=("13",))
    write_valid_hourly(workspace, date="2026-04-20", hour="09")
    write_valid_hourly(workspace, date="2026-04-21", hour="13")

    result = run_for_you_day(
        system,
        date="2026-04-21",
        workspace=workspace,
        concept_dir=concept_dir,
        run_harness=False,
        max_concurrent=8,
        execute_wiki_render=False,
        freshness_check="skip",
        process_state_machine_queue=False,
        cycle_id="test-for-you-day-e2e",
    )

    payload = result.to_payload()
    assert payload["scope"] == "day"
    assert payload["date"] == "2026-04-21"
    assert payload["blocks"]["active_day_count"] == 1
    assert payload["blocks"]["active_days"][0]["date"] == "2026-04-21"
    assert payload["blocks"]["active_blocks"] == []
    assert payload["blocks"]["skipped_existing_count"] == 1
    assert payload["plan_summary"]["work"]["active_hours"] == 1
    assert payload["plan_summary"]["target"]["fixed_four_hour_block_hires"] == 1
    assert payload["plan_summary"]["status"]["day_layer_status"] == "passed"
    assert payload["plan_summary"]["status"]["day_layer_materialized"] is True
    assert payload["wiki"]["tick"]["status"] == "completed"
    assert payload["wiki"]["validation"]["passed"] is True
    route_workspace = workspace / ".wiki-route-workspace"
    assert not (route_workspace / "2026-04-20.private.talk").exists()
    assert (route_workspace / "2026-04-21.private.talk").is_dir()


def test_for_you_day_dry_run_marks_day_layer_waiting_without_hourly_outputs(tmp_path: Path) -> None:
    system = isolated_system(tmp_path)
    workspace = tmp_path / "workspace"
    seed_session_events(system, date="2026-04-22", hours=("09",))

    result = run_for_you_day(
        system,
        date="2026-04-22",
        workspace=workspace,
        run_harness=False,
        max_concurrent=8,
        run_wiki_layer=False,
        freshness_check="skip",
        process_state_machine_queue=False,
    )

    payload = result.to_payload()
    assert payload["blocks"]["prepared_count"] == 1
    assert payload["day_reviews"] == []
    assert payload["phases"][2]["id"] == "day_layer"
    assert payload["phases"][2]["status"] == "waiting_for_hourly_outputs"
    assert payload["plan_summary"]["status"]["day_layer_status"] == "waiting_for_hourly_outputs"
    assert payload["plan_summary"]["status"]["day_layer_materialized"] is False


def isolated_system(tmp_path: Path):
    system = load_system(Path.cwd())
    return replace(
        system,
        runtime_dir=tmp_path / "runtime",
        storage_dir=tmp_path / "lakestore",
    )


def seed_session_events(system, *, hours: tuple[str, ...], date: str = "2026-04-20") -> None:
    store = LakeStore(system.storage_dir)
    store.ensure()
    for hour in hours:
        store.append_event(
            "session.message",
            event_id=f"test-event-{date}-{hour}-user",
            ts=f"{date}T{hour}:00:00Z",
            source="codex",
            kind="user",
            actor="example",
            session_id="codex-test",
            cwd="/tmp/onecontext-test",
            text=f"User asked for worker handoff in hour {hour}.",
            payload={"hour": hour},
        )
        store.append_event(
            "session.message",
            event_id=f"test-event-{date}-{hour}-assistant",
            ts=f"{date}T{hour}:05:00Z",
            source="codex",
            kind="assistant",
            actor="codex",
            session_id="codex-test",
            cwd="/tmp/onecontext-test",
            text=f"Assistant captured session context for hour {hour}.",
            payload={"hour": hour},
        )


def write_valid_hourly(workspace: Path, *, date: str, hour: str) -> Path:
    talk = workspace / f"for-you-{date}.private.talk"
    talk.mkdir(parents=True, exist_ok=True)
    path = talk / f"{date}T{hour}-00Z.conversation.md"
    path.write_text(
        f"""---
kind: conversation
ts: {date}T{hour}:00:00Z
author: test-scribe
---

## Operational Thread

The user asked a worker to preserve session context and continue the handoff.

## What I'd flag

No unresolved risk beyond this being a compact test fixture.
""",
        encoding="utf-8",
    )
    return path


def write_pending_editor_proposal(workspace: Path, *, date: str) -> Path:
    talk = workspace / f"for-you-{date}.private.talk"
    talk.mkdir(parents=True, exist_ok=True)
    path = talk / f"{date}T23-59Z.proposal.editor-day-{date}.md"
    path.write_text(
        f"""---
kind: proposal
ts: {date}T23:59:00Z
author: test-editor
target-section: {date}
---

## Proposal

Promote the compact fixture day into the For You article.
""",
        encoding="utf-8",
    )
    return path
