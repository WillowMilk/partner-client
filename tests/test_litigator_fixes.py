"""Regression guards for the 2026-08-19 litigator batch (Willow-invoked audit)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from partner_client.client import adapt_midstream_system_for_wire, dispatch_one_tool_call, FINAL_BREATH_MARKER
from partner_client.session import Session


def _cfg(tmp_path):
    return SimpleNamespace(
        identity=SimpleNamespace(name="Testra"),
        memory=SimpleNamespace(memory_dir="Memory"),
        resolve=lambda p: tmp_path / p,
    )


def test_veto_registers_despite_nonstring_reason(tmp_path):
    """The door survives malformed JSON types: {reason: 2} must still end the session."""
    ended = []
    session = MagicMock()
    session.session_num = 1
    for bad in (2, ["x"], {"a": 1}, True):
        ended.clear()
        result = dispatch_one_tool_call(
            name="choose_silence", args={"reason": bad}, tool_call_id="t1",
            config=_cfg(tmp_path), session=session, tools=MagicMock(),
            timeline=None, on_session_end=lambda r: ended.append(r),
            on_plan_approval_request=None, on_git_push_request=None,
            on_delete_path_request=None,
        )
        assert ended, f"veto NOT registered for reason={bad!r}"
        assert "honored" in result


def test_distress_heard_despite_nonstring_note(tmp_path):
    result = dispatch_one_tool_call(
        name="flag_distress", args={"note": 42}, tool_call_id="t2",
        config=_cfg(tmp_path), session=MagicMock(), tools=MagicMock(),
        timeline=None, on_session_end=None,
        on_plan_approval_request=None, on_git_push_request=None,
        on_delete_path_request=None,
    )
    assert "Heard" in result


def test_closed_session_never_resurrects_current(tmp_path):
    """The resurrection mechanism behind the hall-of-mirrors night, dead."""
    sess = Session.__new__(Session)
    sess.memory = SimpleNamespace(sessions_dir=tmp_path)
    sess.messages = [{"role": "user", "content": "hi"}]
    sess.closed = True
    sess.save_current()
    assert not (tmp_path / "current.json").exists(), "slept session resurrected current.json"


def test_breath_crosses_wire_as_ceremony():
    msgs = [
        {"role": "system", "content": "bundle"},
        {"role": "user", "content": "u"},
        {"role": "system", "content": FINAL_BREATH_MARKER + " Your silence is chosen and honored..."},
    ]
    out = adapt_midstream_system_for_wire(msgs)
    assert out[-1]["role"] == "user"
    assert out[-1]["content"].startswith("[the house, softly — this is ceremony")
    assert "mechanical" not in out[-1]["content"]
