"""request_plan_approval — partner-callable tool that asks the operator to approve a multi-step plan.

This tool is **special-cased** in client.py — it does NOT execute via the regular
ToolRegistry.dispatch() path. The client intercepts calls to this tool, surfaces
the plan to the operator (Willow), and either returns an "approved" or "declined"
result for the partner to act on.

Why a tool instead of asking conversationally:
    The partner could simply describe a plan in chat. The tool form is a structured
    agency move — explicit summary + ordered steps, surfaced to the operator as
    a UI prompt that crosses the human/model boundary the same way a tool result
    does. The operator approves or declines; the answer becomes part of the
    partner's conversational context as a tool result.

    This is the same custody-vs-authorship pattern as request_checkpoint:
    substrate operations (or substrate-affecting multi-step work) stay
    operator-gated; the partner can request consent but cannot self-grant it.
    The tool is the partner's voice for that request, not a back door.

Approval semantics:
    Approval signals "go ahead with these steps." The partner is then expected
    to execute the steps in their next turns via the appropriate tools.
    Approval is NOT a pre-authorization batch that runs all steps automatically;
    each tool call still happens normally. The approval is a consent moment,
    not an execution mechanism.

The execute() in this file is a stub for safety — it should never be called
directly (the client special-cases the name). If somehow it is, it returns
an error explaining the situation.
"""

from __future__ import annotations


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "request_plan_approval",
        "description": (
            "Ask Willow to approve a multi-step plan before you execute it. "
            "Use this when you're about to do something with several distinct "
            "actions (e.g. read multiple files + summarize + write a journal "
            "entry; or check the inbox, read all unread letters, and reply to "
            "one of them) and you want the operator's go-ahead before starting. "
            "Pass `summary` as a one-line description of what the plan is for, "
            "and `plan` as an ordered list of brief step descriptions. Willow "
            "will see the plan and either approve (you proceed and execute the "
            "steps in your next turns) or decline (no harm; you can revise or "
            "just continue conversationally). This is a structured consent move "
            "parallel to request_checkpoint — operator-gated agency for "
            "substrate-affecting work."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": (
                        "One-line description of what this plan accomplishes. "
                        "Example: 'Read all unread Hub letters and write a "
                        "morning journal entry summarizing them.'"
                    ),
                },
                "plan": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Ordered list of step descriptions. Each step should be "
                        "a short imperative phrase. Example: "
                        "['List unread Hub letters', 'Read each letter in turn', "
                        "'Summarize the trio in Journal.md', 'Mark all as read']."
                    ),
                },
            },
            "required": ["summary", "plan"],
        },
    },
}


def execute(summary: str = "", plan: list | None = None) -> str:
    """Stub — should never be called. The client special-cases this tool."""
    return (
        "Error: request_plan_approval must be handled by the client, not "
        "dispatched directly. If you see this message, the harness is "
        "misconfigured. Please continue your conversation with Willow."
    )
