# Translating e08 Into The Portable Memory System

This document explains how to turn the `1Context-private-2`
`experiments/e08-for-you` prototype into the `base-memory-v1`
plugin, state-machine, hired-agent, and wiki-runtime system.

It is not a request to faithfully preserve the experiment's shell
scripts. e08 proved the product shape. The private-4 system should
preserve the lessons and improve the mechanics.

The key translation is:

```text
e08 shell lab
  proof that the memory wiki can work

base-memory-v1 plugin
  portable contracts, prompts, route plans, deterministic builders,
  hired-agent birth records, and state-machine control
```

## Source Read

The e08 record has been read through:

```text
0000-format.md
...
0037-unclosed-proposals-index.md
```

The important late-stage conclusions are:

```text
0024:
  The architecture is multi-week-validated.
  The wiki survives era boundaries.
  The remaining blocker for true week-three operation is fresh event data.

0025-0026:
  Real-time behavior should be validated by replaying historic events with
  an event-stream clock before trusting a live daemon cadence.

0027:
  Fresh event import is not optional infrastructure. It is a state/evidence
  gate. A stale importer can make the wiki look valid while the source corpus
  is days behind reality.

0028-0030:
  Production safety needs compute-not-enumerate discipline, runtime invariants,
  and an explicit distinction between replay validation and live execution
  invariants.

0032-0037:
  The e08 notebook is a production-builder handoff. The architecture must carry
  forward the 7-phase wiki pipeline, first-class forgetting/skipping, migration
  receipts for contract changes, quality-audit findings, and the honest gaps:
  scheduler stability, replay Phase 3, quality probes, cost economics, and
  tier reconciliation. 0037 adds one more scale lesson: long-lived proposal
  discovery should be index-driven, not talk-folder traversal-driven.
```

That means private-4 should no longer treat e08 as a loose inspiration. It is
the behavioral spec for the memory wiki.

## Product Shape To Preserve

e08 converged on a personal context wiki with these surfaces:

```text
For You article
  rolling 14-day operator narrative
  day sections
  weekly Biography section
  private canonical source
  internal/public lower-fidelity outputs

For You talk folders
  append-only working venue
  hourly conversations
  historian questions
  editor proposals
  curator decisions
  redaction summaries

Your Context
  durable operator manual
  working style, taste, desires, habits, standing requests
  life story section later

Concept pages
  named-subject wiki
  created/expanded by librarian
  categorized by frontmatter
  backlinks and open questions

Topics / Projects
  generated indexes from concept metadata

Open Questions
  generated worklist from open-thread sections and article text

This Week
  generated recent-changes digest from talk decisions and biography

Landing page
  generated front door with latest For You, Your Context, Open Questions,
  This Week, and most-cited concepts
```

The system should preserve the wiki feeling: not a report generator, not a
chatbot transcript dump, but a living context wiki with talk pages, article
pages, concept pages, redaction tiers, and visible maintenance state.

## Translate, Do Not Recreate

The experiment used shell scripts because that was the fastest way to learn.
The portable system should translate each shell idea into one of four surfaces:

| e08 artifact | Portable system surface |
|---|---|
| `lab/run-*.sh` | job manifests, runner adapters, route-plan executor |
| Prompt markdown | plugin prompts copied with minimal semantic changes |
| Talk/article/concept files | user workspace content under `~/1Context` |
| Generated indexes/scripts | deterministic Python wiki builders |
| Shell orchestration | `wiki_growth_fabric` state machine and daemon tick |
| Scratch logs | hired-agent ledger, artifacts, evidence rows |
| Hardcoded paths | host config and release storage roots |

Keep the prompts close to e08 unless there is an obvious upgrade. Improve the
runtime around them.

## Experiment-To-System Map

### 0000-0004: Format, Voice, Isolation

What e08 proved:

- Lab records need frontmatter, hypothesis, setup, run, observations,
  followups.
- Agent voice is load-bearing. Neutral, concrete, non-marketing prose matters.
- `skip` and `<no-talk>` are real outputs.
- Isolation matters, but the exact `--bare` mechanism was experimental.

System translation:

- Keep lab-note structure as the pattern for durable experiment/result docs.
- Keep `prompts/agent-profile.md` as the shared role identity.
- Store `skip`, `forget`, `defer`, `no_change`, `needs_approval`,
  `needs_wider_context`, and `failure` as typed outcomes.
- Use `account_clean` Claude mode for role jobs:
  local Claude subscription auth, temp cwd, no `CLAUDE.md`,
  no session persistence, explicit tool policy.
- For lived-experience jobs, isolation does not mean "empty context"; it means
  a clean agent born with the intended source packet and nothing accidental.

### 0005-0008: Talk Folders, Hourlies, Historian, Answerers

What e08 proved:

- Talk pages should be folders, closer to Maildir or LKML thread archives than
  one giant markdown file.
- Hourly entries are not color commentary. They are canonical working memory.
- The historian asks why. The hourly answerer answers a specific question about
  one hour.
- First-pass hourlies were expensive because agents rediscovered the hour
  through tools.

System translation:

- Use `.talk/` folders as first-class workspace artifacts.
- Preserve the hourly/historian/answerer role split.
- Upgrade hourly source loading: render a braided lived-experience packet
  outside the agent, then birth the scribe/answerer with that packet loaded in
  the initial context.
- Keep tools available when useful, but do not make bounded hourly jobs spend
  their budget rediscovering source events by default.
- Record the experience packet path/hash/source window on the hired-agent birth
  certificate.

### 0009-0010: Parallelism And Finishing Roadmap

What e08 proved:

- Parallelism is necessary. Serial weekly generation is too slow.
- `CONCURRENCY=4` was safe in the shell lab; higher caps should be configurable.
- Agents should be additive by default. Holistic rewrites are exceptional.
- Batch runs are "fast-forwarded reality": they simulate days/weeks of normal
  daemon ticks.

System translation:

- Default `runtime_policy.max_concurrent_agents` should be configurable, with
  8 as the current working target and role-specific lower caps where needed.
- Prefer fixed 4-hour source chunks for high-throughput month processing.
- If a source packet exceeds the current route budget, split to another bounded hire instead
  of forcing one giant prompt.
- The DSL should model concurrency, backpressure, timeout, retry, and
  reconciliation.
- The planner should avoid launching jobs whose outcome is already knowable as
  `skip`, `forget`, `defer`, or `no_change`.

### 0011: Editor And For You Curator

What e08 proved:

- The editor writes proposals, not final article mutations.
- The For You curator owns applying editor proposals to article sections.
- Curators should process proposals oldest-first, but newest proposal wins for
  the same target.
- A curator is page-type-specific, not a universal editor.

System translation:

- Keep `memory.daily.editor` or rename it clearly as For You editor if the job
  surface grows.
- Keep `memory.wiki.for_you_curator`.
- Route rows must include target era, target day, article path, talk folder,
  ownership section, and proposal sources.
- Mutation gates must block edits outside the owned section.
- Operator-touched content is a hard preflight and post-diff validator concern.

### 0012-0013: Librarian, Forgetting, Contradictions

What e08 proved:

- The librarian is both gatekeeper and mason: decide whether a concept earns a
  page, then create or expand it.
- Two-of-three notability matters: For You narrative weight, Your Context
  stable framing, and talk-folder evidence.
- Forgetting is first-class. Demotion, fading, archival, and no-change are
  memory behavior, not failed generation.
- Contradiction flagger surfaces conflicts but does not resolve them.

System translation:

- Keep `memory.wiki.librarian`, `memory.wiki.librarian_sweep`, and
  `memory.wiki.contradiction_flagger`.
- Route plans must include concept proposals, existing concept pages, category
  metadata, reinforcement state, and prior decisions.
- Librarian must expand existing pages before creating duplicates.
- Sweep cadence is slower than generation; fading needs multiple weeks of no
  reinforcement.
- Contradiction output is a talk entry / flag artifact. It must not edit source
  pages.

### 0014, 0020-0022: Reader Loop And Front Door

What e08 proved:

- Bracket links, aliases, external fallbacks, backlinks, Open Questions, This
  Week, and the landing page radically improve the reader experience.
- Some surfaces are better as deterministic generators than agents.
- Public-tier rendering exposed a model mismatch: e08's redactor writes
  separate tier source files, while the imported renderer originally expected
  section-tagged streams.

System translation:

- Keep deterministic generation in `src/onectx/memory/wiki.py` and future
  `src/onectx/wiki/` renderer integration.
- Treat wiki rendering as a separate evidence-producing subsystem:
  `memory/wiki.py` derives planner inputs and role routes; `wiki/` discovers
  page families, ensures source/talk/template scaffolding, renders pages,
  writes manifests, serves localhost routes, and records render evidence.
- Preserve:
  - Topics index
  - Projects index
  - Open Questions page
  - This Week digest
  - landing page
  - bracket resolver
  - aliases and external fallbacks
  - backlinks / "What links here"
  - brackify pass for legacy or hand-authored text
- Do not ask agents to regenerate indexes that can be derived from structured
  frontmatter and markdown.
- A state-machine cycle is not fully reader-ready until generated inputs have
  been rendered and the lakestore has `wiki.render.succeeded`,
  `wiki.manifest.recorded`, and `wiki.generated.available` evidence.
- Resolve the tier model deliberately:
  - either renderer accepts tier-suffixed source files, or
  - redactor produces section audience metadata.
  Until decided, treat `.internal.md` and `.public.md` as redaction artifacts
  and keep render trust behind manifests/evidence.

### 0015-0016: Biographer And Redactor

What e08 proved:

- The biographer is the rare holistic rewrite role. It owns the weekly
  Biography section and reads prior 1-2 weeks for continuity.
- The redactor produces lower-fidelity views and writes redaction summaries.
- Private canonical truth must survive redaction.

System translation:

- Keep `memory.wiki.biographer` and `memory.wiki.redactor`.
- Route biographer only when enough weekly material exists or biography is stale.
- Biographer source packets should include current era day sections and prior
  biography/open threads.
- Redactor never mutates source. It writes target-tier artifact files and a
  `[REDACTED]` talk entry.
- Validators should compare target-tier size/shape and ensure private source
  hash is unchanged.

### 0017-0018: Curator Generators And Orchestrator

What e08 proved:

- Topics and Projects are generated from concept metadata.
- The weekly pipeline has seven phases:
  generate, synthesize, curate, concept/biography, prune, redact, render.
- One command is useful, but the true runtime wants a daemon/state-machine tick.

System translation:

- Keep generated indexes deterministic.
- Translate `run-week-pipeline.sh` into:
  - route planning
  - route execution
  - typed outcomes
  - evidence validation
  - reader rebuild
  - daemon reconciliation
- The state machine should express the phase boundaries and artifact contracts,
  not every shell subcommand.

### 0037: Indexed Proposal Discovery

What e08 proved:

- Walking all talk folders to discover pending proposals is the wrong long-run
  shape. It scales linearly with corpus age and gives evaluator agents too much
  stale context.
- A small `.unclosed-proposals.json` index is the right shape: proposal writes
  add entries, decision/defer writes remove entries, and a bootstrap migration
  catches old unclosed proposals.
- The pattern is the same as `_backlinks.json`: precompute relationships once,
  then let agents pull the small set they need.

System translation:

- Add unclosed-proposal indexes to wiki inventory and route planning.
- The librarian should read pending concept proposals from an index, sorted by
  age/staleness, then pull targeted proposal bodies.
- The same index-family should eventually cover unresolved contradictions,
  stale initial-fill markers, deferred proposals eligible for reconsideration,
  and stale open questions.
- Shipping this is a Phase 0.5 migration task: bootstrap the index from any
  existing talk folders before relying on it for routing.

### 0019: Full System Summary

What e08 proved:

- The system is not a single agent. It is a layered wiki:
  10 roles, deterministic scripts, talk folders, concept graph, redaction, and
  rendered reader surfaces.
- The main gaps at 0019 were reader-side: Your Context links, categories,
  public tier rendering, empty 4/13, production deployment.

System translation:

- Keep the role count and role boundaries.
- Keep the exact prompt lessons unless an upgrade is obvious.
- Treat reader-side deterministic output as part of the memory product, not
  optional polish.
- Require generated output receipts before the daemon/menu trusts local URLs.

### 0023-0024: Multi-Week And Second-Week Validation

What e08 proved:

- Newest-overwrites works: newer era proposals are sharper readings of older
  days and should become canonical unless operator-touched content blocks them.
- Era initialization is mandatory.
- Adjacent era talk folders must be explicit inputs, not prompt folklore.
- Biographer prior-week awareness works.
- Contradiction flagger learned from prior flags and avoided duplicates.
- Dynamic era computation is mandatory; hardcoded era case statements fail.
- Renderer/morning-prep must discover era files dynamically, not from a
  hardcoded list.
- Canonical `<era>.md` is the private source. Requiring a phantom
  `<era>.private.md` is wrong.
- Fresh event import is the remaining blocker for true week-three operation.

System translation:

- Add era helpers in Python:
  - `monday_anchor(date)`
  - `rolling_window(anchor, days=14)`
  - `adjacent_eras(anchor, span=1)`
  - `article_paths(anchor, tier_model)`
- Route plan rows must carry:
  - run era
  - target day/window
  - adjacent talk folders
  - prior biography paths
  - newest-overwrites policy
  - operator-touched scan result
- Reader builders should render by manifest or file discovery, not hardcoded
  dates.
- Freshness gates should block or defer multi-week generation when event import
  is stale.

### 0025-0027: Replay Harness And Fresh Import

What e08 proved:

- Batch runs are not enough to design real-time behavior safely.
- Historic event replay is the right bridge from batch to live operation:
  replay actual session events chronologically, use event-stream time for
  hourly/daily/weekly boundaries, and fire the same agents/runners that live
  mode would fire.
- Replay can answer cadence, latency, mid-day UX, week-boundary, failure
  recovery, concurrent-edit, and per-agent budget questions without waiting
  days or weeks.
- The replay harness verified a full-week dry-run schedule: hourly scribes,
  daily historian/answerer/editor roles, weekly curators/biographer/librarian,
  and contradiction flagger.
- The scheduler can be dumb if runners are smart: cadence fires may be
  unconditional, while route/job preparation records `skip`, `no-talk`, or
  `no_change` when there is no useful work.
- Fresh event import is a hard dependency. In e08 the experiment DB was stale
  for five days because import was manual, even though product launchd agents
  were running elsewhere.

System translation:

- Add replay/freshness concepts to the top-level `memory_system_fabric`.
- Treat importer cursors as evidence, not logs:
  - source id
  - latest imported timestamp
  - source coverage window
  - mtime/checkpoint
  - staleness threshold
- Multi-day, month, and real-time runs should defer with
  `needs_fresh_events`/`defer` when required importer cursors are stale.
- Port replay from e08 shell orchestration into private-4 as orchestration over
  the real route planner, prompt-stack builder, hired-agent runner, validator,
  and wiki builders.
- Replay artifacts should be first-class:
  - `config.json`
  - `events.jsonl`
  - `fires.jsonl`
  - `snapshots/`
  - `summary.md` or `summary.json`
- Use replay to tune default cadence and concurrency before turning on live
  real-time behavior.

## Current Private-4 Coverage

Already represented in private-4:

- e08 prompts copied into `memory/plugins/base-memory-v1/prompts/`.
- e08 role contracts copied into `agents/` and `jobs/`.
- `memory_system_fabric` state machine captures the top-level memory loop:
  ingest, freshness, route planning, lived-experience rendering, hired-agent
  birth, execution, validation, wiki routing, reader build, replay, and ledger
  feedback.
- `wiki_growth_fabric` state machine captures dynamic role routing.
- `wiki_reader_loop` captures deterministic reader rebuild.
- `memory.hourly.scribe` has the lived-experience upgrade e08 did not have.
- `src/onectx/memory/wiki.py` has deterministic Topics, Projects, Open
  Questions, Landing, This Week, bracket resolution, backlinks, and route
  planning.
- `wiki route-dry-run` previews hired-agent births from route rows without
  launching agents.
- `wiki route-dry-run --write-artifact` persists a first route execution
  preview artifact and records `source_import.fresh` evidence.
- `memory replay-dry-run` ports the first e08 replay idea into private-4:
  it reads lakestore events, derives cadence fires, writes `events.jsonl`,
  `fires.jsonl`, and `summary.json`, and records `replay_schedule.ready`
  evidence without launching agents.
- Era helpers now compute Monday anchors, rolling 14-day windows, adjacent talk
  folders, and separate tier-file paths without hardcoded era lists.
- CLI has `wiki build-inputs`, `wiki plan-roles`, and `wiki brackify`.
- Runtime policy has configurable max concurrent agents.

Still missing or incomplete:

- Replay harness has only its first dry-run scheduler in private-4. Snapshots,
  failure injection, route-planner integration, and real-fire replay remain.
- Generic route-plan executor that writes concrete runtime prompt/source packet
  files and launches or queues jobs. The current dry-run now persists a route
  execution preview, but it does not yet render per-role source packets or run
  harnesses.
- Route-plan persistence in runtime storage and lakestore.
- Source/importer freshness rows and gates.
- Unclosed-proposals index and bootstrap migration from e08 0037.
- Era initialization command.
- Role prompt-stack dry-run for every route row.
- Mutation validators for curator, librarian, biographer, redactor.
- Hard timeout, retry, stuck-agent reconciliation.
- Renderer/tier model decision and bridge.
- Wiki-engine render manifest/evidence integration in this repo's mainline.
- Release-root portability validation against `~/1Context` and Library roots.

## Translation Rules

Use these rules when porting any remaining e08 feature:

1. **Prompts are valuable; runners are prototypes.**
   Copy prompt semantics. Replace shell runner mechanics with manifests,
   route rows, prompt-stack assembly, and hired-agent execution.

2. **Agents decide meaning; deterministic code maintains graph shape.**
   Agents write prose, proposals, judgments, redactions, and flags. Code builds
   indexes, backlinks, route tables, manifests, and freshness checks.

3. **Skip and forget are successful outcomes.**
   The planner should save time by not hiring agents when silence is the right
   memory behavior.

4. **Birth-loaded context is literal.**
   When a role depends on source experience, the source packet goes into the
   initial harness prompt/context, not merely into a directory the agent might
   read.

5. **Era windows are computed, never hardcoded.**
   Any case statement listing `2026-04-13`, `2026-04-20`, `2026-04-27` is a
   bug in the portable system.

6. **Canonical private source is `<era>.md`.**
   Do not require `<era>.private.md` unless the tier model is intentionally
   changed.

7. **Operator-touched is sacred.**
   It is both a route-plan fact and a mutation validator.

8. **Artifacts need receipts.**
   Render manifests, route-plan artifacts, hired-agent birth certificates,
   output validators, and evidence rows are how the state machine knows what is
   real.

9. **Speed is a design requirement.**
   Use route planning, chunking, source-packet cleaning, concurrency, and
   deterministic generators to keep a month-run plausibly under one hour.

10. **The wiki should get sharper over time.**
    Newer era proposals improve older day sections, unless protected by
    operator-touched markers. History remains in talk folders.

11. **Replay before live real-time.**
    Use historic event replay to validate cadence, latency, failure recovery,
    concurrent edits, and in-progress wiki UX before trusting the live daemon.

12. **Fresh import is a gate.**
    A source corpus that is days stale must produce a typed defer/block, not a
    confident multi-week memory artifact.

## Target Portable Pipeline

```text
daemon tick or CLI command
  -> import freshness check
  -> scan wiki inventory
  -> initialize era skeletons if needed
  -> derive role route plan
  -> persist route plan artifact and lakestore row
  -> prepare prompt/source packets
  -> birth hired agents through Claude account_clean or Codex harness
  -> run bounded jobs with concurrency, timeout, retry
  -> validate outputs and mutations
  -> record typed outcomes and evidence
  -> rebuild deterministic reader surface
  -> render wiki and record render manifest/evidence
  -> next tick starts from facts, not memory of the last script run
```

Real-time validation path:

```text
historic event replay request
  -> load events in timestamp order
  -> map event timestamps to event-stream clock
  -> derive cadence fires from hour/day/week/month boundaries
  -> dry-run or live-fire the same route planner and hired-agent runner
  -> capture fires.jsonl, snapshots, timings, costs, failures
  -> update runtime policy from replay evidence
  -> only then enable or adjust live daemon cadence
```

## Immediate Implementation Order

1. Port e08 `0025-0027` replay/freshness lessons into docs and DSL.
2. Add reusable era/window helpers and tests.
3. Extend `wiki plan-roles` rows with era windows, adjacent talk folders, prior
   biography paths, freshness, ownership, budget, and skip reasons.
4. Add dry-run route-plan executor that assembles prompt stacks and birth
   certificates without launching agents.
5. Add freshness/importer gate for session-derived work.
6. Add private-4 replay dry-run over current route planner and runner specs.
7. Add era-init command that produces the e08 skeleton without shell scripts.
8. Add validators for the first mutating roles.
9. Wire live execution for a small route plan.
10. Wire reader render manifest/evidence.

The first proof should be a copied e08 fixture where the route executor dry-run
prepares every role, skips at least one route for a positive reason, and shows
the exact source packets each role would be born with.
