from __future__ import annotations

from onectx.memory.day_hourlies import DEFAULT_PROMPT_WARNING_TOKENS, agent_facing_events_for_estimate
from onectx.memory.hour_experience import MESSAGES_ONLY_EXPERIENCE_MODE, select_agent_facing_events
from onectx.storage.hour_events import HourEvent


def test_default_month_block_route_budget_is_256k() -> None:
    assert DEFAULT_PROMPT_WARNING_TOKENS == 256_000


def test_braided_lived_messages_keeps_all_messages_and_drops_tools() -> None:
    events = [
        make_event("2026-04-20T09:00:00Z", "user", "first user message"),
        make_event("2026-04-20T09:01:00Z", "tool_use", "Bash(ls)"),
        *[
            make_event(f"2026-04-20T09:{minute:02d}:00Z", "assistant", f"assistant message {minute}")
            for minute in range(2, 58)
        ],
        make_event("2026-04-20T09:58:00Z", "tool_result", "command output"),
        make_event("2026-04-20T09:59:00Z", "user", "last user message"),
    ]

    selected = select_agent_facing_events(events, max_events=4, experience_mode=MESSAGES_ONLY_EXPERIENCE_MODE)
    estimated = agent_facing_events_for_estimate(events, experience_mode=MESSAGES_ONLY_EXPERIENCE_MODE)

    assert [event.kind for event in selected] == ["user", *["assistant"] * 56, "user"]
    assert selected[0].text == "first user message"
    assert selected[-1].text == "last user message"
    assert len(selected) == 58
    assert selected == estimated


def make_event(ts: str, kind: str, text: str) -> HourEvent:
    return HourEvent(
        event_id=f"event-{ts}-{kind}",
        hash="hash",
        session_id="session-1",
        ts=ts,
        event="session.message",
        source="codex",
        kind=kind,
        actor="agent",
        cwd="/tmp",
        text=text,
        payload={},
    )
