# e08 prompt parity report

## e08 sources read

- `experiments/e08-for-you/lab-notes/0036-experiment-closed.md` for the late handoff, skip-as-first-class, talk-folder/Maildir shape, voice register, and reader-loop direction.
- `experiments/e08-for-you/lab-notes/0037-unclosed-proposals-index.md` for index-driven proposal discovery via `.unclosed-proposals.json`.
- `experiments/e08-for-you/lab-notes/0034-quality-audit.md` for librarian frontmatter, Your Context initial-fill markers, quiet biography weeks, dated current-state staleness, and open-question closure.
- `experiments/e08-for-you/lab-notes/0012-librarian.md`, `0013-forgetting.md`, `0015-biographer.md`, `0016-redaction.md`, `0017-curator-generators.md`, `0020-ycx-bracket-discipline.md`, and `0021-reader-loop.md` for the mature curator/librarian/biographer/redactor behavior.
- `experiments/e08-for-you/lab-notes/0028-silent-no-op-anti-pattern.md`, `0029-runtime-invariants-pattern.md`, `0030-live-vs-replay-distinction.md`, and `0035-migration-pattern.md` for typed outcomes, no-op handling, live/replay boundaries, and production backfill constraints.
- Late e08 prompt files, especially `prompts/hourly.md`, `prompts/editor.md`, `prompts/agent-profile.md`, `prompts/librarian.md`, `prompts/for-you-curator.md`, `prompts/context-curator.md`, `prompts/biographer.md`, `prompts/redactor.md`, `prompts/contradiction-flagger.md`, and the talk-convention prompts.

## Changes made

- Restored the rich late-e08 role texture for hourly scribe and daily editor instead of keeping the shortened plugin versions: role identity, voice, output contract, isolation, references, skip behavior, and talk-page proposal semantics are back.
- Adapted only the source-acquisition/tool-loop posture for portable base-memory: hourly and block scribes now treat birth-loaded lived-experience packets as primary input, with default user+assistant messages only and tool calls/results omitted. Raw events/tool traces are retry or explicit-escalation inputs, not the default path.
- Added explicit anti-truncation guidance: no crude head/middle/tail sampling as a default. Oversized hours should split into block/shard/retry routes or return a wider-window/tool-trace request.
- Added 256k route-budget expectations to hourly, block, and shard scribe job manifests where prompt-visible routing policy matters.
- Made skip, forget, defer, no-change, needs-retry, and needs-wider-context successful typed outcomes in the relevant job manifests.
- Updated hourly answerer, historian, librarian, curator, and talk-convention prompts for the late e08 rules:
  - answerers use loaded hour packets first and raw traces only as escalation;
  - historian concept candidates go through For You talk proposals rather than direct Projects/Topics edits;
  - librarian reads routed proposals or `.unclosed-proposals.json` first and does not walk all talk folders at agent-fire time;
  - Your Context has thirteen sections, consumes initial-fill markers, and treats Life story as longer-cadence;
  - Projects and Topics are generated index pages from concept frontmatter, not standing curator-owned pages.
- Added `account_clean` / subscription-compatible runtime expectations to agent manifests that lacked them.
- Ported e08 lab path examples to memory-tree or web-base placeholders where leaving the old experiment path would violate account-clean portability.

## Intentional gaps and deviations

- I did not edit runtime execution code, wiki render/apply code, or generated wiki pages. This pass is prompt/job/agent parity only.
- I did not add a new proposal-index backfill or generator. The prompts and manifests now expect the indexed/routed proposal behavior, but runtime creation and maintenance of `.unclosed-proposals.json` belongs to the orchestration/runtime workers.
- I preserved e08's long-form prompt prose and model-author examples where they are part of the talk-page convention texture. The deliberate deviations are limited to portable source loading, account-clean paths, and product decisions that supersede lab-era implementation details.
- I did not create new tests because existing manifest/prompt loading coverage is sufficient for this change set.
