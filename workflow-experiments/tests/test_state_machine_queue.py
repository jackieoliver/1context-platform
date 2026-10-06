from __future__ import annotations

import json
from pathlib import Path

import pytest

from onectx.state_machines.queue import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_NEEDS_RETRY,
    STATUS_RUNNING,
    StateMachineQueueError,
    StateMachineWorkQueue,
)


def test_queue_persists_across_reload(tmp_path: Path) -> None:
    queue = StateMachineWorkQueue.load(tmp_path / "queue")

    item = queue.enqueue(
        machine="memory.for_you_day",
        scope="day",
        key="2026-04-29",
        event="tick",
        source_state="collecting",
        target_state="drafting",
        payload={"reason": "daemon"},
        now="2026-04-29T12:00:00Z",
    )

    reloaded = StateMachineWorkQueue.load(tmp_path / "queue")

    assert reloaded.get(item.queue_id) == item
    assert reloaded.list()[0].payload == {"reason": "daemon"}
    data = json.loads((tmp_path / "queue" / "state_machine_work_queue.json").read_text(encoding="utf-8"))
    assert data["items"][0]["queue_id"] == item.queue_id


def test_mark_running_increments_attempts(tmp_path: Path) -> None:
    queue = StateMachineWorkQueue.load(tmp_path / "queue.json")
    item = queue.enqueue(
        machine="memory.for_you_day",
        scope="day",
        key="2026-04-29",
        event="retry",
        now="2026-04-29T12:00:00Z",
    )

    running = queue.mark_running(item.queue_id, now="2026-04-29T12:01:00Z")

    assert running.status == STATUS_RUNNING
    assert running.attempts == 1
    assert running.updated_at == "2026-04-29T12:01:00Z"


def test_retry_budget_moves_from_needs_retry_to_failed(tmp_path: Path) -> None:
    queue = StateMachineWorkQueue.load(tmp_path / "queue.json")
    item = queue.enqueue(
        machine="memory.for_you_day",
        scope="day",
        key="2026-04-29",
        event="tick",
        max_attempts=2,
        now="2026-04-29T12:00:00Z",
    )

    first_attempt = queue.mark_running(item.queue_id, now="2026-04-29T12:01:00Z")
    retry = queue.mark_retryable(
        first_attempt.queue_id,
        scheduled_at="2026-04-29T12:05:00Z",
        payload={"error": "timeout"},
        now="2026-04-29T12:02:00Z",
    )
    second_attempt = queue.mark_running(retry.queue_id, now="2026-04-29T12:05:00Z")
    exhausted = queue.mark_retryable(
        second_attempt.queue_id,
        payload={"last_error": "timeout again"},
        now="2026-04-29T12:06:00Z",
    )

    assert retry.status == STATUS_NEEDS_RETRY
    assert retry.retryable is True
    assert retry.attempts == 1
    assert retry.attempts_remaining == 1
    assert second_attempt.attempts == 2
    assert exhausted.status == STATUS_FAILED
    assert exhausted.retryable is False
    assert exhausted.attempts_remaining == 0
    assert exhausted.payload == {"error": "timeout", "last_error": "timeout again"}


def test_idempotent_upsert_preserves_identity_and_attempts(tmp_path: Path) -> None:
    queue = StateMachineWorkQueue.load(tmp_path / "queue.json")
    first = queue.upsert(
        machine="memory.for_you_day",
        scope="day",
        key="2026-04-29",
        event="tick",
        source_state="collecting",
        target_state="drafting",
        payload={"ordinal": 1},
        now="2026-04-29T12:00:00Z",
    )
    queue.mark_running(first.queue_id, now="2026-04-29T12:01:00Z")

    second = queue.upsert(
        machine="memory.for_you_day",
        scope="day",
        key="2026-04-29",
        event="tick",
        source_state="collecting",
        target_state="drafting",
        payload={"ordinal": 2},
        now="2026-04-29T12:02:00Z",
    )

    assert second.queue_id == first.queue_id
    assert second.created_at == first.created_at
    assert second.attempts == 1
    assert second.payload == {"ordinal": 2}
    assert len(queue.list()) == 1

    third = queue.upsert(
        machine="memory.for_you_day",
        scope="day",
        key="2026-04-29",
        event="tick",
        source_state="collecting",
        target_state="drafting",
        now="2026-04-29T12:03:00Z",
    )

    assert third.queue_id == first.queue_id
    assert third.payload == {"ordinal": 2}
    assert len(queue.list()) == 1


def test_terminal_items_cannot_be_marked_running(tmp_path: Path) -> None:
    queue = StateMachineWorkQueue.load(tmp_path / "queue.json")
    item = queue.enqueue(
        machine="memory.for_you_day",
        scope="day",
        key="2026-04-29",
        event="tick",
    )
    done = queue.mark_done(item.queue_id)

    with pytest.raises(StateMachineQueueError):
        queue.mark_running(done.queue_id)

    assert queue.list(STATUS_DONE) == [done]
