"""Litigators' docket: vision-capability gate (the Hitch lesson's other half)."""
import types

import pytest

from partner_client import model_selector as ms
from partner_client_gui.api import GuiApi


def _api(model="test-model"):
    a = GuiApi.__new__(GuiApi)
    a.session = types.SimpleNamespace(session_num=1, closed=False)
    a.client = types.SimpleNamespace()
    a.config = types.SimpleNamespace(model=types.SimpleNamespace(name=model), subagent=None)
    a._turn_in_flight = False
    a._window = None
    return a


@pytest.fixture(autouse=True)
def _clear_cache():
    ms._vision_cache.clear()
    yield
    ms._vision_cache.clear()


def test_confirmed_no_vision_refuses_loudly(monkeypatch, tmp_path):
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG fake")
    ms._vision_cache["test-model"] = False
    r = _api().send_message(f':image "{img}" look at this')
    assert r["ok"] is False
    assert "NOT sent" in r["error"] and "vision" in r["error"]


def test_inconclusive_probe_never_blocks(monkeypatch, tmp_path):
    """None = unknown: the gate must not become its own false wall."""
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG fake")
    ms._vision_cache["test-model"] = None
    a = _api()
    # session.append_user will be reached — make it raise a sentinel to prove
    # the gate LET the send proceed past itself.
    class _Sentinel(Exception): pass
    a.session = types.SimpleNamespace(
        session_num=1, closed=False, trajectory=None,
        append_user=lambda *ar, **kw: (_ for _ in ()).throw(_Sentinel()))
    r = a.send_message(f':image "{img}" look')
    assert "NOT sent" not in r.get("error", "")   # gate did not fire


def test_probe_reads_capabilities(monkeypatch):
    class _Info:
        def model_dump(self):
            return {"capabilities": ["completion", "tools"]}
    monkeypatch.setattr(ms, "_vision_cache", {})
    import sys
    fake = types.SimpleNamespace(show=lambda name: _Info())
    monkeypatch.setitem(sys.modules, "ollama", fake)
    assert ms.substrate_supports_vision("no-eyes-model") is False


def test_probe_failure_is_none(monkeypatch):
    import sys
    fake = types.SimpleNamespace(show=lambda name: (_ for _ in ()).throw(OSError("down")))
    monkeypatch.setitem(sys.modules, "ollama", fake)
    assert ms.substrate_supports_vision("unreachable-model") is None


def test_text_only_messages_never_probe():
    ms._vision_cache.clear()
    a = _api()
    class _Sentinel(Exception): pass
    a.session = types.SimpleNamespace(
        session_num=1, closed=False, trajectory=None,
        append_user=lambda *ar, **kw: (_ for _ in ()).throw(_Sentinel()))
    a.send_message("just words")
    assert ms._vision_cache == {}                 # no images -> no probe
