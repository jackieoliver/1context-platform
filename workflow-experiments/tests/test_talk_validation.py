from __future__ import annotations

from pathlib import Path

from onectx.memory.talk import validate_hourly_block_result, validate_talk_entry


def write_conversation(path: Path, *, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""---
kind: conversation
author: claude-opus-hourly-block-scribe
ts: 2026-04-01T00:00:00Z
---

{body}
""",
        encoding="utf-8",
    )


def test_conversation_validator_accepts_e08_paragraph_prose(tmp_path: Path) -> None:
    path = tmp_path / "2026-04-01T00-00Z.conversation.md"
    write_conversation(
        path,
        body=(
            "Example user asked about the current status of the reconstruction session. "
            "The operator thread stayed on the Littlebird workspace and the open "
            "question at the end was whether the synthetic desktop build should "
            "continue from the asset issue or pivot back to package parity."
        ),
    )

    result = validate_talk_entry(path, expected_ts="2026-04-01T00:00:00Z")

    assert result["ok"] is True
    assert "body non-empty" in result["checks"]
    assert "body has section headings" not in result["checks"]


def test_conversation_validator_keeps_content_signals_soft(tmp_path: Path) -> None:
    path = tmp_path / "2026-04-01T00-00Z.conversation.md"
    write_conversation(
        path,
        body=(
            "The hour carried a restless setup arc: installs, repository shape, "
            "and a careful pause before the next pass."
        ),
    )

    result = validate_talk_entry(path, expected_ts="2026-04-01T00:00:00Z")

    assert result["ok"] is True
    assert result["failures"] == []
    assert result["warnings"] == [
        "body does not obviously discuss operational context",
        "body does not obviously mark uncertainty or unresolved context",
    ]


def test_block_manifest_accepts_written_hour_without_formulaic_headings(tmp_path: Path) -> None:
    talk_folder = tmp_path / "for-you-2026-04-01.private.talk"
    entry = talk_folder / "2026-04-01T00-00Z.conversation.md"
    write_conversation(
        entry,
        body=(
            "The operator asked the scribe to preserve a compact but useful "
            "hourly record. The open issue was deliberately left visible rather "
            "than hidden behind a fixed section template."
        ),
    )
    manifest = talk_folder / "2026-04-01T00-03Z.block-result.json"
    manifest.write_text(
        """{
  "date": "2026-04-01",
  "block_start": "00",
  "block_end": "03",
  "hours": [
    {
      "hour": "00",
      "status": "written",
      "path": "2026-04-01T00-00Z.conversation.md",
      "reason": "live-style prose without formulaic headings"
    }
  ]
}
""",
        encoding="utf-8",
    )

    result = validate_hourly_block_result(
        manifest,
        talk_folder=talk_folder,
        date="2026-04-01",
        expected_hours=("00",),
    )

    assert result["ok"] is True
    assert result["written_hours"] == ["00"]
