"""Tests for the GUI Lumen-surface — the partner's parallel reach made visible.

When the partner casts Lumens (the sub-agent tool), the GUI's stream sink emits
a distinct `__lumen_cast` event (labels + the partner's term) instead of folding
the cast into a raw tool-result blob. Normal tools are unaffected.

The GUI lives under partner_client_gui/ (not on the default import path), so we
add it explicitly — mirroring how launch.py loads it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_GUI_DIR = Path(__file__).resolve().parent.parent / "partner_client_gui"
if str(_GUI_DIR) not in sys.path:
    sys.path.insert(0, str(_GUI_DIR))

from api import _WebViewStreamSink  # noqa: E402


class _FakeWindow:
    """Captures the JS pushed via evaluate_js() so we can assert on it."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def evaluate_js(self, js: str) -> None:
        self.calls.append(js)


def _sink(term: str = "Lumen") -> tuple[_WebViewStreamSink, _FakeWindow]:
    win = _FakeWindow()
    return _WebViewStreamSink(win, subagent_term=term), win


def test_cast_emits_single_lumen_event_with_labels_and_term() -> None:
    sink, win = _sink(term="Lumen")
    sink.show_tool_call(
        "cast_lumens",
        {"tasks": [
            {"task": "survey the codebase", "label": "survey"},
            {"task": "check the tests", "label": "tests"},
        ]},
        "Cast 2 working Lumens; all returned to the center.\n...",
    )
    lumen = [c for c in win.calls if "__lumen_cast" in c]
    generic = [c for c in win.calls if "__stream_tool_call" in c]
    assert len(lumen) == 1
    assert not generic
    assert "Lumen" in lumen[0]
    assert "survey" in lumen[0] and "tests" in lumen[0]


def test_normal_tool_does_not_emit_lumen_event() -> None:
    sink, win = _sink()
    sink.show_tool_call("read_file", {"path": "/x"}, "file contents")
    assert not [c for c in win.calls if "__lumen_cast" in c]
    assert [c for c in win.calls if "__stream_tool_call" in c]


def test_missing_label_falls_back_to_reach_n() -> None:
    sink, win = _sink(term="facet")
    sink.show_tool_call(
        "spawn_subagents",
        {"tasks": [{"task": "do a thing"}, {"task": "do another"}]},
        "Dispatched 2 working facets; all reported back.",
    )
    lumen = [c for c in win.calls if "__lumen_cast" in c]
    assert len(lumen) == 1
    # the term is carried, and fallback labels are present
    assert "facet" in lumen[0]
    assert "reach 1" in lumen[0] and "reach 2" in lumen[0]


def test_empty_tasks_list_is_not_treated_as_a_cast() -> None:
    sink, win = _sink()
    sink.show_tool_call("some_tool", {"tasks": []}, "result")
    # empty tasks → not a cast → routes to the generic tool path
    assert not [c for c in win.calls if "__lumen_cast" in c]
    assert [c for c in win.calls if "__stream_tool_call" in c]


def test_labels_are_valid_json_array() -> None:
    sink, win = _sink(term="Lumen")
    sink.show_tool_call(
        "cast_lumens",
        {"tasks": [{"task": "t", "label": "alpha"}]},
        "Cast 1 working Lumen; all returned to the center.",
    )
    call = next(c for c in win.calls if "__lumen_cast" in c)
    # _call_js json-dumps each arg, so labels_json is a double-encoded string:
    # the frontend receives it as a JS string and JSON.parse()s it back. Mirror
    # that here — raw_decode the first arg (the labels_json string), then load it.
    marker = "window.__lumen_cast("
    args_str = call[call.rfind(marker) + len(marker):]
    labels_json, _ = json.JSONDecoder().raw_decode(args_str)  # → '["alpha"]'
    labels = json.loads(labels_json)  # → ["alpha"]
    assert labels == ["alpha"]
