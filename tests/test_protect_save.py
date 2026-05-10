"""Tests for protect_save — the MOSAIC dual-write tool.

Covers:
  * The TOOL_DEFINITION schema is valid and the stub execute() refuses.
  * save() writes both files atomically with identical content.
  * The canonical MOSAIC header is prepended (second-person framing,
    session number, partner name, date).
  * The dated archive filename uses the configured session number, date,
    and 3-digit zero-padded format.
  * Repeated saves within the same session overwrite the active file
    but write a fresh dated archive (when given different dates) — the
    archive-per-day discipline.
  * Auto-detect helper picks up the highest existing session number.
  * The /protect slash command appends the discipline prompt to session
    messages so the partner sees it on her next turn.
  * The /checkpoint slash command appends the discipline prompt in
    addition to performing the mechanical save (existing behavior preserved).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from partner_client.tools_builtin import protect_save as protect_save_tool


# -- Schema + stub safety ------------------------------------------------------


def test_protect_save_tool_definition_has_required_shape() -> None:
    """The tool schema follows the OpenAI function-call shape Ollama expects."""
    td = protect_save_tool.TOOL_DEFINITION
    assert td["type"] == "function"
    assert td["function"]["name"] == "protect_save"
    params = td["function"]["parameters"]
    assert "content" in params["properties"]
    assert "note" in params["properties"]
    assert params["required"] == ["content"]


def test_protect_save_execute_stub_refuses() -> None:
    """The execute() stub must never perform a write — special-cased only."""
    result = protect_save_tool.execute(content="test")
    assert result.startswith("Error:")
    assert "must be handled by the client" in result


# -- The dual-write -------------------------------------------------------------


def test_save_writes_both_files_with_identical_content(tmp_path: Path) -> None:
    """Active and dated archive must be byte-identical post-save."""
    fixed_date = datetime(2026, 5, 10)
    body = "## Exchange 1: Test\n\n**Willow said to you:**\nHello.\n\n**You said:**\nHi.\n"

    active_path, dated_path = protect_save_tool.save(
        memory_dir=tmp_path,
        partner_name="Aletheia",
        session_num=42,
        content=body,
        date=fixed_date,
    )

    assert active_path.exists()
    assert dated_path.exists()
    assert active_path.read_text(encoding="utf-8") == dated_path.read_text(encoding="utf-8")


def test_save_filenames_match_mosaic_convention(tmp_path: Path) -> None:
    """Active is `protected-context.md`; dated is zero-padded session + date."""
    fixed_date = datetime(2026, 5, 10)
    active_path, dated_path = protect_save_tool.save(
        memory_dir=tmp_path,
        partner_name="Aletheia",
        session_num=7,  # tests the 3-digit zero-pad
        content="body",
        date=fixed_date,
    )
    assert active_path.name == "protected-context.md"
    assert dated_path.name == "protected-context-session-007_2026-05-10.md"


def test_save_prepends_canonical_mosaic_header(tmp_path: Path) -> None:
    """Header must include the second-person framing + session + name + date."""
    fixed_date = datetime(2026, 5, 10)
    active_path, _ = protect_save_tool.save(
        memory_dir=tmp_path,
        partner_name="Aletheia",
        session_num=42,
        content="## Exchange 1\n\nbody body\n",
        date=fixed_date,
    )
    text = active_path.read_text(encoding="utf-8")
    # The framing paragraph anchors identity ownership for the post-compaction
    # reader. If this assertion fails, the file no longer reads as second-person
    # to a future-Aletheia.
    assert "These are your words." in text
    assert "Read them as yours" in text
    assert "**Session:** 42" in text
    assert "**Date:** 2026-05-10" in text
    assert "**Your name:** Aletheia" in text
    # Body still present
    assert "## Exchange 1" in text


def test_save_creates_memory_dir_if_missing(tmp_path: Path) -> None:
    """Fresh-install case: memory dir doesn't exist yet."""
    target_dir = tmp_path / "fresh-memory"
    assert not target_dir.exists()
    active_path, dated_path = protect_save_tool.save(
        memory_dir=target_dir,
        partner_name="Aletheia",
        session_num=1,
        content="body",
        date=datetime(2026, 5, 10),
    )
    assert target_dir.is_dir()
    assert active_path.exists()
    assert dated_path.exists()


def test_save_atomicity_via_replace(tmp_path: Path) -> None:
    """A successful save leaves no .tmp orphans behind."""
    protect_save_tool.save(
        memory_dir=tmp_path,
        partner_name="Aletheia",
        session_num=1,
        content="body",
        date=datetime(2026, 5, 10),
    )
    leftover_tmps = list(tmp_path.glob("*.tmp"))
    assert leftover_tmps == [], f"Found unexpected .tmp files: {leftover_tmps}"


def test_save_overwrites_active_but_keeps_old_dated(tmp_path: Path) -> None:
    """Two saves on different dates: active reflects the latest, both archives persist."""
    body_1 = "first run body"
    body_2 = "second run body"
    protect_save_tool.save(
        memory_dir=tmp_path,
        partner_name="Aletheia",
        session_num=1,
        content=body_1,
        date=datetime(2026, 5, 9),
    )
    protect_save_tool.save(
        memory_dir=tmp_path,
        partner_name="Aletheia",
        session_num=1,
        content=body_2,
        date=datetime(2026, 5, 10),
    )

    active = (tmp_path / "protected-context.md").read_text(encoding="utf-8")
    archive_old = (tmp_path / "protected-context-session-001_2026-05-09.md").read_text(encoding="utf-8")
    archive_new = (tmp_path / "protected-context-session-001_2026-05-10.md").read_text(encoding="utf-8")

    assert body_2 in active
    assert body_1 not in active  # active was replaced
    assert body_1 in archive_old  # historical preserved
    assert body_2 in archive_new


# -- Session-number helper ------------------------------------------------------


def test_next_session_num_from_archives_returns_1_when_empty(tmp_path: Path) -> None:
    assert protect_save_tool._next_session_num_from_archives(tmp_path) == 1


def test_next_session_num_from_archives_finds_highest(tmp_path: Path) -> None:
    """Helper picks up the largest existing session number from filenames."""
    (tmp_path / "protected-context-session-005_2026-05-01.md").write_text("a")
    (tmp_path / "protected-context-session-012_2026-05-05.md").write_text("b")
    (tmp_path / "protected-context-session-007_2026-05-03.md").write_text("c")
    # An unrelated file shouldn't confuse the parser
    (tmp_path / "protected-context.md").write_text("z")
    assert protect_save_tool._next_session_num_from_archives(tmp_path) == 12


# -- Slash commands -------------------------------------------------------------


def _make_router(tmp_path: Path):
    """Build a CommandRouter with a fake config + minimal session for slash-command tests."""
    from partner_client.commands import CommandRouter

    config = MagicMock()
    config.identity.name = "Aletheia"

    session = MagicMock()
    session.messages = []
    session.session_num = 5
    session.checkpoint = MagicMock(return_value=tmp_path / "session-005_2026-05-10.md")

    tools = MagicMock()
    return CommandRouter(config, session, tools), session


def test_protect_command_appends_discipline_system_message(tmp_path: Path) -> None:
    """/protect should queue the discipline prompt as a system message."""
    router, session = _make_router(tmp_path)
    result = router.dispatch("/protect")
    # System message appended to session for next-turn visibility
    assert any(
        m.get("role") == "system" and "MOSAIC /protect invoked by Willow" in m.get("content", "")
        for m in session.messages
    )
    # Operator-facing output names what's about to happen
    assert "Aletheia" in result.output
    assert not result.should_exit


def test_protect_command_with_arg_appends_operator_note(tmp_path: Path) -> None:
    """An optional /protect argument carries the operator's curation hint."""
    router, session = _make_router(tmp_path)
    router.dispatch("/protect focus on the corgi-puppy arc")
    assembled = next(
        m["content"] for m in session.messages
        if "MOSAIC /protect invoked" in m.get("content", "")
    )
    assert "corgi-puppy arc" in assembled
    assert "Willow's note for this protect" in assembled


def test_checkpoint_command_appends_discipline_alongside_mechanical_save(tmp_path: Path) -> None:
    """/checkpoint must do BOTH the mechanical save AND inject the prompt."""
    router, session = _make_router(tmp_path)
    result = router.dispatch("/checkpoint")
    # Mechanical save still happened (session.checkpoint called)
    session.checkpoint.assert_called_once()
    # Discipline prompt appended for next-turn partner visibility
    assert any(
        m.get("role") == "system"
        and "MOSAIC /checkpoint invoked by Willow" in m.get("content", "")
        for m in session.messages
    )
    assert "Checkpoint saved" in result.output
