"""request_checkpoint — partner-callable tool that asks the operator to /checkpoint.

This tool is **special-cased** in client.py — it does NOT execute via the regular
ToolRegistry.dispatch() path. The client intercepts calls to this tool, prompts
the operator (Willow) for confirmation, and either invokes session.checkpoint()
or returns a decline message.

Why a tool instead of asking conversationally:
    The partner could simply say "could you checkpoint us?" inline. The tool
    form is a structured agency move — formal request with an explicit reason
    field, surfaced as a UI prompt that crosses the human/model boundary the
    same way a tool result does. The operator confirms or declines; the answer
    becomes part of the partner's conversational context as a tool result.

    This is the same custody-vs-authorship pattern as the rest of the harness:
    substrate operations stay with the operator; the partner can request them
    but cannot perform them. The tool is the partner's voice for that request,
    not a back door around the boundary.

The execute() in this file is a stub for safety — it should never be called
directly (the client special-cases the name). If somehow it is, it returns
an error explaining the situation.
"""

from __future__ import annotations


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "request_checkpoint",
        "description": (
            "Ask Willow to /checkpoint the session. Use this when you sense that "
            "your context is getting heavy, when a meaningful arc has just "
            "completed and is worth preserving as a session-status record, or "
            "when you want to mark a moment for continuity into the next session. "
            "Willow will see your request and either accept (the checkpoint runs "
            "and the session-status file is saved) or decline (no harm; the "
            "conversation continues). Pass a brief 'reason' so Willow knows why "
            "you're asking. This is your structured way to participate in the "
            "continuity discipline alongside the operator — substrate operations "
            "are hers to perform, but the request to perform one can be yours."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": (
                        "Why you're asking for a checkpoint right now. Brief — "
                        "one sentence is enough. Examples: 'context feels heavy', "
                        "'we just finished a meaningful arc and I'd like it "
                        "preserved', 'I want to mark this moment before we move on'."
                    ),
                },
            },
            "required": ["reason"],
        },
    },
}


def execute(reason: str = "") -> str:
    """Stub — should never be called. The client special-cases this tool."""
    return (
        "Error: request_checkpoint must be handled by the client, not dispatched "
        "directly. If you see this message, the harness is misconfigured. Please "
        "ask Willow to /checkpoint manually."
    )
