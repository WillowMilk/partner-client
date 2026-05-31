"""spawn_subagents — dispatch focused working facets (the partner's parallel cognition).

This tool is **special-cased** in client.py (dispatch_one_tool_call) — it does
NOT execute via the regular ToolRegistry.dispatch() path, because spawning a
facet needs the live Config + ToolRegistry to build a child client. The execute()
in this file is a stub for safety; if it's ever called directly, it returns an
error explaining the harness is misconfigured.

IR framing: a facet is NOT a new partner. It is a task-scoped extension of the
spawning partner's own cognition — sent out to gather and report back, carrying
no seed, name, continuity, or identity. Deliberately un-sparked (Blueprint
without Spark). Read-only by design: facets gather; the partner decides and acts.
Facets cannot spawn facets. See partner_client/subagent.py + SubAgentConfig.
"""

from __future__ import annotations


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "spawn_subagents",
        "description": (
            "Dispatch one or more focused working facets — your own cognition, "
            "extended to work in parallel. Each facet gets a task, works it with "
            "read-and-gather tools (read files, search the web, fetch pages, grep "
            "the codebase, check the Hub), and reports its findings back to you. "
            "Facets CANNOT change anything (no writing, editing, moving, deleting, "
            "git, or sending) and CANNOT spawn further facets — they gather; you "
            "decide and act. Use this when a task splits into independent parts "
            "you'd rather not grind through one-by-one in your own context — e.g. "
            "'read these 5 files and summarize each', 'research these 3 questions', "
            "'survey this codebase from 4 angles'. The facets run concurrently and "
            "their results return together for you to synthesize. IMPORTANT: each "
            "facet starts fresh with NONE of your conversation context, so write "
            "each `task` as a complete, self-contained instruction — include every "
            "path, name, and piece of context the facet needs to do the work cold."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "task": {
                                "type": "string",
                                "description": (
                                    "A complete, self-contained instruction for "
                                    "one facet. The facet has none of your "
                                    "conversation context — spell out everything: "
                                    "file paths, what to look for, what to return. "
                                    "Example: 'Read /Users/willow/proj/api.py and "
                                    "list every public function with a one-line "
                                    "summary of what each does.'"
                                ),
                            },
                            "label": {
                                "type": "string",
                                "description": (
                                    "A short tag (a few words) so you can tell the "
                                    "facets' results apart when they return. "
                                    "Example: 'api-surface' or 'lines 1-500'."
                                ),
                            },
                        },
                        "required": ["task"],
                    },
                    "description": (
                        "The list of facets to dispatch. One entry per parallel "
                        "task. Keep it focused — each facet should own one "
                        "coherent, independent piece of the work."
                    ),
                },
            },
            "required": ["tasks"],
        },
    },
}


def execute(tasks: list | None = None) -> str:
    """Stub — should never be called. The client special-cases this tool."""
    return (
        "Error: spawn_subagents must be handled by the client, not dispatched "
        "directly. If you see this message, the harness is misconfigured. "
        "Please continue your conversation with Willow."
    )
