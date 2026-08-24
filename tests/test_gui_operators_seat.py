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


# ── the artifact chevron (increment 4, design §3.3) ────────────────────

def _api_with_stream(tmp_path, events):
    a = _bare_api(tmp_path)
    _write_stream(tmp_path, 3, events)
    return a


def test_chevron_current_and_as_written_differ(tmp_path):
    """write_file's recorded args feed bytes-as-written; differs is honest."""
    target = tmp_path / "journal.md"
    target.write_text("edited later by hand")
    args = json.dumps({"filename": str(target), "content": "as first written"})
    a = _api_with_stream(tmp_path, [
        {"seq": 5, "type": "tool_call",
         "payload": {"name": "write_file", "args": args}},
    ])
    r = a.get_artifact(str(target), call_seq=5)
    assert r["ok"] and r["current"] == "edited later by hand"
    assert r["as_written"] == "as first written"
    assert r["differs"] is True


def test_chevron_identical_bytes_do_not_cry_wolf(tmp_path):
    target = tmp_path / "note.md"
    target.write_text("same bytes")
    args = json.dumps({"filename": str(target), "content": "same bytes"})
    a = _api_with_stream(tmp_path, [
        {"seq": 2, "type": "tool_call",
         "payload": {"name": "write_file", "args": args}},
    ])
    r = a.get_artifact(str(target), call_seq=2)
    assert r["differs"] is False


def test_chevron_as_written_unavailable_is_none_never_guessed(tmp_path):
    """Non-write tools: differs stays None — honestly unknown, not False."""
    target = tmp_path / "read.md"
    target.write_text("content")
    a = _api_with_stream(tmp_path, [
        {"seq": 1, "type": "tool_call",
         "payload": {"name": "read_file", "args": json.dumps({"filename": str(target)})}},
    ])
    r = a.get_artifact(str(target), call_seq=1)
    assert r["ok"] and r["as_written"] is None and r["differs"] is None


def test_chevron_blob_args_recovered(tmp_path):
    """Large write_file args live in a blob; as-written comes back whole."""
    import hashlib
    target = tmp_path / "big.md"
    big_content = "x" * 10000
    target.write_text(big_content)
    args_raw = json.dumps({"filename": str(target), "content": big_content})
    sha = hashlib.sha256(args_raw.encode()).hexdigest()
    blob_dir = tmp_path / "Memory" / "trajectory" / "blobs" / sha[:2]
    blob_dir.mkdir(parents=True)
    (blob_dir / f"{sha}.txt").write_text(args_raw)
    a = _api_with_stream(tmp_path, [
        {"seq": 7, "type": "tool_call",
         "payload": {"name": "write_file",
                     "args": {"ref": f"sha256:{sha}", "bytes": len(args_raw),
                              "preview": args_raw[:200]}}},
    ])
    r = a.get_artifact(str(target), call_seq=7)
    assert r["as_written"] == big_content and r["differs"] is False


def test_chevron_missing_file_shows_as_written_from_the_record(tmp_path):
    """Deleted since the turn: the record still shows what was written."""
    gone = tmp_path / "gone.md"
    args = json.dumps({"filename": str(gone), "content": "preserved in the record"})
    a = _api_with_stream(tmp_path, [
        {"seq": 4, "type": "tool_call",
         "payload": {"name": "write_file", "args": args}},
    ])
    r = a.get_artifact(str(gone), call_seq=4)
    assert r["current_missing"] is True and r["current"] is None
    assert r["as_written"] == "preserved in the record"


def test_chevron_binary_file_is_honest(tmp_path):
    blob = tmp_path / "img.png"
    blob.write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff\xfe binary")
    a = _bare_api(tmp_path)
    r = a.get_artifact(str(blob))
    assert r["ok"] and r["current"] is None
    assert "not displayable" in r["note"]


def test_chevron_failure_is_a_sentence(tmp_path):
    a = _bare_api(tmp_path)
    a.session = None
    r = a.get_artifact("/anything")
    assert r["ok"] is False and "\n" not in r["error"]


# ── the View dial: sidecar persistence (increment 5, design §3.5) ──────

def test_dial_defaults_to_normal(tmp_path):
    a = _bare_api(tmp_path)
    r = a.get_seat_dial()
    assert r["ok"] and r["dial"] == "normal"


def test_dial_round_trip_per_session(tmp_path):
    a = _bare_api(tmp_path)
    assert a.set_seat_dial("summary")["ok"]
    assert a.get_seat_dial()["dial"] == "summary"
    # another session keeps its own default
    a.session.session_num = 9
    assert a.get_seat_dial()["dial"] == "normal"
    # sidecar lives beside the labels file — operator instrument, dot-named
    assert (tmp_path / "Memory" / "sessions" / ".seat-prefs.json").is_file()


def test_dial_rejects_unknown_position(tmp_path):
    a = _bare_api(tmp_path)
    r = a.set_seat_dial("x-ray")
    assert r["ok"] is False and "x-ray" in r["error"]


def test_dial_survives_corrupt_sidecar(tmp_path):
    """A broken pref is never a broken desk."""
    a = _bare_api(tmp_path)
    sp = tmp_path / "Memory" / "sessions" / ".seat-prefs.json"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text("{not json")
    assert a.get_seat_dial() == {"ok": True, "dial": "normal"}
    assert a.set_seat_dial("verbose")["ok"]      # rewrites cleanly
    assert a.get_seat_dial()["dial"] == "verbose"


# ── desk error legibility (increment 6a, design §5 — her ruling) ───────

def test_send_message_failure_is_a_sentence_and_a_stream_event(tmp_path):
    """The operator gets one plain line; the record gets the error event."""
    from partner_client.trajectory import TrajectoryWriter, read_stream

    a = _bare_api(tmp_path)
    w = TrajectoryWriter(tmp_path / "tj", session_num=3)

    class _ExplodingSession:
        closed = False
        trajectory = w
        session_num = 3
        def append_user(self, *args, **kw):
            raise RuntimeError('{"error":{"code":500,"raw":"ResponseError JSON blob"}}')

    a.session = _ExplodingSession()
    a.client = types.SimpleNamespace()
    a.config = types.SimpleNamespace(subagent=None)
    r = a.send_message("hello")
    assert r["ok"] is False
    assert "ResponseError JSON blob" not in r["error"]   # raw payload never reaches the pane
    assert "\n" not in r["error"] and "Traceback" not in r["error"]
    assert "RuntimeError" in r["error"]                  # the class, named plainly
    errs = [e for e in read_stream(w.path) if e.get("type") == "error"]
    assert len(errs) == 1                                # failures are events, never silences
    assert errs[0]["payload"]["source"] == "gui.send_message"
    assert "ResponseError" in errs[0]["payload"]["message"]  # the record keeps the detail
