"""Litigators' docket: archive-reader substrate provenance (fixed 2026-08-25).

The record outranks the config: an archived room's seams and carried-
dimming speak the substrate THAT room ran on — never today's configured
model stamped onto history.
"""
import types

from partner_client_gui.api import GuiApi


def _api(cfg_model="qwen3.8:27b-q8-surveyed"):
    a = GuiApi.__new__(GuiApi)
    a.config = types.SimpleNamespace(model=types.SimpleNamespace(name=cfg_model))
    return a


GEMMA_ROOM = [
    {"role": "system", "content": "[SESSION NUM: 12]"},
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": "hi", "substrate": "gemma4:31b-mxfp8"},
    {"role": "assistant", "content": "more", "substrate": "gemma4:31b-mxfp8"},
]


def test_archive_seam_speaks_the_rooms_own_water():
    out = _api()._render_messages(GEMMA_ROOM, for_archive=True)
    divider = out[0]
    assert divider["role"] == "divider"
    assert "gemma4:31b-mxfp8" in divider["content"]
    assert "qwen" not in divider["content"]          # never today's config


def test_archive_native_turns_are_not_dimmed_foreign():
    out = _api()._render_messages(GEMMA_ROOM, for_archive=True)
    assert all(m["carried"] is False for m in out if m["role"] == "assistant")


def test_archive_pre_crossing_turns_still_dim_against_own_final_water():
    room = [
        {"role": "assistant", "content": "before", "substrate": "gemma4:31b-mxfp8"},
        {"role": "assistant", "content": "after", "substrate": "qwen3.8:27b-q8-surveyed"},
    ]
    out = _api()._render_messages(room, for_archive=True)
    assert out[0]["carried"] is True     # pre-crossing, relative to the room's final water
    assert out[1]["carried"] is False


def test_archive_without_tags_renders_honest_absence():
    room = [
        {"role": "system", "content": "[SESSION NUM: 3]"},
        {"role": "assistant", "content": "old record, no tags"},
    ]
    out = _api()._render_messages(room, for_archive=True)
    assert out[0]["content"] == "Session 3 · fresh wake"   # no substrate guessed
    assert out[1]["carried"] is False


def test_live_view_behavior_unchanged():
    out = _api()._render_messages(GEMMA_ROOM, for_archive=False)
    # live room's seam speaks the record when tags exist (truth-first there too)
    assert "gemma4:31b-mxfp8" in out[0]["content"]
    # live view still dims turns that predate the current config's water
    assert all(m["carried"] is True for m in out if m["role"] == "assistant")


def test_live_view_seam_falls_back_to_config_when_untagged():
    room = [{"role": "system", "content": "[SESSION NUM: 44]"},
            {"role": "user", "content": "hi"}]
    out = _api(cfg_model="fable-5")._render_messages(room, for_archive=False)
    assert out[0]["content"] == "Session 44 · fresh wake · fable-5"
