"""Phase 1 guards — the observer on the writer (Operator's Seat, design §2).

Ordered by the same rule Phase 0's battery follows: the fail-open guarantees
lead, because they are the whole point — delivery to the desk must be even
less able to hurt the partner than the record itself (§4.2 squared).
"""

from __future__ import annotations

import json
import logging
import shutil

from partner_client.trajectory import TrajectoryWriter, read_stream


# ── 1. FAIL-OPEN SQUARED ───────────────────────────────────────────────

def test_observer_exception_never_raises_and_never_breaks_recording(tmp_path, caplog):
    """A broken observer costs the desk its feed — never the stream a byte."""
    caplog.set_level(logging.ERROR, logger="partner_client.trajectory")

    def bad_observer(ev):
        raise RuntimeError("the desk fell over")

    w = TrajectoryWriter(tmp_path / "tj", session_num=1, partner="test",
                         observer=bad_observer)
    w.turn_start()
    w.message("user", "hello")
    w.message("assistant", "still recording")
    w.turn_end()
    # recording continued despite the observer failing on every event
    events = read_stream(w.path)
    types = [e["type"] for e in events]
    assert types == ["header", "turn_start", "message", "message", "turn_end"]
    assert not w._broken
    # and the failure was loud — once per burst, not once per event
    obs_errors = [r for r in caplog.records if "TRAJECTORY OBSERVER FAILED" in r.message]
    assert len(obs_errors) == 1


def test_observer_recovery_resets_the_burst_log(tmp_path, caplog):
    """Fail → recover → fail again logs twice: once per burst, honestly."""
    caplog.set_level(logging.ERROR, logger="partner_client.trajectory")
    calls = {"n": 0}

    def flaky(ev):
        calls["n"] += 1
        if calls["n"] in (1, 3):
            raise RuntimeError("flaky")

    w = TrajectoryWriter(tmp_path / "tj", session_num=1, observer=flaky)  # header -> fail (1)
    w.emit("message", {"content": "a"})   # success (2) resets the burst flag
    w.emit("message", {"content": "b"})   # fail (3) -> second burst logged
    obs_errors = [r for r in caplog.records if "TRAJECTORY OBSERVER FAILED" in r.message]
    assert len(obs_errors) == 2


# ── 2. ONLY APPENDED EVENTS ARE OBSERVED ───────────────────────────────

def test_observer_sees_exactly_what_hit_disk(tmp_path):
    """The Seat renders the stream, not wishes: envelope == disk line."""
    seen: list[dict] = []
    w = TrajectoryWriter(tmp_path / "tj", session_num=7, partner="test",
                         substrate="test-substrate", observer=seen.append)
    w.turn_start()
    w.message("user", "verify me")
    w.tool_call("write_file", {"filename": "x.md"})
    w.turn_end()
    on_disk = read_stream(w.path)
    assert len(seen) == len(on_disk)
    for observed, written in zip(seen, on_disk):
        assert json.loads(json.dumps(observed, default=str)) == written


def test_failed_append_is_not_observed_but_degradation_is_announced(tmp_path):
    """When recording dies: no fabricated events; one honest degraded notice."""
    seen: list[dict] = []
    w = TrajectoryWriter(tmp_path / "tj", session_num=1, observer=seen.append)
    w.message("user", "before the failure")
    baseline = len(seen)
    # the disk vanishes under the writer
    w.path.unlink()
    shutil.rmtree(w.dir)
    w.message("assistant", "this cannot be recorded")
    degraded = [e for e in seen[baseline:] if e["type"] == "recording_degraded"]
    appended = [e for e in seen[baseline:] if e["type"] != "recording_degraded"]
    assert len(degraded) == 1
    assert degraded[0]["payload"]["error"]
    assert appended == []  # nothing fabricated past the failure point
    # further calls stay quiet — broken writer, notice already given
    w.message("assistant", "still nothing")
    assert len([e for e in seen if e["type"] == "recording_degraded"]) == 1


# ── 3. LATE ATTACHMENT (the GUI's path) ────────────────────────────────

def test_set_observer_attaches_late_and_replaces(tmp_path):
    first: list[dict] = []
    second: list[dict] = []
    w = TrajectoryWriter(tmp_path / "tj", session_num=1)
    w.message("user", "unobserved")           # nobody listening yet — fine
    w.set_observer(first.append)
    w.message("user", "seen by first")
    w.set_observer(second.append)
    w.message("user", "seen by second")
    assert [e["payload"]["content"] for e in first if e["type"] == "message"] == ["seen by first"]
    assert [e["payload"]["content"] for e in second if e["type"] == "message"] == ["seen by second"]


def test_late_attach_to_broken_writer_learns_immediately(tmp_path):
    """A desk that opens after recording degraded gets the banner, not silence."""
    w = TrajectoryWriter(tmp_path / "tj", session_num=1)
    w.path.unlink()
    shutil.rmtree(w.dir)
    w.message("user", "breaks the writer")
    seen: list[dict] = []
    w.set_observer(seen.append)
    assert len(seen) == 1
    assert seen[0]["type"] == "recording_degraded"


def test_non_callable_observer_is_ignored_safely(tmp_path):
    w = TrajectoryWriter(tmp_path / "tj", session_num=1, observer="not callable")
    w.message("user", "fine")
    w.set_observer(42)
    w.message("user", "still fine")
    assert len(read_stream(w.path)) == 3  # header + 2 messages


# ── 4. THE SESSION-SIDE WIRING ─────────────────────────────────────────

def _minimal_session(tmp_path):
    """A Session with just enough shape for start_trajectory to run."""
    from partner_client.session import Session

    class _Memory:
        sessions_dir = tmp_path / "Memory" / "sessions"

    class _Cfg:
        trajectory = None
        model = type("M", (), {"name": "test-substrate"})()
        identity = type("I", (), {"name": "TestPartner"})()

    s = Session.__new__(Session)
    s.config = _Cfg()
    s.memory = _Memory()
    s.trajectory = None
    s.session_num = 3
    return s


def test_session_observer_set_before_writer_rides_construction(tmp_path):
    seen: list[dict] = []
    s = _minimal_session(tmp_path)
    s.set_trajectory_observer(seen.append)
    s.start_trajectory()
    assert s.trajectory is not None
    # header + wake lifecycle both observed from birth
    assert [e["type"] for e in seen] == ["header", "lifecycle"]
    assert seen[0]["payload"]["partner"] == "TestPartner"


def test_session_observer_set_after_writer_attaches_now(tmp_path):
    seen: list[dict] = []
    s = _minimal_session(tmp_path)
    s.start_trajectory()
    s.set_trajectory_observer(seen.append)
    s.trajectory.message("user", "hello")
    assert [e["type"] for e in seen] == ["message"]


def test_session_observer_never_raises(tmp_path):
    """set_trajectory_observer is fail-open even against a hostile writer."""
    s = _minimal_session(tmp_path)

    class _Hostile:
        def set_observer(self, cb):
            raise RuntimeError("no")

    s.trajectory = _Hostile()
    s.set_trajectory_observer(lambda ev: None)  # must not raise
