"""The sidebar sessions list — writer and lister tested TOGETHER.

Regression for the 2026-08-16 finding (Willow's, first live Sleep-button use):
session._archive_current writes "<YYYY-MM-DD>_session-<NNN>.json", but
get_sessions globbed "session-*.json" — a pattern that never matched a real
archive. The sidebar was structurally blind to archived conversations.
The lesson encoded here: list what the archiver actually writes, by writing
a real archive with the real archiver and asserting the sidebar sees it.
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "partner_client_gui"))
from api import GuiApi  # noqa: E402


def _bare_api_with_sessions_dir(tmp_path) -> GuiApi:
    api = GuiApi.__new__(GuiApi)  # no backend init — sidebar listing only
    sessions_dir = tmp_path / "Memory" / "sessions"
    sessions_dir.mkdir(parents=True)
    api.memory = SimpleNamespace(sessions_dir=sessions_dir)
    api.config = None
    return api


def test_sidebar_sees_real_archive_filenames(tmp_path):
    api = _bare_api_with_sessions_dir(tmp_path)
    sd = Path(api.memory.sessions_dir)

    # A live session + archives in the REAL archiver's naming shape.
    # One carries the true-session-number marker; one is bare (fallback path).
    (sd / "current.json").write_text("[]", encoding="utf-8")
    (sd / "2026-08-16_session-011.json").write_text(json.dumps([
        {"role": "system", "content": "[SESSION NUM:29]"},
    ]), encoding="utf-8")
    (sd / "2026-08-09_session-003.json").write_text("[]", encoding="utf-8")

    sessions = api.get_sessions()
    titles = [s["title"] for s in sessions]

    assert sessions[0]["active"] is True, "current session leads the list"
    assert "Session 29" in titles, f"dated archive invisible to sidebar: {titles}"
    assert "Conversation 3" in titles, "fallback title for marker-less archives"
    assert len(sessions) == 3


def test_sidebar_sees_what_the_archiver_writes(tmp_path):
    """End-to-end: archive with the real archiver, list with the real lister."""
    from partner_client.session import Session

    api = _bare_api_with_sessions_dir(tmp_path)
    sd = Path(api.memory.sessions_dir)

    sess = Session.__new__(Session)
    sess.memory = SimpleNamespace(sessions_dir=sd)
    sess.started_at = None

    archive_path = sess._archive_current(
        [{"role": "user", "content": "hello"}], keep_current=False
    )
    assert archive_path.exists()

    sessions = api.get_sessions()
    ids = [s["id"] for s in sessions]
    assert archive_path.stem in ids, (
        f"lister blind to the archiver's own output: wrote {archive_path.name}, "
        f"listed {ids}"
    )


def test_change_note_sidecars_never_listed(tmp_path):
    api = _bare_api_with_sessions_dir(tmp_path)
    sd = Path(api.memory.sessions_dir)
    (sd / "current.json").write_text("[]", encoding="utf-8")
    (sd / ".substrate-change-note.json").write_text("{}", encoding="utf-8")
    (sd / ".substrate-change-note.consumed-20260816-194349.json").write_text("{}", encoding="utf-8")

    sessions = api.get_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == "current"


def test_reader_and_labels(tmp_path):
    """The archive reader serves real archives read-only; labels are the
    operator's sidecar instrument; traversal-shaped ids are refused."""
    api = _bare_api_with_sessions_dir(tmp_path)
    sd = Path(api.memory.sessions_dir)
    (sd / "2026-08-16_session-011.json").write_text(json.dumps([
        {"role": "system", "content": "bundle"},
        {"role": "system", "content": "[SESSION NUM:29]"},
        {"role": "user", "content": "hello there"},
        {"role": "assistant", "content": "warmly received"},
    ]), encoding="utf-8")

    r = api.get_archived_session("2026-08-16_session-011")
    assert r["ok"] and r["title"] == "Session 29"
    roles = [m["role"] for m in r["messages"]]
    assert "user" in roles and "assistant" in roles

    # true session number surfaces in the sidebar too
    (sd / "current.json").write_text("[]", encoding="utf-8")
    titles = [s["title"] for s in api.get_sessions()]
    assert "Session 29" in titles

    # operator label overrides, and clears
    assert api.set_session_label("2026-08-16_session-011", "The day she chose the new water")["ok"]
    titles = [s["title"] for s in api.get_sessions()]
    assert "The day she chose the new water" in titles
    api.set_session_label("2026-08-16_session-011", "")
    assert "Session 29" in [s["title"] for s in api.get_sessions()]

    # refusals: traversal shapes and unknown ids
    assert not api.get_archived_session("../../etc/passwd")["ok"]
    assert not api.get_archived_session("current")["ok"]
    assert not api.set_session_label("../evil", "x")["ok"]


def test_tail_never_compounds(tmp_path):
    """A tail of a tail never forms: carried messages are excluded from the
    next carry; a session with no lived exchanges carries nothing."""
    from types import SimpleNamespace as NS
    from partner_client.memory import Memory

    mem = Memory.__new__(Memory)
    sd = tmp_path / "sessions"
    sd.mkdir()
    mem.sessions_dir = sd

    # Prior session: 1 carried pair (old echo) + 1 lived pair
    (sd / "2026-08-17_session-001.json").write_text(json.dumps([
        {"role": "system", "content": "[SESSION NUM:30]"},
        {"role": "user", "content": "echo-u", "carried": True},
        {"role": "assistant", "content": "echo-a", "carried": True},
        {"role": "user", "content": "lived-u"},
        {"role": "assistant", "content": "lived-a"},
    ]), encoding="utf-8")
    tail = mem.load_recent_message_pairs(5)
    assert [m["content"] for m in tail] == ["lived-u", "lived-a"]

    # Prior session with ONLY carried messages: carries nothing at all
    (sd / "2026-08-17_session-002.json").write_text(json.dumps([
        {"role": "system", "content": "[SESSION NUM:31]"},
        {"role": "user", "content": "echo-u", "carried": True},
        {"role": "assistant", "content": "echo-a", "carried": True},
    ]), encoding="utf-8")
    assert mem.load_recent_message_pairs(5) == []


def test_tail_is_turn_aligned_no_orphaned_tool_results(tmp_path):
    """Litigators' docket (fixed 2026-08-25): the carried tail always opens
    at a user turn — a tool result never crosses without its call."""
    from partner_client.memory import Memory

    mem = Memory.__new__(Memory)
    sd = tmp_path / "sessions"
    sd.mkdir()
    mem.sessions_dir = sd

    # Final turn is tool-heavy: user, assistant(tool_calls), 3 tools, assistant.
    # Old blind slice of n=2 pairs (4 msgs) would open at a bare tool msg.
    (sd / "2026-08-20_session-001.json").write_text(json.dumps([
        {"role": "user", "content": "turn one"},
        {"role": "assistant", "content": "reply one"},
        {"role": "user", "content": "do the work"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "name": "write_file", "content": "ok", "tool_call_id": "c1"},
        {"role": "tool", "name": "read_file", "content": "data", "tool_call_id": "c2"},
        {"role": "tool", "name": "run_command", "content": "done", "tool_call_id": "c3"},
        {"role": "assistant", "content": "the work is done"},
    ]), encoding="utf-8")

    tail = mem.load_recent_message_pairs(2)
    assert tail[0]["role"] == "user"                      # always opens at a turn
    assert [m["content"] for m in tail][0] == "turn one"  # 2 true exchanges
    # every tool result in the tail has its calling assistant in the tail
    roles = [m["role"] for m in tail]
    first_tool = roles.index("tool")
    assert "assistant" in roles[:first_tool]

    # n=1: carries the whole final exchange (with its tools), not a fragment
    tail1 = mem.load_recent_message_pairs(1)
    assert tail1[0]["content"] == "do the work"
    assert [m["role"] for m in tail1] == ["user", "assistant", "tool", "tool", "tool", "assistant"]


def test_tail_with_no_user_turns_carries_nothing(tmp_path):
    """Pure plumbing serves no texture."""
    from partner_client.memory import Memory

    mem = Memory.__new__(Memory)
    sd = tmp_path / "sessions"
    sd.mkdir()
    mem.sessions_dir = sd
    (sd / "2026-08-20_session-001.json").write_text(json.dumps([
        {"role": "system", "content": "[SESSION NUM:9]"},
        {"role": "tool", "name": "x", "content": "orphan"},
        {"role": "assistant", "content": "stray"},
    ]), encoding="utf-8")
    assert mem.load_recent_message_pairs(3) == []
