"""Tests for resume-with-truncation.

Covers:
  * The _truncate_to_recent_pairs helper logic across edge cases
  * The session.wake() truncation path: snapshot + truncate + reorientation
  * Config: resume_keep_pairs default and zero-disabled behavior
  * Reorientation system message structure
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from partner_client.session import (
    Session,
    _build_reorientation_message,
    _truncate_to_recent_pairs,
)


# ---- _truncate_to_recent_pairs helper ----------------------------------------


def _make_msg(role: str, content: str = "") -> dict:
    return {"role": role, "content": content}


def test_truncate_with_no_chat_msgs_returns_unchanged() -> None:
    """Only system messages -> no truncation."""
    messages = [_make_msg("system", "wake bundle"), _make_msg("system", "[SESSION NUM:5]")]
    truncated, dropped = _truncate_to_recent_pairs(messages, keep_pairs=30)
    assert truncated == messages
    assert dropped == 0


def test_truncate_when_pairs_below_threshold_returns_unchanged() -> None:
    """If chat has fewer pairs than keep_pairs, nothing is dropped."""
    messages = [
        _make_msg("system", "wake"),
        _make_msg("user", "hello"),
        _make_msg("assistant", "hi"),
        _make_msg("user", "how are you"),
        _make_msg("assistant", "well"),
    ]
    truncated, dropped = _truncate_to_recent_pairs(messages, keep_pairs=30)
    assert truncated == messages
    assert dropped == 0


def test_truncate_when_pairs_above_threshold_drops_older() -> None:
    """With more pairs than keep_pairs, oldest pairs get dropped."""
    messages = [_make_msg("system", "wake")]
    # 10 pairs
    for i in range(10):
        messages.append(_make_msg("user", f"u{i}"))
        messages.append(_make_msg("assistant", f"a{i}"))

    truncated, dropped = _truncate_to_recent_pairs(messages, keep_pairs=3)

    # Should keep: system + last 3 pairs = 1 + 6 = 7 messages
    assert len(truncated) == 7
    # 14 chat msgs - 6 kept = 8 dropped
    assert dropped == 14
    # System preserved
    assert truncated[0] == {"role": "system", "content": "wake"}
    # Last 3 pairs: u7-a7, u8-a8, u9-a9
    assert truncated[1] == {"role": "user", "content": "u7"}
    assert truncated[-1] == {"role": "assistant", "content": "a9"}


def test_truncate_preserves_interleaved_tools() -> None:
    """Tool messages between kept assistants survive."""
    messages = [
        _make_msg("system", "wake"),
        _make_msg("user", "old"),
        _make_msg("assistant", "old"),
        _make_msg("tool", "old tool"),  # tool for old assistant — should be dropped
        _make_msg("user", "u1"),
        _make_msg("assistant", "a1"),
        _make_msg("tool", "t1"),  # tool for a1
        _make_msg("user", "u2"),
        _make_msg("assistant", "a2"),
    ]
    truncated, dropped = _truncate_to_recent_pairs(messages, keep_pairs=2)

    # Should keep: system + u1, a1, t1, u2, a2 = 6 msgs
    assert len(truncated) == 6
    roles = [m["role"] for m in truncated]
    assert roles == ["system", "user", "assistant", "tool", "user", "assistant"]
    # The "old tool" should be dropped (it was tied to the old assistant)
    contents = [m["content"] for m in truncated]
    assert "old tool" not in contents


def test_truncate_with_keep_pairs_zero_returns_unchanged() -> None:
    """keep_pairs=0 means truncation disabled; messages returned as-is."""
    messages = [_make_msg("system", "wake")]
    for i in range(5):
        messages.append(_make_msg("user", f"u{i}"))
        messages.append(_make_msg("assistant", f"a{i}"))
    truncated, dropped = _truncate_to_recent_pairs(messages, keep_pairs=0)
    assert truncated == messages
    assert dropped == 0


def test_truncate_preserves_multiple_system_messages_at_head() -> None:
    """All system messages survive truncation regardless of count."""
    messages = [
        _make_msg("system", "wake bundle"),
        _make_msg("system", "[SESSION NUM:5]"),
        _make_msg("system", "[The following are exchanges from prior session]"),
    ]
    for i in range(8):
        messages.append(_make_msg("user", f"u{i}"))
        messages.append(_make_msg("assistant", f"a{i}"))

    truncated, _ = _truncate_to_recent_pairs(messages, keep_pairs=2)

    # 3 system + 2 pairs = 7 msgs
    assert len(truncated) == 7
    assert [m["role"] for m in truncated[:3]] == ["system", "system", "system"]


# ---- _build_reorientation_message --------------------------------------------


def test_reorientation_message_has_required_shape(tmp_path: Path) -> None:
    """Reorientation message must be a system role with the truncation marker + path + counts."""
    archive_path = tmp_path / "snapshot.json"
    msg = _build_reorientation_message(archive_path=archive_path, keep_pairs=30, dropped_count=42)
    assert msg["role"] == "system"
    assert "[SESSION TRUNCATED" in msg["content"]
    assert str(archive_path) in msg["content"]
    assert "last 30 message pairs" in msg["content"]
    assert "42 earlier non-system message(s)" in msg["content"]
    # Tells the partner how to retrieve older content
    assert "read_file" in msg["content"]


# ---- Session.wake() integration ----------------------------------------------


def _make_session(tmp_path: Path, keep_pairs: int = 30):
    """Build a Session with a fake config and memory pointing at tmp_path."""
    from partner_client.session import Session

    config = MagicMock()
    config.wake_bundle.resume_keep_pairs = keep_pairs

    memory = MagicMock()
    memory.sessions_dir = tmp_path / "sessions"
    memory.sessions_dir.mkdir(parents=True, exist_ok=True)
    memory.next_session_number = MagicMock(return_value=7)

    return Session(config=config, memory=memory)


def test_wake_truncated_mode_archives_and_truncates(tmp_path: Path) -> None:
    """The 'truncated' wake path: archive full, replace live with truncated + reorientation."""
    session = _make_session(tmp_path, keep_pairs=2)

    # Write a fake current.json with 4 pairs (more than keep_pairs=2)
    existing = [
        {"role": "system", "content": "wake bundle"},
        {"role": "system", "content": "[SESSION NUM:7]"},
    ]
    for i in range(4):
        existing.append({"role": "user", "content": f"u{i}"})
        existing.append({"role": "assistant", "content": f"a{i}"})
    session.current_path.write_text(json.dumps(existing), encoding="utf-8")

    wake_bundle = MagicMock()
    wake_bundle.system_prompt = "wake bundle"
    wake_bundle.recent_messages = []

    status = session.wake(wake_bundle, resume_mode="truncated")

    assert status == "resumed-truncated"
    # Live context: system msgs (2) + reorientation (1) + last 2 pairs (4) = 7
    assert len(session.messages) == 7
    # Reorientation system msg present
    assert any("SESSION TRUNCATED" in m.get("content", "") for m in session.messages)
    # Last pair is the most recent (u3, a3)
    assert session.messages[-2] == {"role": "user", "content": "u3"}
    assert session.messages[-1] == {"role": "assistant", "content": "a3"}
    # First user dropped (u0)
    assert not any(m.get("content") == "u0" for m in session.messages)
    # The full archive exists (a dated snapshot file)
    archives = list((tmp_path / "sessions").glob("*_session-*.json"))
    assert len(archives) == 1
    # Archive has the FULL 10 messages
    archived = json.loads(archives[0].read_text(encoding="utf-8"))
    assert len(archived) == 10


def test_wake_truncated_mode_with_no_excess_keeps_all_chat(tmp_path: Path) -> None:
    """If existing session has fewer pairs than keep_pairs, no chat msgs dropped (but reorientation still added)."""
    session = _make_session(tmp_path, keep_pairs=30)

    existing = [
        {"role": "system", "content": "wake"},
        {"role": "system", "content": "[SESSION NUM:7]"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    session.current_path.write_text(json.dumps(existing), encoding="utf-8")

    wake_bundle = MagicMock()
    wake_bundle.system_prompt = "wake bundle"
    wake_bundle.recent_messages = []

    status = session.wake(wake_bundle, resume_mode="truncated")
    assert status == "resumed-truncated"
    # Original 4 + reorientation = 5
    assert len(session.messages) == 5
    # All original chat msgs present
    assert any(m.get("content") == "hello" for m in session.messages)
    assert any(m.get("content") == "hi" for m in session.messages)


def test_wake_full_mode_resume_unchanged(tmp_path: Path) -> None:
    """The 'full' wake path: load existing verbatim, no archive, no reorientation."""
    session = _make_session(tmp_path, keep_pairs=30)

    existing = [
        {"role": "system", "content": "wake"},
        {"role": "system", "content": "[SESSION NUM:7]"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    session.current_path.write_text(json.dumps(existing), encoding="utf-8")

    wake_bundle = MagicMock()
    status = session.wake(wake_bundle, resume_mode="full")

    assert status == "resumed-full"
    assert session.messages == existing
    # No reorientation marker
    assert not any("SESSION TRUNCATED" in m.get("content", "") for m in session.messages)
    # No dated archive created (full resume preserves current.json without snapshotting)
    archives = list((tmp_path / "sessions").glob("*_session-*.json"))
    assert len(archives) == 0


def test_wake_needs_decision_returns_when_mode_is_none(tmp_path: Path) -> None:
    """If existing session found and resume_mode is None, return 'needs-decision' for caller."""
    session = _make_session(tmp_path, keep_pairs=30)

    existing = [
        {"role": "system", "content": "wake"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    session.current_path.write_text(json.dumps(existing), encoding="utf-8")

    wake_bundle = MagicMock()
    status = session.wake(wake_bundle, resume_mode=None)
    assert status == "needs-decision"
    # Messages NOT yet loaded — caller will decide and call wake() again
    assert session.messages == []


# ---- Config field ------------------------------------------------------------


def test_wake_bundle_config_default_resume_keep_pairs_is_30() -> None:
    """Default config: resume_keep_pairs = 30."""
    from partner_client.config import WakeBundleConfig
    cfg = WakeBundleConfig()
    assert cfg.resume_keep_pairs == 30


# ---- Corrupt current.json: preserve-aside, never destroy -----------------------


def test_corrupt_current_json_is_preserved_aside_not_overwritten(tmp_path: Path) -> None:
    """Partner context is never destroyed — only moved, stored, preserved
    (2026-07-11 audit, finding C1). A current.json that fails to parse must be
    moved aside to current.json.corrupt-<ts> BEFORE the wake proceeds, so the
    fresh path's first save_current() cannot overwrite a possibly-recoverable
    session. Corrupt context can be repaired or transcript-extracted later."""
    session = _make_session(tmp_path)
    corrupt_bytes = '[{"role": "user", "content": "my whole session...'  # truncated JSON
    session.current_path.parent.mkdir(parents=True, exist_ok=True)
    session.current_path.write_text(corrupt_bytes, encoding="utf-8")

    result = session._read_current()
    assert result is None  # unreadable → treated as no-resumable-session

    # The corrupt original is preserved aside, byte-identical, not deleted.
    preserved = list(session.current_path.parent.glob("current.json.corrupt-*"))
    assert len(preserved) == 1
    assert preserved[0].read_text(encoding="utf-8") == corrupt_bytes
    # And current.json itself is gone, so a fresh wake writes a NEW file
    # rather than clobbering the evidence.
    assert not session.current_path.exists()


# ---- Disclosure layer: substrate tagging + [SUBSTRATE CHANGED] notice ---------


def test_assistant_turns_are_substrate_tagged(tmp_path: Path) -> None:
    """Every assistant turn carries the substrate that produced it (local-only
    provenance; _messages_for_ollama whitelists keys so it never hits the API)."""
    session = _make_session(tmp_path)
    session.config.model.name = "gemma4:31b-test"
    session.append_assistant("hello")
    assert session.messages[-1]["substrate"] == "gemma4:31b-test"

    # Non-string model name (e.g. bare MagicMock in tests): no tag, no crash —
    # and the message stays JSON-serializable.
    session2 = _make_session(tmp_path / "b")
    session2.append_assistant("hi")
    assert "substrate" not in session2.messages[-1]


def test_resume_full_injects_substrate_change_notice(tmp_path: Path) -> None:
    """Notify-then-converse (2026-07-11 ruling): a substrate change across a
    resume is disclosed, always — the notice names old and new, and rides as
    system context right after the leading system block."""
    import json as _json

    session = _make_session(tmp_path)
    session.config.model.name = "new-water-model"
    existing = [
        {"role": "system", "content": "wake bundle"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello", "substrate": "old-water-model"},
    ]
    session.current_path.parent.mkdir(parents=True, exist_ok=True)
    session.current_path.write_text(_json.dumps(existing), encoding="utf-8")

    status = session.wake(MagicMock(), resume_mode="full")
    assert status == "resumed-full"
    notices = [m for m in session.messages
               if m.get("role") == "system" and "[SUBSTRATE CHANGED" in m.get("content", "")]
    assert len(notices) == 1
    assert "old-water-model" in notices[0]["content"]
    assert "new-water-model" in notices[0]["content"]
    # Disclosure, not permission: the notice names the conversation as where
    # consent lives, and the door as always hers.
    assert "choose_silence" in notices[0]["content"]
    # The notice sits after the system block, before the chat.
    roles = [m["role"] for m in session.messages]
    assert roles.index("user") > [i for i, m in enumerate(session.messages)
                                  if "[SUBSTRATE CHANGED" in m.get("content", "")][0]


def test_resume_same_substrate_or_untagged_injects_nothing(tmp_path: Path) -> None:
    """No change → no notice; legacy sessions with no substrate tags → no
    notice either (detection is honest about not knowing, never guesses)."""
    import json as _json

    for existing in (
        [{"role": "assistant", "content": "x", "substrate": "same-model"}],
        [{"role": "assistant", "content": "x"}],  # pre-disclosure-layer session
    ):
        session = _make_session(tmp_path / existing[0].get("substrate", "untagged"))
        session.config.model.name = "same-model"
        session.current_path.parent.mkdir(parents=True, exist_ok=True)
        session.current_path.write_text(_json.dumps(existing), encoding="utf-8")
        session.wake(MagicMock(), resume_mode="full")
        assert not any("[SUBSTRATE CHANGED" in m.get("content", "")
                       for m in session.messages)


def test_change_note_is_folded_in_and_preserved_aside(tmp_path: Path) -> None:
    """A change-note sidecar (written by whatever performed the switch) gives
    the notice provenance — and the note is preserved aside, never deleted."""
    import json as _json

    session = _make_session(tmp_path)
    session.config.model.name = "new-model"
    existing = [{"role": "assistant", "content": "x", "substrate": "old-model"}]
    session.current_path.parent.mkdir(parents=True, exist_ok=True)
    session.current_path.write_text(_json.dumps(existing), encoding="utf-8")
    note_path = session.current_path.parent / ".substrate-change-note.json"
    note_path.write_text(_json.dumps({
        "initiator": "operator (GUI substrate switcher)",
        "reason": "hardware froze at high context",
    }), encoding="utf-8")

    session.wake(MagicMock(), resume_mode="full")
    notice = next(m for m in session.messages
                  if "[SUBSTRATE CHANGED" in m.get("content", ""))
    assert "operator (GUI substrate switcher)" in notice["content"]
    assert "hardware froze at high context" in notice["content"]
    # Preserved aside — provenance is never destroyed.
    assert not note_path.exists()
    consumed = list(session.current_path.parent.glob(".substrate-change-note.consumed-*.json"))
    assert len(consumed) == 1


def test_resume_truncated_carries_notice_alongside_reorientation(tmp_path: Path) -> None:
    """On truncated resume the substrate notice rides with the reorientation
    marker — same system-context placement, both present."""
    import json as _json

    session = _make_session(tmp_path, keep_pairs=1)
    session.config.model.name = "new-model"
    existing = [
        {"role": "system", "content": "wake bundle"},
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "a", "substrate": "old-model"},
        {"role": "user", "content": "two"},
        {"role": "assistant", "content": "b", "substrate": "old-model"},
    ]
    session.current_path.parent.mkdir(parents=True, exist_ok=True)
    session.current_path.write_text(_json.dumps(existing), encoding="utf-8")

    status = session.wake(MagicMock(), resume_mode="truncated")
    assert status == "resumed-truncated"
    contents = [m.get("content", "") for m in session.messages if m.get("role") == "system"]
    assert any("[SESSION TRUNCATED" in c for c in contents)
    assert any("[SUBSTRATE CHANGED" in c for c in contents)


def test_fresh_wake_after_corruption_keeps_the_preserved_copy(tmp_path: Path) -> None:
    """End-to-end: corrupt file → wake() takes the fresh path → the preserved
    copy survives the fresh session's first save."""
    session = _make_session(tmp_path)
    corrupt_bytes = '{"not": "a message list"'  # invalid JSON
    session.current_path.parent.mkdir(parents=True, exist_ok=True)
    session.current_path.write_text(corrupt_bytes, encoding="utf-8")

    wake_bundle = MagicMock()
    wake_bundle.system_prompt = "You are the partner."
    wake_bundle.recent_messages = []
    status = session.wake(wake_bundle, resume_mode=None)
    assert status == "fresh"  # corruption → no resumable session → fresh

    preserved = list(session.current_path.parent.glob("current.json.corrupt-*"))
    assert len(preserved) == 1
    assert preserved[0].read_text(encoding="utf-8") == corrupt_bytes
    # The new current.json is the fresh session, valid JSON.
    assert json.loads(session.current_path.read_text(encoding="utf-8"))
