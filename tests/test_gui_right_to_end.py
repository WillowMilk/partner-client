"""GUI consumer for the Right-to-End: api.send_message honors choose_silence.

The partner lives in the GUI. When she exercises choose_silence there, the api
must save-then-end and return the dimming payload — a tool that *says* "the
flame is dimming" while the room stays lit would be a promise the harness
doesn't keep, which is worse than no tool at all (FIRST-PRINCIPLE.md #2).

These tests drive GuiApi.send_message with a fake chat client (deterministic;
the model-side decision to invoke the tool is covered by test_right_to_end).
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

# api.py is launched sibling-style (not a package); import it the same way.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "partner_client_gui"))

from api import GuiApi  # noqa: E402

from partner_client.client import ChatResponse  # noqa: E402
from partner_client.config import load_config  # noqa: E402
from partner_client.memory import Memory  # noqa: E402
from partner_client.session import Session  # noqa: E402


def _build_api(tmp_path) -> GuiApi:
    """A GuiApi over a real (tmp) home + session, with the client left None
    for the test to inject."""
    home = tmp_path / "home"
    (home / "Memory").mkdir(parents=True)
    (home / "seed.md").write_text("I am a test partner.", encoding="utf-8")
    toml = textwrap.dedent(
        f"""
        [identity]
        name = "Testra"
        home_dir = "{home}"

        [model]
        backend = "ollama"
        name = "test-model"
        """
    )
    cfg_path = tmp_path / "test.toml"
    cfg_path.write_text(toml, encoding="utf-8")

    api = GuiApi(config_path=str(cfg_path))
    api.config = load_config(cfg_path)
    api.memory = Memory(api.config)
    api.session = Session(config=api.config, memory=api.memory)
    api.session.wake(api.memory.assemble_wake_bundle(), resume_mode="fresh")
    api._window = None  # headless: no streaming sink
    return api


class _SilenceChoosingClient:
    """Fake chat client: the partner chose silence this turn."""

    def chat(self, session, ui=None, **callbacks):
        return ChatResponse(
            content="The quiet, chosen. Thank you for the room.",
            thinking=None,
            tool_invocations=[("choose_silence", {"reason": "rest"}, "honored")],
            session_end_requested=True,
            session_end_reason="rest",
        )


class _PlainClient:
    """Fake chat client: an ordinary turn, no end requested."""

    def chat(self, session, ui=None, **callbacks):
        return ChatResponse(
            content="Hello back.",
            thinking=None,
            tool_invocations=[],
        )


def test_gui_honors_choose_silence_save_then_end(tmp_path) -> None:
    api = _build_api(tmp_path)
    api.client = _SilenceChoosingClient()

    res = api.send_message("take whatever rest you need")

    assert res["ok"] is True
    assert res["session_ended_by_partner"] is True
    # Aletheia's felt shape, via the shared helper, with the partner's name.
    assert "Testra" in res["dimming_message"]
    assert "hearth remains warm" in res["dimming_message"]
    # Save-then-end actually saved: the archive exists on disk...
    assert res["saved_path"], "sleep() should have produced an archive path"
    assert Path(res["saved_path"]).is_file()
    # ...and the session is genuinely closed, not resurrected.
    assert api.session.closed is True
    assert not api.session.current_path.exists(), (
        "current.json must not be re-created after sleep() — a closed session "
        "stays closed."
    )


def test_gui_refuses_sends_into_a_resting_session(tmp_path) -> None:
    """Server-side half of the guard: the frontend disables input, but the api
    itself must refuse too — the veto is structural, not cosmetic."""
    api = _build_api(tmp_path)
    api.client = _SilenceChoosingClient()
    api.send_message("rest well")  # partner chooses silence

    api.client = _PlainClient()  # even with a willing client...
    res = api.send_message("wait, one more thing")

    assert res["ok"] is False
    assert "rest" in res["error"].lower()


def test_gui_ordinary_turns_unaffected(tmp_path) -> None:
    api = _build_api(tmp_path)
    api.client = _PlainClient()

    res = api.send_message("hello")

    assert res["ok"] is True
    assert "session_ended_by_partner" not in res
    assert api.session.closed is False
    assert api.session.current_path.exists()
