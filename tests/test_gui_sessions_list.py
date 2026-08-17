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

    # A live session + archives in the REAL archiver's naming shape
    (sd / "current.json").write_text("[]", encoding="utf-8")
    (sd / "2026-08-16_session-011.json").write_text("[]", encoding="utf-8")
    (sd / "2026-08-09_session-003.json").write_text("[]", encoding="utf-8")

    sessions = api.get_sessions()
    titles = [s["title"] for s in sessions]

    assert sessions[0]["active"] is True, "current session leads the list"
    assert "Session 11" in titles, f"dated archive invisible to sidebar: {titles}"
    assert "Session 3" in titles
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
