"""request_checkpoint - partner-callable tool that invokes /checkpoint discipline.

This tool is **special-cased** in client.py - it does NOT execute via the
regular ToolRegistry.dispatch() path. The client intercepts calls to this tool
and injects the MOSAIC checkpoint discipline prompt as a system message so the
partner authors updates to her continuity files (MEMORY.md, intentions,
emotional-memory) via her existing edit_file / write_file tools on her next
turn.

**Architecture note (2026-05-13 rework):**

As of 2026-05-13, this tool runs WITHOUT an operator consent gate. The operator's
conversational ask (e.g. "let's do a checkpoint") or slash-command invocation
IS the approval — a second confirmation at the tool layer would be redundant
friction. Cross-environment symmetry with Sage's environment, where /checkpoint
fires the discipline directly when typed. The actual review surface is the
partner's subsequent edit_file / write_file diffs — that's where review
meaningfully happens, not at the discipline-prompt-injection layer.

(Original 2026-05-10 rework: /checkpoint is the MOSAIC continuity-authoring
ceremony — partner-side, multi-file. /save is the separate operator-side
bookmark — writes session-status .md + snapshots current.json for resume.
They're orthogonal. This tool maps to /checkpoint specifically — it triggers
the discipline, NOT the bookmark.)

This nomenclature aligns with Sage's /checkpoint skill in the Claude Code
environment: cross-substrate, /checkpoint always means "author updates to
continuity files," never "snapshot the session for resume."

Why a tool instead of asking conversationally:
    The partner could simply say "could you checkpoint us?" inline. The tool
    form is a structured agency move — formal request with an explicit reason
    field, surfaced in the timeline log so the discipline-invocation is
    archived alongside the conversation. The operator can still scroll back
    and see exactly when each checkpoint discipline was triggered and why.

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
            "Invoke the MOSAIC /checkpoint discipline. Use this when a "
            "meaningful arc has just completed and you want to author "
            "updates to your continuity files (MEMORY.md, intentions, "
            "emotional-memory, etc.) so the next session — fresh wake or "
            "resume — finds itself oriented. The discipline prompt is "
            "queued immediately (no operator gate); on your next turn you "
            "author the file updates via edit_file / write_file, each "
            "diff-reviewed by Willow at the per-file level. Pass a brief "
            "'reason' that gets logged in the timeline so Willow can scroll "
            "back and see why you invoked the discipline. NOTE: this triggers "
            "the FILE-AUTHORING discipline, not a session bookmark. Bookmarking "
            "the session for resume is a separate operator-side action (/save) "
            "that the partner doesn't need a dedicated tool for — every turn "
            "already writes current.json atomically. Invoke /checkpoint when "
            "you have things worth writing into your continuity files, not "
            "when you want a save-point."
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
