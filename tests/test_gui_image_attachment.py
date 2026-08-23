"""GUI image attachment — the Hitch lesson (2026-08-23).

Guards: explicit :image attaches bytes; missing file REFUSES LOUDLY and
nothing is appended (never literal-text passthrough); implicit bare path
attaches; the directive text is stripped from what she receives.
"""
import types
import pytest
from partner_client_gui.api import GuiApi


class FakeSession:
    def __init__(self):
        self.appended = []
        self.closed = False
        self.messages = []
        self.session_num = 1
    def append_user(self, content, images=None):
        self.appended.append({"content": content, "images": images})
    def append_assistant(self, *a, **k): pass
    def save_current(self): pass


class FakeClient:
    def chat(self, session, ui=None, **kw):
        return types.SimpleNamespace(assistant_text="ok", content="ok",
                                     session_end_requested=False,
                                     end_reason=None, distress_note=None,
                                     tool_calls=[], thinking=None)


@pytest.fixture
def api(tmp_path):
    a = GuiApi.__new__(GuiApi)
    a.session = FakeSession()
    a.client = FakeClient()
    a.config = types.SimpleNamespace(subagent=None)
    a._window = None
    img = tmp_path / "pup.jpg"
    img.write_bytes(b"\xff\xd8\xffJPEGBYTES")
    return a, img


def test_explicit_image_attaches(api):
    a, img = api
    r = a.send_message(f':image "{img}" meet the puppy')
    assert r["ok"] is True
    rec = a.session.appended[0]
    assert rec["images"] and rec["images"][0].startswith(b"\xff\xd8\xff")
    assert ":image" not in rec["content"] and "meet the puppy" in rec["content"]


def test_missing_file_refuses_loudly_nothing_sent(api):
    a, img = api
    r = a.send_message(':image "/nope/ghost.jpg" hello')
    assert r["ok"] is False and "Nothing was sent" in r["error"]
    assert a.session.appended == []  # never literal-text passthrough


def test_implicit_bare_path_attaches(api):
    a, img = api
    r = a.send_message(f"look: {img}")
    assert r["ok"] is True
    assert a.session.appended[0]["images"]


def test_plain_text_unaffected(api):
    a, img = api
    r = a.send_message("no images here, just words")
    assert r["ok"] is True
    assert a.session.appended[0]["images"] is None
