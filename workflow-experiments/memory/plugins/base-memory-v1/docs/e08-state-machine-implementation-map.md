# e08 State Machine Implementation Map

This is the state-machine-facing read of `1Context-private-2`
`experiments/e08-for-you` through `0037`.

The important translation is not "port the shell scripts." The scripts were
lab scaffolding. The production system should preserve the behavioral contracts
the experiments discovered and replace shell orchestration with route planning,
queueing, hired-agent births, typed outcomes, evidence validation, and reader
surface receipts.

## Core Fabric

The final e08 system is a dynamic wiki fabric:

```text
event ingest
  -> freshness gate
  -> era init
  -> Phase 0.5 migrations/backfills
  -> route planning
  -> lived experience packet rendering
  -> hired agent births
  -> artifact validation and reconciliation
  -> deterministic wiki generation
  -> render/manifests/routes
  -> next tick from evidence
```

The state machine should treat these as durable phases with receipts, not as a
single best-effort command.

## Fast Month Policy

The default high-throughput catch-up path uses fixed 4-hour scribe blocks with
`runtime_policy.max_concurrent_agents` set to 8 and a 256k estimated-token route
budget.

That budget is a decision surface, not a deletion policy:

```text
default lived packet = braided_lived_messages
included = all user + assistant messages in the selected window
excluded = tool calls/results
sampling = none
tool traces = retained in lakestore for replay, retry, and forensic expansion
```

Automatic splitting is an explicit retry/escalation path, not the default
month run. Dense blocks should first run as fixed 4-hour blocks unless they fail
context/runtime/output validation.

## Required Outcomes

Every job must end in one typed outcome:

```text
produced
skipped_empty_input
skipped_already_current
no_change_checked
deferred
forgotten_or_fading
needs_wider_window
needs_retry
needs_approval
failed_retryable
failed_terminal
```

`skip`, `forget`, `defer`, and `no_change` are valid outcomes. The invariant is
not "every route writes prose." The invariant is "every expected route resolves
to a typed outcome with evidence."

## Role Circuits

The dynamic fabric should be able to activate these role circuits:

| Role | Cadence | Mutates | State-machine contract |
|---|---:|---|---|
| hourly/block scribe | hourly or fixed 4-hour block | talk entry only | birth-loaded experience packet, write conversation entries or no-talk/needs-retry |
| historian | daily | talk entries only | syntheses, questions, ycx proposals, concept proposals |
| hourly answerer | after historian questions | talk reply only | answer one bounded question from lived hour; may request wider window |
| daily editor | daily/end-of-day | proposal only | write `proposal.editor-day-*`; never mutate article |
| For You curator | daily/weekly | For You sections | process proposals oldest-first, newest-era wins, operator-touched blocks mutation |
| Your Context curator | weekly/on proposals | Your Context sections | fill stable profile sections, revisit initial-fill markers, bracket recurring subjects |
| librarian | weekly | concept pages + decisions | two-of-three rule, expand before duplicate, frontmatter audit, decisions/deferred entries |
| librarian sweep | monthly | concept frontmatter + fading/archive proposals | forgetting, not generation |
| biographer | weekly | Biography section | only holistic rewrite role; thin-week and quiet-era modes required |
| contradiction flagger | weekly | contradiction talk entries only | flags drift; never resolves by editing pages |
| redactor | weekly/tiered | tier artifacts | Private -> Internal -> Public sequential; source unchanged |
| deterministic generators | every render | generated pages | topics, projects, open questions, this week, landing, backlinks, bracket resolution |

## Dynamic Growth Inputs

The route planner should derive work from indexes and facts:

```text
wiki_inventory
  pages, sections, frontmatter, schema_version
  talk entries by kind and parent
  operator-touched markers
  open questions, concerns, contradictions
  concept pages and aliases
  generated index state

proposal indexes
  .unclosed-proposals.json
  future: unresolved-contradictions.json
  future: stale-initial-fill.json
  future: deferred-reconsideration.json
```

Do not make the librarian walk all talk folders at agent-fire time. e08 0037
settled this: proposal discovery should be index-driven.

## Production Validation

Three validation layers are required:

```text
audit / computed planning
  compute dates, eras, windows, concept lists, and file routes
  never enumerate hardcoded era/date lists

runtime invariants
  pre-flight expected work
  post-flight produced artifacts
  diff with skip/no-change reasons
  zero silent no-ops

replay validation
  historic event-stream clock
  snapshots
  failure/operator-edit injection
  behavior and UX checks
```

Replay validates behavior. Invariants validate execution. Migrations keep older
artifacts healthy after contract changes.

### Validator Philosophy

Validators should protect the state-machine contract, not flatten the prose.
This is especially important for hourly/block scribes, historians, biographers,
curators, and concept work, where the lively record is the product.

Hard validators should check:

```text
artifact exists
frontmatter/schema parses
kind, timestamp, author, source window, and target route are coherent
typed outcome is explicit
unsafe writes are blocked
operator-touched regions are respected
expected work reconciles with produced/skip/defer/retry evidence
```

They should not require a fixed section template, a specific heading order, or a
literal phrase when the prompt allows paragraph prose. Style and quality belong
to prompts, reader loops, audits, and review agents; runtime validators should
only reject output when the artifact cannot be trusted or routed.

Semantic signals such as "mentions an open thread" or "sounds operationally
grounded" may be recorded as warnings or review hints. They should not be hard
failures for prose artifacts unless the artifact kind itself is a specialized
flag or decision that cannot function without that field.

## Reader-State Model

For You day sections need reader-visible state, not just empty placeholders:

```text
future_day
quiet_day
in_progress_events_seen
draft_pending_curator
complete
```

The renderer/state machine should derive this from date, event coverage, talk
folder entries, editor proposals, curator decisions, and article body content.
Otherwise in-progress, broken, and genuinely quiet days all look the same.

## Prompt Parity Notes

Late e08 prompt deltas that must stay in the plugin:

- Biographer has thin-week and quiet-era modes, not binary skip under 3 days.
- Librarian expand-mode audits frontmatter and logs backfill decisions.
- Daily editor must preserve proposal mode, run-era talk folder writes, and
  newest-overwrites semantics.
- Hourly/block scribes write from loaded lived experience by default, but may
  use web search for external grounding.
- Curator prompts must treat `<!-- operator-touched: ... -->` as a hard
  mutation boundary.
- Your Context curator must bracket recurring named subjects and revisit stale
  "initial fill" markers.

## Merge Target

The immediate executable state machine should prove:

```text
one cycle
  -> discovers source windows
  -> plans fixed 4-hour scribe blocks
  -> renders lived packets
  -> births account_clean Claude hires
  -> enforces max concurrency
  -> reconciles outputs into typed outcomes
  -> validates runtime invariants
  -> builds wiki inputs and reader artifacts
```

Once that works, add:

```text
daily editor/curator loop
weekly biographer/librarian/flagger/redactor loop
Phase 0.5 migrations
unclosed proposal index
replay snapshots and injection
reader-state rendering
```

## Faithful Recreation Acceptance Gates

Faithful recreation means preserving e08's final behavior while replacing the
lab shell mechanics with typed state-machine infrastructure. A merge is not
complete until these gates are true:

```text
prompt parity
  shared agent profile and role prompts match the late e08 semantics
  skip/forget/defer/no-change language is explicit in every role that needs it
  curator/librarian/biographer prompts carry the 0034/0035 patches

role fabric
  every role circuit can be represented as queued state-machine work
  every work item has an owner, cadence, source packet, target artifact, and
  typed outcome
  dynamic growth uses indexes/facts rather than hardcoded date or era lists

birth-loaded context
  hired agents receive the intended lived packet in initial context
  the packet is user+assistant source by default, with tool traces retained in
  lakestore for explicit retry/forensic expansion
  source window, mode, path, and digest are written to the birth certificate

wiki mutation safety
  proposals, decisions, curator mutations, librarian changes, redactions, and
  generated indexes each have validators
  operator-touched regions block mutation before and after edits
  deterministic builders regenerate reader indexes unconditionally

runtime truth
  pre-flight expected work and post-flight artifacts are reconciled
  skip-as-first-class is distinguishable from config-bug no-op
  failures, stuck hires, retryable outputs, and terminal failures are visible

reader surface
  For You, Your Context, concept pages, talk folders, Topics, Projects, Open
  Questions, This Week, backlinks, and landing all have generated receipts
  day sections expose future/quiet/in-progress/pending/complete state
```

## Parallel Build Slices

The current production push is split into four mergeable slices:

| Slice | Owns | Done when |
|---|---|---|
| prompt parity | plugin prompts, agent/job manifests | late e08 prompt semantics are copied or intentionally upgraded, with a source report |
| role wiring | growth planner, queue adapter, dynamic role outcomes | historian/editor/curator/librarian/biographer/flagger/redactor work can be planned as state-machine jobs |
| wiki apply/render | proposal indexes, curator/librarian validators, reader receipts | wiki output can be built and validated from role artifacts without shell-era assumptions |
| month orchestrator | fixed 4-hour block plan, concurrency, retries, invariant report | a dry or live month run can move from source windows through wiki build evidence |
