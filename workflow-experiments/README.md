# Python workflow experiments

This is the independent state-machine/control-fabric development snapshot accompanying the native 1Context source edition. It demonstrates durable queueing, transition guards, runtime execution, evidence receipts, replay, and session-import retention.

## Verified subset

```sh
uv run pytest -q tests/test_state_machine_runtime.py tests/test_state_machine_runtime_executor.py tests/test_state_machine_mermaid.py tests/test_memory_replay.py tests/test_session_import_retention.py
```

All 30 selected tests passed on October 6, 2026. The broader suite produced 144 passes and 17 failures: higher-level wiki apps, prompts, migrations, and renderer integrations are not complete in this snapshot. These missing integrations are not required for the selected core tests. Do not treat this subtree as the native app's deployed runtime.

See [the platform overview](../README.md) for architecture and component entry points. No accounts, memory database, populated wiki, or runtime ledgers are included.
