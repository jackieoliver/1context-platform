# E08 End-to-End Month Run

The production-shaped month spine is intentionally thin:

```text
run-for-you-month
  -> fixed 4-hour block scribes
  -> retry hours requested by block manifests
  -> daily editor + concept scout layer
  -> wiki route workspace view
  -> wiki route planning + route hired-agent births
  -> deterministic wiki render
  -> runtime invariant report + cycle validation
```

The tighter proof loop is day-scoped:

```text
run-for-you-day --date YYYY-MM-DD
  -> same fabric
  -> explicit date filter
  -> day-level phase statuses
```

In dry-run mode, a fresh workspace may report
`day_layer: waiting_for_hourly_outputs`. That is intentional. Planned scribe
hires do not invent prose artifacts when the harness is not launched, so dry-run
proves planning, prompt construction, route scope, wiki-cycle evidence, and
invariants. Live harness output or existing talk entries prove the prose chain.

The command is the same in dry-run and live modes. Dry-run omits
`--run-harness`, so hired agents are born with prompt/source artifacts but the
Claude harness is not launched. Live mode adds `--run-harness`; guarded source
promotion still requires `--promote-route-outputs --operator-approval
promote-source`.

```bash
uv run 1context job run-for-you-month \
  --month 2026-04 \
  --workspace /tmp/onecontext-for-you-2026-04 \
  --concept-dir /tmp/onecontext-for-you-2026-04/concept \
  --max-concurrent 8 \
  --json
```

The result payload is the state-machine proof surface. Read:

- `phases[]`: explicit phase status, payload, and wall-clock timing.
- `plan_summary.target`: month-in-about-an-hour target, fixed block hires,
  waves at max concurrency, and whether split escalation was explicitly
  enabled.
- `plan_summary.work`: active hours, prepared block/retry/day hires, route jobs,
  wiki render counts, manifests, and route table counts.
- `plan_summary.validation_failures`: block, retry, day-layer, route-hire, and
  invariant failure counts.
- `wiki.cycle.runtime_invariant_summary` and `wiki.validation`: durable cycle
  and invariant proof, not hidden chat state.

Default fast mode uses `braided_lived_messages`: all user and assistant
messages, no tool calls/results, and no head/middle/tail sampling. Tool detail
stays in lakestore and is rehydrated only by explicit retry/escalation modes.

The default block/month route budget is 256k estimated tokens. Automatic split
is not the default; use `--split-large-blocks` to opt into the explicit
escalation path.
