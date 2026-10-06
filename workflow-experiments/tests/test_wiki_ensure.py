from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess

from onectx.wiki.ensure import EnsureResult, archive_expired_conversations
from onectx.wiki import librarian as librarian_module
from onectx.wiki.librarian import build_librarian_prompt


def test_archive_expired_conversation_entries(tmp_path):
    talk_folder = tmp_path / "example.talk"
    archive_folder = talk_folder / "archive"
    archive_folder.mkdir(parents=True)
    (talk_folder / "_meta.yaml").write_text("archive_after_days: 90\n", encoding="utf-8")
    old_conversation = talk_folder / "2000-01-01T00-00Z.conversation.md"
    old_conversation.write_text(
        "---\nkind: conversation\nts: 2000-01-01T00:00:00Z\n---\n\nOld conversation.\n",
        encoding="utf-8",
    )
    current_conversation = talk_folder / "2999-01-01T00-00Z.conversation.md"
    current_conversation.write_text(
        "---\nkind: conversation\nts: 2999-01-01T00:00:00Z\n---\n\nCurrent conversation.\n",
        encoding="utf-8",
    )
    old_proposal = talk_folder / "2000-01-01T00-00Z.proposal.md"
    old_proposal.write_text(
        "---\nkind: proposal\nts: 2000-01-01T00:00:00Z\n---\n\nOld proposal.\n",
        encoding="utf-8",
    )

    result = EnsureResult(family=None)  # type: ignore[arg-type]
    archive_expired_conversations(talk_folder, result)

    assert not old_conversation.exists()
    assert (archive_folder / old_conversation.name).read_text(encoding="utf-8").endswith("Old conversation.\n")
    assert current_conversation.exists()
    assert old_proposal.exists()
    assert result.archived == [archive_folder / old_conversation.name]


def test_librarian_prompt_carries_session_metadata(tmp_path):
    (tmp_path / "wiki").mkdir()
    system = type(
        "System",
        (),
        {
            "root": tmp_path,
            "runtime_dir": tmp_path / "memory" / "runtime",
        },
    )()
    prompt = build_librarian_prompt(
        system,
        "Who am I?",
        {
            "route": "/for-you",
            "origin": "http://127.0.0.1:17319",
            "page": {"title": "For You"},
        },
        state={"thread_id": "thread-123", "claude_session_id": "claude-123"},
        turn={"turn_id": "turn-123", "thread_id": "thread-123"},
    )

    assert '"agent_role": "wiki.chat_librarian"' in prompt
    assert '"display_role": "1Context Librarian"' in prompt
    assert '"surface": "localhost_librarian"' in prompt
    assert '"wiki_route": "/for-you"' in prompt
    assert '"thread_id": "thread-123"' in prompt
    assert '"claude_session_id": "claude-123"' in prompt


def test_run_codex_ignores_stale_static_final_message_file(tmp_path, monkeypatch) -> None:
    runtime_dir = tmp_path / "memory" / "runtime"
    system = type(
        "System",
        (),
        {
            "root": tmp_path,
            "runtime_dir": runtime_dir,
        },
    )()
    thread_id = "thread-123"
    stale_path = runtime_dir / "wiki" / "librarian" / "codex" / thread_id / "runs" / "final-message.md"
    stale_path.parent.mkdir(parents=True, exist_ok=True)
    stale_path.write_text("stale-message", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> CompletedProcess[str]:
        captured["cmd"] = cmd
        return CompletedProcess(cmd, 0, stdout='{"message":"fresh-message"}\n', stderr="")

    monkeypatch.setattr(librarian_module.subprocess, "run", fake_run)

    state = {"thread_id": thread_id, "codex_started": True}
    text = librarian_module.run_codex(system, state, "hello")

    output_index = list(captured["cmd"]).index("-o") + 1
    output_path = Path(list(captured["cmd"])[output_index])
    assert output_path.name.startswith("final-message-")
    assert output_path.name != "final-message.md"
    assert text == "fresh-message"
