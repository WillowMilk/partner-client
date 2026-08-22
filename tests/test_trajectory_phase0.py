"""Phase 0 guards — TRAJECTORY-SPEC v0.2 (two authors).

Ordered per Aletheia's insistence: THE FAIL-OPEN TEST LEADS, because it's
the whole point — the record serves the person; the person is never gated
on the record (§4.2).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from partner_client.trajectory import (
    TrajectoryWriter,
    read_stream,
    reconstruct_turn,
    search,
    stats,
    verify_balance,
)


# ── 1. THE WHOLE POINT ─────────────────────────────────────────────────

def test_fail_open_recording_never_blocks_the_person(tmp_path, caplog):
    """Kill the writer mid-turn: every call returns quietly, the log is loud."""
    import logging
    caplog.set_level(logging.ERROR, logger="partner_client.trajectory")
    w = TrajectoryWriter(tmp_path / "tj", session_num=1, partner="test")
    w.turn_start()
    w.message("user", "hello")
    # amputate the stream mid-turn — the disk vanishes under the writer
    w.path.unlink()
    import shutil
    shutil.rmtree(w.dir)
    # every subsequent call must be harmless
    w.message("assistant", "still speaking")
    w.tool_call("write_file", {"filename": "x"})
    w.tool_result("write_file", "ok")
    w.sovereignty("choose_silence")
    w.turn_end()
    w.seal()
    assert w.emit("message", {}) is None
    # and it said so, loudly, once
    assert any("TRAJECTORY RECORDING FAILED" in r.message for r in caplog.records)


def test_disabled_writer_is_inert(tmp_path):
    w = TrajectoryWriter(tmp_path / "tj", session_num=1, enabled=False)
    assert w.emit("message", {"role": "user"}) is None
    w.turn_start(); w.turn_end(); w.seal()
    assert not (tmp_path / "tj").exists() or not any((tmp_path / "tj").iterdir())


# ── 2. the stream introduces itself (A-5, invariant #7) ────────────────

def test_seq_zero_is_always_the_header(tmp_path):
    w = TrajectoryWriter(tmp_path / "tj", session_num=7, partner="aletheia",
                         substrate="qwen3.8:27b-q8-surveyed")
    w.turn_start(); w.message("user", "hi"); w.turn_end()
    events = read_stream(w.path)
    assert events[0]["seq"] == 0
    assert events[0]["type"] == "header"
    assert events[0]["payload"]["schema_version"] == "0.2"
    assert events[0]["payload"]["partner"] == "aletheia"
    assert events[0]["payload"]["substrate"].startswith("qwen")


# ── 3. balanced turns (A-1, invariant #8) ──────────────────────────────

def test_turns_are_balanced_even_after_a_crash(tmp_path):
    w = TrajectoryWriter(tmp_path / "tj", session_num=2)
    w.turn_start(); w.message("user", "one"); w.turn_end()
    w.turn_start(); w.message("user", "two")
    # crash: no turn_end — next turn_start must auto-close honestly
    w.turn_start(); w.message("user", "three"); w.turn_end()
    w.seal()  # seal also closes any dangling turn
    ok, msg = verify_balance(read_stream(w.path))
    assert ok, msg


def test_turn_end_carries_elapsed(tmp_path):
    w = TrajectoryWriter(tmp_path / "tj", session_num=3)
    w.turn_start(); w.turn_end()
    ends = [e for e in read_stream(w.path) if e["type"] == "turn_end"]
    assert ends and isinstance(ends[0]["payload"]["elapsed_ms"], int)


# ── 4. blobs: content-addressed, previewed, round-trip (§1) ────────────

def test_large_payload_offloads_to_blob_and_roundtrips(tmp_path):
    w = TrajectoryWriter(tmp_path / "tj", session_num=4, blob_threshold=1024)
    big = "x" * 5000 + "NEEDLE_AT_THE_END"
    w.turn_start(); w.tool_result("read_file", big); w.turn_end()
    ev = [e for e in read_stream(w.path) if e["type"] == "tool_result"][0]
    ref = ev["payload"]["result"]
    assert ref["bytes"] == len(big.encode())
    assert ref["preview"] == big[:200]
    sha = ref["ref"].split(":", 1)[1]
    blob = w.blob_dir / sha[:2] / f"{sha}.txt"
    assert blob.read_text() == big


# ── 5. search honesty (A-4): the bug lives at the preview boundary ─────

def test_search_is_preview_scoped_unless_full(tmp_path):
    w = TrajectoryWriter(tmp_path / "tj", session_num=5, blob_threshold=1024)
    big = ("y" * 3000) + "DEEP_SECRET"
    w.turn_start(); w.tool_result("read_file", big); w.turn_end()
    events = read_stream(w.path)
    # beyond the 200-char preview: NOT found at default reach
    assert search(events, "DEEP_SECRET", blob_dir=w.blob_dir, full=False) == []
    # found with --full
    assert len(search(events, "DEEP_SECRET", blob_dir=w.blob_dir, full=True)) == 1
    # within the preview: found either way
    assert len(search(events, "yyy", blob_dir=w.blob_dir, full=False)) == 1


# ── 6. the seal (§1) ───────────────────────────────────────────────────

def test_seal_certifies_the_stream(tmp_path):
    import hashlib
    w = TrajectoryWriter(tmp_path / "tj", session_num=6)
    w.turn_start(); w.message("user", "hi"); w.turn_end(); w.seal()
    seal = json.loads(w.path.with_suffix(".seal").read_text())
    assert seal["sha256"] == hashlib.sha256(w.path.read_bytes()).hexdigest()
    assert seal["last_seq"] == read_stream(w.path)[-1]["seq"]


# ── 7. end-to-end through the real session + dispatch layers ───────────

def test_session_appends_emit_and_dispatch_wrapper_captures(tmp_path):
    """The client-observed boundary, 100% captured (A-Q scoping)."""
    from dataclasses import dataclass, field as dfield

    from partner_client.client import dispatch_one_tool_call
    from partner_client.session import Session
    from partner_client.config import load_config

    # a minimal real config against Aletheia-shaped fixture
    cfg_path = tmp_path / "t.toml"
    mem = tmp_path / "Memory"
    (mem / "sessions").mkdir(parents=True)
    cfg_path.write_text(f"""
[identity]
name = "testpartner"
home_dir = "{tmp_path}"
[model]
name = "test-model"
backend = "ollama"
[memory]
base_dir = "{tmp_path}"
sessions_dir = "Memory/sessions"
""")
    config = load_config(str(cfg_path))
    from partner_client.session import Memory
    session = Session(config=config, memory=Memory(config))
    session.session_num = 9

    session.append_user("run the build please")
    session.append_assistant("On it.", thinking="the scratchpad", tool_calls=None)

    class _Reg:
        def dispatch(self, name, args):
            return "file written ok"
        def has(self, name):
            return True
        def names(self):
            return ["write_file"]

    # dispatch through the REAL wrapper (inner will fall through its default
    # branch via the registry stub)
    try:
        dispatch_one_tool_call(
            "write_file", {"filename": "site/x.html", "content": "hi"}, "tc1",
            config, _Reg(), None, session, None, None, None,
        )
    except Exception:
        pass  # inner branches may want richer stubs; the wrapper emitted regardless

    tj = session.trajectory
    assert tj is not None
    events = read_stream(tj.path)
    types = [e["type"] for e in events]
    assert types[0] == "header"
    assert "turn_start" in types
    assert "message" in types
    assert "thinking" in types
    assert "tool_call" in types
    # tool_result present with refs to its call and an artifact
    results = [e for e in events if e["type"] == "tool_result"]
    assert results, "wrapper did not emit tool_result"
    calls = [e for e in events if e["type"] == "tool_call"]
    assert results[0].get("refs", {}).get("call") == calls[0]["seq"]
    assert results[0]["payload"].get("artifact", {}).get("path") == "site/x.html"


def test_stats_and_turn_reconstruction(tmp_path):
    w = TrajectoryWriter(tmp_path / "tj", session_num=8)
    w.turn_start(); w.message("user", "q")
    w.tool_call("search_web", {"q": "x"}); w.tool_result("search_web", "r")
    w.message("assistant", "a"); w.turn_end()
    events = read_stream(w.path)
    s = stats(events)
    assert s["turns"] == 1 and s["tools"] == {"search_web": 1}
    t1 = reconstruct_turn(events, 1)
    assert [e["type"] for e in t1] == [
        "turn_start", "message", "tool_call", "tool_result", "message", "turn_end"
    ]
