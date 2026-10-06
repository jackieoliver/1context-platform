from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from onectx.config import load_system
from onectx.state_machines.runtime import execute_transition_chain, load_scope_state


def sample_machine_ir() -> dict:
    return {
        "id": "memory_system_fabric",
        "transitions": [
            {
                "source": {"scope": "cycle", "state": "routing_wiki"},
                "event": {"kind": "event", "name": "wiki.agent_layer.closed"},
                "target": {"scope": "cycle", "state": "building_reader_surface"},
                "actions": [
                    {"kind": "step", "name": "run_wiki_reader_loop"},
                    {"kind": "step", "name": "render_wiki_engine_families"},
                    {"kind": "expect", "evidence": "reader_surface.ready"},
                    {"kind": "emit", "event": "memory.reader_surface.ready"},
                ],
            },
            {
                "source": {"scope": "cycle", "state": "building_reader_surface"},
                "event": {"kind": "event", "name": "memory.reader_surface.ready"},
                "target": {"scope": "cycle", "state": "complete"},
                "actions": [
                    {"kind": "step", "name": "append_cycle_summary_event"},
                    {"kind": "expect", "evidence": "cycle_summary.recorded"},
                    {"kind": "emit", "event": "memory.cycle.complete"},
                ],
            },
        ],
    }


def system_with_sample_machine(tmp_path: Path):
    system = load_system(Path.cwd())
    return replace(
        system,
        runtime_dir=tmp_path / "runtime",
        state_machines={"memory_system_fabric": sample_machine_ir()},
    )


def test_transition_chain_persists_final_state_and_reports_missing_evidence(tmp_path: Path) -> None:
    system = system_with_sample_machine(tmp_path)

    result = execute_transition_chain(
        system,
        machine_id="memory_system_fabric",
        scope="cycle",
        key="cycle-001",
        initial_state="routing_wiki",
        requests=[
            {
                "event": "wiki.agent_layer.closed",
                "target_state": "building_reader_surface",
                "completed_steps": ("run_wiki_reader_loop", "render_wiki_engine_families"),
                "emitted_events": ("memory.reader_surface.ready",),
            },
            {
                "event": "memory.reader_surface.ready",
                "target_state": "complete",
                "produced_evidence": ("cycle_summary.recorded",),
                "completed_steps": ("append_cycle_summary_event",),
                "emitted_events": ("memory.cycle.complete",),
            },
        ],
    )

    assert result.initial_state == "routing_wiki"
    assert result.terminal_state == "complete"
    assert result.status == "missing_evidence"
    assert result.missing_expected_evidence == ("reader_surface.ready",)
    assert [execution.plan.transition_id for execution in result.transitions] == [
        "memory_system_fabric.cycle.routing_wiki--wiki.agent_layer.closed--building_reader_surface",
        "memory_system_fabric.cycle.building_reader_surface--memory.reader_surface.ready--complete",
    ]

    state = load_scope_state(
        system,
        machine_id="memory_system_fabric",
        scope="cycle",
        key="cycle-001",
    )
    assert state["state"] == "complete"
    assert state["status"] == "missing_evidence"
    assert state["transition_count"] == 2
    assert state["transitions"][0]["missing_expected_evidence"] == ["reader_surface.ready"]
    assert state["transitions"][1]["missing_expected_evidence"] == []
    assert state["history"][-1]["from"] == "routing_wiki"
    assert state["history"][-1]["to"] == "complete"


def test_transition_chain_can_resume_from_persisted_scope_state(tmp_path: Path) -> None:
    system = system_with_sample_machine(tmp_path)
    execute_transition_chain(
        system,
        machine_id="memory_system_fabric",
        scope="cycle",
        key="cycle-002",
        initial_state="routing_wiki",
        requests=[
            {
                "event": "wiki.agent_layer.closed",
                "produced_evidence": ("reader_surface.ready",),
            }
        ],
    )

    result = execute_transition_chain(
        system,
        machine_id="memory_system_fabric",
        scope="cycle",
        key="cycle-002",
        requests=[
            {
                "event": "memory.reader_surface.ready",
                "produced_evidence": ("cycle_summary.recorded",),
            }
        ],
    )

    assert result.initial_state == "building_reader_surface"
    assert result.terminal_state == "complete"
    assert result.status == "completed"


def test_transition_chain_can_persist_explicit_terminal_recovery_state(tmp_path: Path) -> None:
    system = system_with_sample_machine(tmp_path)

    result = execute_transition_chain(
        system,
        machine_id="memory_system_fabric",
        scope="cycle",
        key="cycle-003",
        initial_state="routing_wiki",
        terminal_state="failed",
        status="failed",
        requests=[
            {
                "event": "wiki.agent_layer.closed",
                "target_state": "building_reader_surface",
                "status": "failed",
            }
        ],
        note="reader build failed after transition selection",
    )

    assert result.initial_state == "routing_wiki"
    assert result.transitions[0].target_state == "building_reader_surface"
    assert result.terminal_state == "failed"
    assert result.status == "failed"

    state = load_scope_state(
        system,
        machine_id="memory_system_fabric",
        scope="cycle",
        key="cycle-003",
    )
    assert state["state"] == "failed"
    assert state["transitions"][0]["target_state"] == "building_reader_surface"
    assert state["note"] == "reader build failed after transition selection"


def test_transition_chain_can_persist_terminal_state_without_transition_requests(tmp_path: Path) -> None:
    system = system_with_sample_machine(tmp_path)

    result = execute_transition_chain(
        system,
        machine_id="memory_system_fabric",
        scope="cycle",
        key="cycle-004",
        initial_state="routing_wiki",
        terminal_state="blocked",
        status="blocked",
        requests=[],
        note="preflight blocked before transition execution",
    )

    assert result.initial_state == "routing_wiki"
    assert result.terminal_state == "blocked"
    assert result.transitions == ()

    state = load_scope_state(
        system,
        machine_id="memory_system_fabric",
        scope="cycle",
        key="cycle-004",
    )
    assert state["state"] == "blocked"
    assert state["transition_count"] == 0
