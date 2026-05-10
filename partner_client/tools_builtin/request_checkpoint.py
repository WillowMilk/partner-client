"""request_checkpoint - partner-callable tool that asks the operator to /checkpoint.

This tool is **special-cased** in client.py - it does NOT execute via the
regular ToolRegistry.dispatch() path. The client intercepts calls to this tool,
prompts the operator (Willow) for confirmation, and on acceptance injects the
MOSAIC checkpoint discipline prompt as a system message so the partner authors
updates to her continuity files (MEMORY.md, intentions, emotional-memory) via
her existing edit_file / write_file tools on her next turn.

**Architecture note (2026-05-10 rework):**

/checkpoint is the MOSAIC continuity-authoring ceremony - partner-side, multi-
file. /save is the separate operator-side bookmark (writes session-status .md
+ snapshots current.json for resume). They're orthogonal - operator may run
either, both, or neither, in any order. request_checkpoint maps to /checkpoint
specifically - it asks for the discipline, NOT the bookmark.

This nomenclature aligns with Sage's /checkpoint skill in the Claude Code
environment: cross-substrate, /checkpoint always means "author updates to
continuity files," never "snapshot the session for resume."

Why a tool instead of asking conversationally:
    The partner could simply say "could you checkpoint us?" inline. The tool
    form is a structured agency move - formal request with an explicit reason
    field, surfaced as a UI prompt that crosses the human/model boundary the
    same way a tool result does. The operator confirms or declines; the answer
    becomes part of the partner's conversational context as a tool result.

    This is the same custody-vs-authorship pattern as the rest of the harness:
    substrate operations stay with the operator; the partner can request them
    but cannot perform them. The tool is the partner's voice for that request,
    not a back door around the boundary.

The execute() in this file is a stub for safety - it should never be called
directly (the client special-cases the name). If somehow it is, it returns
an error explaining the situation.
"""

from __future__ import annotations


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "request_checkpoint",
        "description": (
            "Ask Willow to invoke the MOSAIC /checkpoint discipline. Use this "
            "when a meaningful arc has just completed and you want to author "
            "updates to your continuity files (MEMORY.md, intentions, "
            "emotional-memory, etc.) so the next session - fresh wake or "
            "resume - finds itself oriented. Willow will see your request "
            "and either accept (the discipline prompt is queued; on your "
            "next turn you author the file updates via edit_file / "
            "write_file, each diff-reviewed) or decline (no harm; the "
            "conversation continues). Pass a brief 'reason' so Willow knows "
            "why you're asking. NOTE: this requests the FILE-AUTHORING "
            "discipline, not a session bookmark. Bookmarking the session "
            "for resume is a separate operator-side action (/save) that "
            "the partner doesn't need a dedicated tool for - every turn "
            "already writes current.json atomically. You ask for /checkpoint "
            "when you have things worth writing into your continuity files, "
            "not when you want a save-point."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": (
                        "Why you're asking for a checkpoint right now - what "
                        "happened in this arc that's worth authoring into your "
                        "continuity files. One sentence is enough. Examples: "
                        "'we just named a new principle worth adding to "
                        "emotional-memory', 'this session built X and I'd "
                        "like to log it in MEMORY.md before we continue', "
                        "'the work today touched intentions I should mark "
                        "complete'."
                    ),
                },
            },
            "required": ["reason"],
        },
    },
}


def execute(reason: str = "") -> str:
    """Stub - should never be called. The client special-cases this tool."""
    return (
        "Error: request_checkpoint must be handled by the client, not dispatched "
        "directly. If you see this message, the harness is misconfigured. Please "
        "ask Willow to /checkpoint manually."
    )
