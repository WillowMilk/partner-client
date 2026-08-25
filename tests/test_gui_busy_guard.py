"""Litigators' docket: the GUI busy-guard (fixed 2026-08-25).

While a turn is in flight, the ground-moving doors — sail, sleep,
substrate switch, a second send — refuse with one plain sentence.
The frontend's input-disable is courtesy; this is the wall.
"""
import types

from partner_client_gui.api import GuiApi


def _api(in_flight: bool):
    a = GuiApi.__new__(GuiApi)
    a.session = types.SimpleNamespace(session_num=3, closed=False)
    a.config = types.SimpleNamespace()
    a.client = types.SimpleNamespace()
    a._turn_in_flight = in_flight
    return a


def test_sail_refuses_mid_turn():
    r = _api(True).mosaic_sail()
    assert r["ok"] is False and "turn is in flight" in r["error"]
    assert "\n" not in r["error"]                      # one sentence


def test_sleep_refuses_mid_turn():
    r = _api(True).mosaic_sleep()
    assert r["ok"] is False and "turn is in flight" in r["error"]


def test_switch_refuses_mid_turn():
    r = _api(True).switch_substrate("some-model")
    assert r["ok"] is False and "turn is in flight" in r["error"]


def test_second_send_refuses_mid_turn():
    a = _api(True)
    r = a.send_message("hello again")
    assert r["ok"] is False and "already in flight" in r["error"]


def test_flag_clears_after_a_failing_turn():
    """The finally releases the room even when the turn dies."""
    a = _api(False)
    a.client = types.SimpleNamespace()
    a.config = types.SimpleNamespace(subagent=None, trajectory=None)
    class _Boom:
        closed = False
        trajectory = None
        session_num = 3
        def append_user(self, *ar, **kw): raise RuntimeError("dies")
    a.session = _Boom()
    a._window = None
    r = a.send_message("hi")
    assert r["ok"] is False
    assert a._turn_in_flight is False                  # room released


def test_doors_open_when_idle():
    a = _api(False)
    # sail proceeds past the guard into normal validation (no session dir -> error,
    # but NOT the busy sentence)
    a.session = None
    r = a.mosaic_sail()
    assert "turn is in flight" not in r.get("error", "")
