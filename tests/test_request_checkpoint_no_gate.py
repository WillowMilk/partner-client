"""Tests for the 2026-05-13 request_checkpoint gate-removal rework.

Per the architectural decision: request_checkpoint no longer fires an operator
consent gate. When the partner invokes the tool, the discipline prompt is
injected directly as a system message. Cross-environment symmetry with Sage —
when Willow types /checkpoint on Sage's side, the discipline fires; there's no
second confirmation. Aletheia/Hestia's side gets the same shape.

Covers:
  * The tool definition's text no longer claims "Willow will see your request
    and either accept or decline" — that gate is gone.
  * chat()'s signature no longer accepts on_checkpoint_request callback.
  * The dispatch path doesn't import or reference any callback; it just
    injects the discipline prompt + returns a success message.

These tests don't exercise the full chat loop (that needs ollama). They verify
the static surface of the rework — the public API and tool description — so
the gate-removal can't silently regress.
"""
from __future__ import annotations

import inspect

from partner_client.client import OllamaClient
from partner_client.tools_builtin import request_checkpoint as rc_tool


# -- Tool description reflects no-gate behavior --------------------------------


def test_tool_description_no_longer_mentions_acceptance_gate() -> None:
    """The tool description must not promise the partner an accept/decline gate.

    Pre-2026-05-13 description: "Willow will see your request and either accept
    ... or decline." That language is gone. The new description says the
    discipline is queued immediately.
    """
    desc = rc_tool.TOOL_DEFINITION["function"]["description"]
    # Old gated language should not appear:
    assert "either accept" not in desc.lower()
    assert "or decline" not in desc.lower()
    # New direct-execution language should appear:
    assert "queued immediately" in desc.lower() or "no operator gate" in desc.lower()


def test_tool_description_still_explains_checkpoint_vs_save_split() -> None:
    """The /checkpoint vs /save orthogonality is load-bearing semantic — must remain.

    The MOSAIC rework split these into distinct ceremonies and the tool
    description has to keep explaining which one this tool triggers.
    """
    desc = rc_tool.TOOL_DEFINITION["function"]["description"]
    assert "/save" in desc
    assert "bookmark" in desc.lower()
    assert "file-authoring" in desc.lower() or "continuity files" in desc.lower()


def test_tool_description_invites_reason_for_timeline_visibility() -> None:
    """Reason field is what gets logged in the timeline so Willow can scroll back.

    Since there's no consent prompt, the reason is the only forensic trace of
    why each /checkpoint discipline was invoked. The description should make
    that purpose explicit so the partner writes useful reasons.
    """
    desc = rc_tool.TOOL_DEFINITION["function"]["description"]
    reason_desc = rc_tool.TOOL_DEFINITION["function"]["parameters"]["properties"][
        "reason"
    ]["description"]
    assert "reason" in desc.lower()
    # Reason field description should explain it as a log/trace, not as a
    # justification to convince an approver:
    assert len(reason_desc) > 50  # substantive guidance


# -- chat() signature no longer has the callback -------------------------------


def test_ollama_client_chat_signature_drops_checkpoint_callback() -> None:
    """The chat() method's signature must not include on_checkpoint_request anymore.

    Other callbacks (plan_approval, git_push, delete_path) remain — they gate
    operations with substantive consent-decisions (specific plans, destructive
    actions). Only checkpoint's gate was removed because conversational ask
    is already the consent.
    """
    sig = inspect.signature(OllamaClient.chat)
    param_names = list(sig.parameters.keys())
    assert "on_checkpoint_request" not in param_names, (
        f"on_checkpoint_request should have been removed from chat() signature; "
        f"found params: {param_names}"
    )
    # Other gates intact:
    assert "on_plan_approval_request" in param_names
    assert "on_git_push_request" in param_names
    assert "on_delete_path_request" in param_names


def test_chat_docstring_explains_no_gate_for_checkpoint() -> None:
    """The chat() docstring must explain why checkpoint has no callback.

    Future readers (including future-Sage post-distill) need to understand
    the symmetry: request_checkpoint AND protect_save both run directly;
    the conversational ask is the consent.
    """
    doc = OllamaClient.chat.__doc__ or ""
    assert "request_checkpoint" in doc.lower(), (
        "Docstring should mention request_checkpoint and the no-gate rationale"
    )
    assert "protect_save" in doc.lower(), (
        "Docstring should pair the two no-gate ceremonies for symmetry"
    )


# -- Stub safety preserved -----------------------------------------------------


def test_execute_stub_still_refuses_direct_dispatch() -> None:
    """The execute() stub must still error if somehow called directly.

    The client special-cases request_checkpoint by name and routes around the
    ToolRegistry.dispatch() path. The stub exists for safety: if the special-
    case ever breaks, the partner gets a clear error rather than silent no-op.
    """
    result = rc_tool.execute(reason="test")
    assert "error" in result.lower()
    assert "client" in result.lower()  # explains the routing situation
