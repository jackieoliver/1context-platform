from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from onectx.config import MemorySystem
from onectx.state_machines.runtime import (
    StateMachineRuntimeError,
    TransitionChainExecution,
    TransitionRequest,
    execute_transition_chain,
    select_transition,
)


READER_SURFACE_CONTRACT = {
    "machine": "memory_system_fabric",
    "scope": "cycle",
    "source_state": "routing_wiki",
    "event": "wiki.agent_layer.closed",
    "target_state": "building_reader_surface",
}


@dataclass(frozen=True)
class MemoryCycleTransitionTrace:
    execution: TransitionChainExecution

    def to_tick_payload(self, *, cycle_id: str) -> dict[str, Any]:
        scope_state = self.execution.scope_state
        transitions = [transition.to_payload() for transition in self.execution.transitions]
        return {
            "machine": self.execution.machine_id,
            "scope": self.execution.scope,
            "cycle_id": cycle_id,
            "status": self.execution.status,
            "dry_run": self.execution.dry_run,
            "initial_state": self.execution.initial_state,
            "terminal_state": self.execution.terminal_state,
            "transition_count": len(transitions),
            "transitions": transitions,
            "scope_state": {
                "path": scope_state.get("path", ""),
                "state": scope_state.get("state", ""),
                "previous_state": scope_state.get("previous_state", ""),
                "updated_at": scope_state.get("updated_at", ""),
                "history_count": len(scope_state.get("history", []))
                if isinstance(scope_state.get("history"), list)
                else 0,
            },
            "note": self.execution.note,
        }


def transition_contract(
    system: MemorySystem,
    *,
    machine_id: str,
    scope: str,
    source_state: str,
    event_name: str,
    target_state: str,
) -> dict[str, Any]:
    try:
        plan = select_transition(
            system,
            machine_id=machine_id,
            scope=scope,
            source_state=source_state,
            event_name=event_name,
            target_state=target_state,
        )
    except StateMachineRuntimeError as exc:
        raise MemoryTransitionExecutorError(str(exc)) from exc
    payload = plan.to_payload()
    return {
        "machine": payload["machine"],
        "scope": payload["scope"],
        "transition": payload["transition"],
        "transition_index": payload["transition_index"],
        "event": payload["event"],
        "source": payload["source"],
        "target": payload["target"],
        "steps": payload["steps"],
        "expects": payload["expects"],
        "emits": payload["emits"],
    }


def reader_surface_ir_contract(system: MemorySystem) -> dict[str, Any]:
    return transition_contract(
        system,
        machine_id=READER_SURFACE_CONTRACT["machine"],
        scope=READER_SURFACE_CONTRACT["scope"],
        source_state=READER_SURFACE_CONTRACT["source_state"],
        event_name=READER_SURFACE_CONTRACT["event"],
        target_state=READER_SURFACE_CONTRACT["target_state"],
    )


def execute_memory_cycle_transitions(
    system: MemorySystem,
    *,
    cycle_id: str,
    status: str,
    dry_run: bool,
    execute_render: bool,
    render_count: int,
    manifest_count: int,
    route_count: int,
    route_plan_ready: bool = False,
    route_agent_layer_closed: bool = True,
) -> MemoryCycleTransitionTrace:
    initial_state = "validating" if route_plan_ready else READER_SURFACE_CONTRACT["source_state"]
    terminal_state = terminal_state_for_status(status)
    note = ""
    requests: list[TransitionRequest] = []

    if route_plan_ready:
        requests.append(
            TransitionRequest(
                event_name="memory.agent_outputs.closed",
                target_state=READER_SURFACE_CONTRACT["source_state"],
                status="passed",
                produced_evidence=("wiki_route_plan.ready",),
                completed_steps=("run_wiki_growth_fabric",),
                emitted_events=("wiki.fabric.tick",),
            )
        )
        terminal_state = READER_SURFACE_CONTRACT["source_state"]

    should_trace_reader = (
        execute_render
        and route_agent_layer_closed
        and status in {"completed", "failed", "retryable"}
    )
    if should_trace_reader:
        reader_ready = bool(render_count and manifest_count and route_count)
        requests.append(
            TransitionRequest(
                event_name=READER_SURFACE_CONTRACT["event"],
                target_state=READER_SURFACE_CONTRACT["target_state"],
                status="passed" if reader_ready else "failed",
                produced_evidence=("reader_surface.ready",) if reader_ready else (),
                completed_steps=("run_wiki_reader_loop", "render_wiki_engine_families"),
                emitted_events=("memory.reader_surface.ready",) if reader_ready else (),
            )
        )
        terminal_state = (
            READER_SURFACE_CONTRACT["target_state"]
            if reader_ready
            else terminal_state_for_status(status)
        )
        if reader_ready:
            requests.append(
                TransitionRequest(
                    event_name="memory.reader_surface.ready",
                    target_state="complete",
                    status="passed",
                    completed_steps=("append_cycle_summary_event",),
                    emitted_events=("memory.cycle.complete",),
                )
            )
            terminal_state = "complete"
    elif status == "completed" and dry_run:
        terminal_state = READER_SURFACE_CONTRACT["source_state"]
        note = "dry-run tick stopped before the reader-surface transition executed"
    elif status in {"blocked", "failed", "retryable"}:
        terminal_state = terminal_state_for_status(status)
        note = "cycle routed to terminal recovery state before reader-surface transition completed"

    try:
        execution = execute_transition_chain(
            system,
            machine_id=READER_SURFACE_CONTRACT["machine"],
            scope=READER_SURFACE_CONTRACT["scope"],
            key=cycle_id,
            initial_state=initial_state,
            requests=tuple(requests),
            status=status,
            terminal_state=terminal_state,
            dry_run=dry_run,
            note=note,
        )
    except StateMachineRuntimeError as exc:
        failure_note = str(exc)
        execution = execute_transition_chain(
            system,
            machine_id=READER_SURFACE_CONTRACT["machine"],
            scope=READER_SURFACE_CONTRACT["scope"],
            key=cycle_id,
            initial_state=initial_state,
            requests=(),
            status=status,
            terminal_state="failed",
            dry_run=True,
            note=failure_note,
        )
    return MemoryCycleTransitionTrace(execution)


def terminal_state_for_status(status: str) -> str:
    return {
        "completed": "complete",
        "blocked": "blocked",
        "retryable": "retryable",
        "failed": "failed",
    }.get(status, "failed")


class MemoryTransitionExecutorError(RuntimeError):
    """Raised when memory transition execution cannot map onto the compiled IR."""
