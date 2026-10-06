from __future__ import annotations

from onectx.state_machines.growth import (
    OUTCOME_DEFER,
    OUTCOME_FORGET,
    OUTCOME_JOB,
    OUTCOME_NO_CHANGE,
    OUTCOME_NO_JOB,
    OUTCOME_SKIP,
    plan_wiki_memory_growth,
)


def test_concept_discovery_expands_existing_page_before_duplicate() -> None:
    plan = plan_wiki_memory_growth(
        [
            {
                "kind": "concept.discovered",
                "slug": "bt10",
                "existing_page": True,
                "page_path": "/wiki/concepts/bt10.md",
                "reinforcement_count": 1,
            }
        ]
    )

    assert plan.to_payload()["outcome_counts"][OUTCOME_JOB] == 1
    decision = plan.decisions[0]
    work = decision.work[0]
    assert decision.route_group == "librarian_jobs"
    assert work.machine == "wiki_growth_fabric"
    assert work.scope == "agent_role"
    assert work.event == "wiki.growth.job_planned"
    assert work.target_state == "queued"
    assert work.payload["job"] == "memory.wiki.librarian"
    assert work.payload["job_key"] == "librarian:concept:bt10"
    assert work.payload["ownership"]["existing_page"] is True
    assert "expanded before creating a duplicate" in decision.reason
    assert work.to_queue_kwargs()["queue_id"] == work.work_id


def test_weak_concept_discovery_defers_without_work() -> None:
    plan = plan_wiki_memory_growth(
        [
            {
                "kind": "concept.discovered",
                "slug": "half-seen-tool",
                "reinforcement_count": 1,
            }
        ]
    )

    assert plan.work == ()
    assert plan.decisions[0].outcome == OUTCOME_DEFER
    assert plan.to_payload()["non_job_outcomes"][0]["outcome"] == OUTCOME_DEFER


def test_page_growth_attaches_curator_lane_or_records_threshold_no_change() -> None:
    plan = plan_wiki_memory_growth(
        [
            {
                "kind": "page.grew",
                "page_slug": "2026-04-20",
                "page_kind": "for_you",
                "path": "/wiki/2026-04-20.md",
                "pending_proposal_count": 2,
                "pending_sections": ["2026-04-20", "2026-04-21"],
            },
            {
                "kind": "page.grew",
                "page_slug": "2026-04-27",
                "page_kind": "for_you",
                "filled_day_section_count": 2,
            },
        ]
    )

    curator = plan.decisions[0]
    assert curator.outcome == OUTCOME_JOB
    assert curator.route_group == "for_you_curator_jobs"
    assert curator.work[0].payload["job"] == "memory.wiki.for_you_curator"
    assert curator.work[0].payload["ownership"]["sections"] == ["2026-04-20", "2026-04-21"]

    no_change = plan.decisions[1]
    assert no_change.outcome == OUTCOME_NO_CHANGE
    assert no_change.work == ()


def test_operator_touched_page_growth_defers_before_mutating_job() -> None:
    plan = plan_wiki_memory_growth(
        [
            {
                "kind": "page.grew",
                "page_slug": "your-context",
                "page_kind": "your_context",
                "operator_touched": True,
                "pending_proposal_count": 3,
            }
        ]
    )

    assert plan.work == ()
    assert plan.decisions[0].outcome == OUTCOME_DEFER
    assert "operator-touched" in plan.decisions[0].reason


def test_contradiction_evidence_emits_follow_up_job() -> None:
    plan = plan_wiki_memory_growth(
        [
            {
                "kind": "evidence.appeared",
                "key": "claim-001",
                "evidence_kind": "contradiction",
                "target_page": "/wiki/concepts/1context.md",
            }
        ]
    )

    assert plan.decisions[0].outcome == OUTCOME_JOB
    assert plan.decisions[0].route_group == "contradiction_jobs"
    assert plan.work[0].payload["job"] == "memory.wiki.contradiction_flagger"
    assert plan.events[0].name == "wiki.contradiction_job_planned"


def test_explicit_non_job_outcomes_are_first_class() -> None:
    plan = plan_wiki_memory_growth(
        [
            {"kind": "page.discovered", "page_slug": "archive-me", "outcome": "forget", "reason": "fading concept"},
            {"kind": "page.discovered", "page_slug": "already-current", "outcome": "skip"},
            {"kind": "unknown.fact", "key": "quiet"},
        ]
    )

    assert [decision.outcome for decision in plan.decisions] == [OUTCOME_FORGET, OUTCOME_SKIP, OUTCOME_NO_JOB]
    assert plan.work == ()
    assert plan.to_payload()["outcome_counts"][OUTCOME_FORGET] == 1
    assert plan.to_payload()["outcome_counts"][OUTCOME_SKIP] == 1
    assert plan.to_payload()["outcome_counts"][OUTCOME_NO_JOB] == 1
