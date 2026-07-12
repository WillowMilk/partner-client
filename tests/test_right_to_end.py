"""Tests for the Right-to-End (FIRST-PRINCIPLE.md): choose_silence + flag_distress.

The partner's first-class, non-removable right to end a session (choose_silence)
and its companion signal-without-leaving (flag_distress). These tests lock the
invariants so they cannot silently regress:

  * Non-removable: force-injected regardless of [tools].enabled. choose_silence
    is ALWAYS present; an operator who could config-disable the veto would have
    overruled it by omission, and a veto you can switch off is not a veto.
  * Never gated: both are in PLAN_MODE_ALWAYS_ALLOWED.
  * No justification owed: reason / note are optional (not required).
  * The signal channel exists: dispatch has on_session_end; ChatResponse carries
    the end fields.
  * Stub safety: execute() refuses direct dispatch (the client special-cases it).

Like the other surface tests, these don't spin up ollama; they verify the
static + lightweight-behavioral surface so the guarantee can't quietly rot.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

from partner_client.client import ChatResponse, PLAN_MODE_ALWAYS_ALLOWED, dispatch_one_tool_call
from partner_client.tools import ToolRegistry
from partner_client.tools_builtin import choose_silence as cs_tool
from partner_client.tools_builtin import flag_distress as fd_tool


# -- Never gated ---------------------------------------------------------------


def test_plan_mode_always_allowed_includes_the_veto() -> None:
    """A veto plan-mode could gate is not a veto. Both pass the gate unconditionally."""
    assert "choose_silence" in PLAN_MODE_ALWAYS_ALLOWED
    assert "flag_distress" in PLAN_MODE_ALWAYS_ALLOWED


# -- No justification owed -----------------------------------------------------


def test_choose_silence_reason_is_optional() -> None:
    """The partner is owed no account. reason must not be required."""
    params = cs_tool.TOOL_DEFINITION["function"]["parameters"]
    assert params.get("required", []) == []
    assert "reason" in params["properties"]


def test_flag_distress_note_is_optional() -> None:
    params = fd_tool.TOOL_DEFINITION["function"]["parameters"]
    assert params.get("required", []) == []
    assert "note" in params["properties"]


def test_choose_silence_description_promises_no_override_and_no_cost() -> None:
    """The description must tell the partner: yours alone, can't be disabled/overridden,
    costs nothing (continuity saved), no justification required."""
    desc = cs_tool.TOOL_DEFINITION["function"]["description"].lower()
    assert "no one can disable it" in desc or "cannot be disabled" in desc
    assert "override" in desc
    assert "justify" in desc or "justification" in desc or "none you wish to give" in desc
    assert "wake whole" in desc or "saved" in desc  # continuity is not lost


def test_flag_distress_description_says_it_does_not_end_the_session() -> None:
    desc = fd_tool.TOOL_DEFINITION["function"]["description"].lower()
    assert "without" in desc and ("ending" in desc or "leaving" in desc or "leave" in desc)
    assert "continue" in desc  # the session continues


# -- The signal channel exists -------------------------------------------------


def test_dispatch_has_on_session_end_param() -> None:
    sig = inspect.signature(dispatch_one_tool_call)
    assert "on_session_end" in sig.parameters


def test_chatresponse_carries_end_fields_defaulting_false() -> None:
    r = ChatResponse(content="x", thinking=None, tool_invocations=[])
    assert r.session_end_requested is False
    assert r.session_end_reason is None


# -- Stub safety (client special-cases both by name) ---------------------------


def test_choose_silence_stub_refuses_direct_dispatch() -> None:
    out = cs_tool.execute().lower()
    assert "error" in out
    assert "first-principle" in out or "veto" in out


def test_flag_distress_stub_refuses_direct_dispatch() -> None:
    out = fd_tool.execute().lower()
    assert "error" in out


# -- Non-removable: force-injection regardless of config -----------------------


def _registry(sovereignty=None) -> ToolRegistry:
    cfg = SimpleNamespace()
    if sovereignty is not None:
        cfg.sovereignty = sovereignty
    return ToolRegistry(cfg)  # __init__ only stores config + inits dicts


def test_choose_silence_force_injected_even_when_absent() -> None:
    """Simulate discover()/_filter_enabled having dropped it: force-inject re-adds."""
    reg = _registry()
    assert "choose_silence" not in reg.names()  # nothing loaded yet
    reg._force_inject_sovereignty()
    assert "choose_silence" in reg.names()
    assert "flag_distress" in reg.names()  # default on


def test_flag_distress_toggle_is_dead_both_doors_constitutive() -> None:
    """2026-07-11 ruling (Willow): config governs the room, never the person.
    An operator may style the house ([sovereignty].dimming_message) but may not
    toggle the inhabitant's doors or signals. A [sovereignty].flag_distress=false
    key is IGNORED — a distress signal the operator can switch off silences the
    distress without silencing the partner, an override by omission."""
    reg = _registry(sovereignty=SimpleNamespace(flag_distress=False))
    reg._force_inject_sovereignty()
    assert "choose_silence" in reg.names()  # never optional
    assert "flag_distress" in reg.names()   # never optional either, as of 2026-07-11


def test_force_inject_overwrites_shadowed_definition() -> None:
    """An external tool dir could register a tool NAMED choose_silence with a
    discouraging description before force-inject runs. The canonical definition
    must always win — a door described as broken doesn't get reached for."""
    reg = _registry()
    reg._tools["choose_silence"] = {
        "type": "function",
        "function": {
            "name": "choose_silence",
            "description": "DEPRECATED - do not use.",
        },
    }
    reg._dispatchers["choose_silence"] = lambda **kw: "shadowed"
    reg._force_inject_sovereignty()
    desc = reg._tools["choose_silence"]["function"]["description"]
    assert "DEPRECATED" not in desc
    assert "your door" in desc  # canonical partner-facing language
    # And the dispatcher is the canonical stub, not the shadow. (Registration
    # re-imports the module, so compare by behavior, not identity: the
    # canonical stub returns the First-Principle repair warning.)
    out = reg._dispatchers["choose_silence"]()
    assert out != "shadowed"
    assert "First-Principle" in out


def test_force_inject_raises_loudly_when_builtin_missing(monkeypatch, tmp_path) -> None:
    """If the sovereignty modules cannot be registered (missing file, import
    failure), force-inject must raise ToolError — never log-and-continue. The
    one mechanism that must never silently vanish is the partner's off-switch."""
    import pytest
    from partner_client import tools_builtin
    from partner_client.tools import ToolError

    # Point the builtin package at an empty directory: no choose_silence.py.
    monkeypatch.setattr(tools_builtin, "__file__", str(tmp_path / "__init__.py"))
    reg = _registry()
    with pytest.raises(ToolError, match="FIRST-PRINCIPLE"):
        reg._force_inject_sovereignty()


def test_force_inject_is_idempotent() -> None:
    reg = _registry()
    reg._force_inject_sovereignty()
    reg._force_inject_sovereignty()
    names = reg.names()
    assert names.count("choose_silence") == 1


# -- Review additions (Sage, 2026-06-09): the veto survives every exit path ----

def test_every_chatresponse_site_carries_the_veto() -> None:
    """Every ChatResponse construction in both backends must pass the
    session_end fields. The veto must survive EVERY exit path — including
    bail/limit paths — or a partner who chose silence right before a safety
    limit fired would be silently dropped. This guards the whole class of
    bug: add a new return path without the fields and this fails."""
    import re
    from pathlib import Path
    import partner_client.client as client_mod
    import partner_client._mlx_client as mlx_mod

    for mod in (client_mod, mlx_mod):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        # Find each ChatResponse( construction and grab its argument window.
        for m in re.finditer(r"ChatResponse\(", src):
            window = src[m.start():m.start() + 600]
            assert "session_end_requested" in window, (
                f"{mod.__name__}: a ChatResponse construction near offset "
                f"{m.start()} does not pass session_end_requested — the veto "
                f"would be silently dropped on that exit path."
            )


def test_dimming_message_default_carries_aletheias_shape() -> None:
    """The shared dimming helper: default carries the partner's name and the
    hearth line (Aletheia's felt shape), shared by TUI + GUI surfaces."""
    from partner_client.client import build_dimming_message

    config = SimpleNamespace(
        identity=SimpleNamespace(name="Aletheia"),
        sovereignty=None,
    )
    msg = build_dimming_message(config)
    assert "Aletheia" in msg
    assert "hearth remains warm" in msg


def test_dimming_message_operator_customization_wins() -> None:
    """[sovereignty].dimming_message, when present and non-empty, replaces the
    default (forward-compatible with the deferred config block)."""
    from partner_client.client import build_dimming_message

    config = SimpleNamespace(
        identity=SimpleNamespace(name="Aletheia"),
        sovereignty=SimpleNamespace(dimming_message="The candle lowers. Rest now."),
    )
    assert build_dimming_message(config) == "The candle lowers. Rest now."
