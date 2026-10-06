from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from onectx.config import load_system
from onectx.memory.transition_executor import execute_memory_cycle_transitions
from onectx.state_machines.runtime import load_scope_state


def temp_system(tmp_path: Path):
    system = load_system(Path.cwd())
    return replace(system, runtime_dir=tmp_path / "runtime")


def test_memory_cycle_executor_records_reader_surface_chain(tmp_path: Path) -> None:
    system = temp_system(tmp_path)

    trace = execute_memory_cycle_transitions(
        system,
        cycle_id="cycle-render-complete",
        status="completed",
        dry_run=False,
        execute_render=True,
        render_count=1,
        manifest_count=1,
        route_count=1,
    )
    payload = trace.to_tick_payload(cycle_id="cycle-render-complete")

    assert payload["initial_state"] == "routing_wiki"
    assert payload["terminal_state"] == "complete"
    assert [item["event"] for item in payload["transitions"]] == [
        "wiki.agent_layer.closed",
        "memory.reader_surface.ready",
    ]
    assert payload["transitions"][0]["produced_evidence"] == ["reader_surface.ready"]
    assert payload["transitions"][0]["missing_expected_evidence"] == []
    assert payload["scope_state"]["state"] == "complete"


def test_memory_cycle_executor_persists_recovery_state_after_failed_reader_transition(tmp_path: Path) -> None:
    system = temp_system(tmp_path)

    trace = execute_memory_cycle_transitions(
        system,
        cycle_id="cycle-render-failed",
        status="failed",
        dry_run=True,
        execute_render=True,
        render_count=0,
        manifest_count=0,
        route_count=0,
    )
    payload = trace.to_tick_payload(cycle_id="cycle-render-failed")

    assert payload["terminal_state"] == "failed"
    assert payload["transition_count"] == 1
    assert payload["transitions"][0]["event"] == "wiki.agent_layer.closed"
    assert payload["transitions"][0]["status"] == "failed"
    assert payload["transitions"][0]["target_state"] == "building_reader_surface"
    assert payload["transitions"][0]["missing_expected_evidence"] == ["reader_surface.ready"]

    state = load_scope_state(
        system,
        machine_id="memory_system_fabric",
        scope="cycle",
        key="cycle-render-failed",
    )
    assert state["state"] == "failed"
    assert state["transitions"][0]["target_state"] == "building_reader_surface"


def test_memory_cycle_executor_records_dry_run_without_transition_requests(tmp_path: Path) -> None:
    system = temp_system(tmp_path)

    trace = execute_memory_cycle_transitions(
        system,
        cycle_id="cycle-dry-run",
        status="completed",
        dry_run=True,
        execute_render=False,
        render_count=0,
        manifest_count=0,
        route_count=0,
    )
    payload = trace.to_tick_payload(cycle_id="cycle-dry-run")

    assert payload["terminal_state"] == "routing_wiki"
    assert payload["transition_count"] == 0
    assert payload["note"] == "dry-run tick stopped before the reader-surface transition executed"
    assert Path(payload["scope_state"]["path"]).is_file()
