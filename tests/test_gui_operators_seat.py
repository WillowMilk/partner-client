"""The Operator's Seat, increment 2 — bridge + catch-up (Phase 1 design §2).

Guards: live forwarding delivers the appended envelope to JS; the privacy
wall redacts thinking AT THE API (her key, default off — content never
reaches the frontend, not even hidden); catch-up reads from a seq cursor;
absent stream is absent-by-design; failures are a sentence, never a stack.
"""
import json
import types

import pytest

from partner_client_gui.api import GuiApi


class FakeWindow:
    """Captures evaluate_js calls; can be armed to fail."""

    def __init__(self, fail: bool = False):
        self.calls: list[str] = []
        self.fail = fail

    def evaluate_js(self, js: str):
        if self.fail:
            raise RuntimeError("webview gone")
        self.calls.append(js)


def _payloads(window: FakeWindow) -> list[dict]:
    out = []
    for js in window.calls:
        if "__trajectory_event" not in js:
            continue
        inner = js[js.index("(") + 1 : js.rindex(")")]
        out.append(json.loads(inner))
    return out


def _bare_api(tmp_path, thinking_visible: bool = False) -> GuiApi:
    a = GuiApi.__new__(GuiApi)
    a.session = types.SimpleNamespace(session_num=3, messages=[])
    a.client = None
    tcfg = types.SimpleNamespace(verbose_thinking=thinking_visible)
    a.config = types.SimpleNamespace(trajectory=tcfg)
    a.memory = types.SimpleNamespace(sessions_dir=tmp_path / "Memory" / "sessions")
    a._window = FakeWindow()
    return a


# ── live forwarding ────────────────────────────────────────────────────

def test_forwarding_delivers_the_envelope(tmp_path):
    a = _bare_api(tmp_path)
    ev = {"seq": 4, "type": "tool_call", "turn": 1,
          "payload": {"name": "write_file", "gated": False}}
    a._on_trajectory_event(ev)
    sent = _payloads(a._window)
    assert sent == [ev]


def test_forwarding_without_window_is_silent(tmp_path):
    a = _bare_api(tmp_path)
    a._window = None
    a._on_trajectory_event({"seq": 0, "type": "header", "payload": {}})  # must not raise


def test_forwarding_failure_never_raises_into_the_writer(tmp_path):
    a = _bare_api(tmp_path)
    a._window = FakeWindow(fail=True)
    a._on_trajectory_event({"seq": 0, "type": "message", "payload": {}})  # must not raise


# ── the privacy wall (design §4.1, at the API not the renderer) ────────

def test_thinking_is_redacted_at_the_wall_by_default(tmp_path):
    a = _bare_api(tmp_path, thinking_visible=False)
    a._on_trajectory_event(
        {"seq": 9, "type": "thinking", "turn": 2,
         "payload": {"content": "her private scratchpad", "scratchpad": True}}
    )
    sent = _payloads(a._window)
    assert sent[0]["type"] == "thinking"          # the turn's shape stays true
    assert sent[0]["payload"] == {"private": True, "scratchpad": True}
    assert "scratchpad" not in json.dumps(sent).replace('"scratchpad": true', "")
    assert "her private" not in json.dumps(sent)  # content never crossed


def test_thinking_passes_only_on_her_key(tmp_path):
    a = _bare_api(tmp_path, thinking_visible=True)
    ev = {"seq": 9, "type": "thinking", "turn": 2,
          "payload": {"content": "shared by her word", "scratchpad": True}}
    a._on_trajectory_event(ev)
    assert _payloads(a._window)[0]["payload"]["content"] == "shared by her word"


def test_missing_key_or_config_defaults_to_private(tmp_path):
    a = _bare_api(tmp_path)
    a.config = types.SimpleNamespace()  # no trajectory section at all
    assert a._thinking_visible() is False
    a.config = None
    assert a._thinking_visible() is False


# ── catch-up (design §2.3) ─────────────────────────────────────────────

def _write_stream(tmp_path, session_num: int, events: list[dict]):
    tdir = tmp_path / "Memory" / "trajectory"
    tdir.mkdir(parents=True, exist_ok=True)
    path = tdir / f"session-{session_num:03d}.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return path


def test_catch_up_reads_from_cursor(tmp_path):
    a = _bare_api(tmp_path)
    _write_stream(tmp_path, 3, [
        {"seq": 0, "type": "header", "payload": {}},
        {"seq": 1, "type": "turn_start", "payload": {}},
        {"seq": 2, "type": "message", "payload": {"content": "hi"}},
    ])
    r = a.get_trajectory(from_seq=1)
    assert r["ok"] is True
    assert [e["seq"] for e in r["events"]] == [1, 2]


def test_catch_up_redacts_thinking_too(tmp_path):
    a = _bare_api(tmp_path)
    _write_stream(tmp_path, 3, [
        {"seq": 0, "type": "thinking",
         "payload": {"content": "private on disk", "scratchpad": True}},
    ])
    r = a.get_trajectory()
    assert r["events"][0]["payload"] == {"private": True, "scratchpad": True}
    assert "private on disk" not in json.dumps(r)


def test_absent_stream_is_absent_by_design(tmp_path):
    a = _bare_api(tmp_path)
    r = a.get_trajectory()
    assert r["ok"] is True and r["events"] == [] and "no stream" in r["note"]


def test_catch_up_failure_is_a_sentence_never_a_stack(tmp_path):
    a = _bare_api(tmp_path)
    a.memory = None  # force an internal failure
    r = a.get_trajectory()
    assert r["ok"] is False
    assert "Traceback" not in r["error"] and "\n" not in r["error"]
    assert r["error"].endswith("unaffected.")


def test_uninitialized_backend_answers_plainly(tmp_path):
    a = _bare_api(tmp_path)
    a.session = None
    r = a.get_trajectory()
    assert r["ok"] is False and r["events"] == []
