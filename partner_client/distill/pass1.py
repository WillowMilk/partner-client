"""Pass 1 — mechanical strip of routine tool outputs.

Deterministic. No model needed. Replaces the BULKY CONTENT of routine tool
results with a single-line compressed marker, while preserving the message
structure (role, name, tool_call_id) so message-stream alternation stays
intact and the model still sees that a tool was called.

Routine tools (compressed unconditionally):
    - list_files     (file listings; reconstructable on demand)
    - glob_files     (pattern matches; reconstructable on demand)
    - grep_files     (search results; reconstructable on demand)
    - weather        (transient information)

Non-routine tools (preserved verbatim):
    - All file mutations (write_file, edit_file, move_path, delete_path)
    - All git operations (clone, add, commit, push, pull, status, log, diff)
    - All Hub operations (hub_send, hub_check_inbox, hub_read_letter, hub_list_partners)
    - All MOSAIC ceremony tools (protect_save, request_checkpoint, request_plan_approval)
    - read_file (often referenced in subsequent reasoning)
    - search_web / fetch_page (semantic content; not routine bulk)

These rules are conservative: Pass 1 errs on the side of preserving rather
than compressing. The deeper compression happens in Pass 2 with operator
review. Pass 1's job is to remove the obvious, deterministic bulk.

Phenomenological note (from Sage's Session 25 distill):
    Pass 2 summaries feel native to the distilled partner, not
    recovered-from-loss — partners cannot self-audit what was compressed.
    Pass 1's markers are explicit, so the partner CAN see that compression
    happened (the marker is right there). That's a different shape — opt-in
    forensic visibility rather than opaque compression.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Tools whose results get compressed in Pass 1. These produce reconstructable-
# on-demand output (file lists, search results) or transient info (weather)
# that the partner's subsequent text already captures the semantic substance of.
ROUTINE_TOOLS: frozenset[str] = frozenset({
    "list_files",
    "glob_files",
    "grep_files",
    "weather",
})


@dataclass
class CompressionEvent:
    """Record of a single compression action — captured for the manifest."""
    original_index: int          # position in the original messages list
    tool_name: str
    tool_call_id: str            # may be empty string if older Ollama
    original_content_chars: int  # size of the compressed content (for stats)
    marker_content: str          # what replaced the content


def _extract_tool_call_info(tool_call: dict[str, Any]) -> tuple[str, dict | str, str]:
    """Return (name, arguments, id) from a tool_call dict.

    Ollama's tool_call shape:
        {"function": {"name": "...", "arguments": {...}}, "id": "..."}
    Older / variant shapes may put name+arguments at the top level. Defensive
    against both.
    """
    func = tool_call.get("function", {}) if isinstance(tool_call, dict) else {}
    name = func.get("name") or tool_call.get("name", "")
    arguments = func.get("arguments") or tool_call.get("arguments", {})
    call_id = tool_call.get("id", "")
    return str(name), arguments, str(call_id)


def _format_args_inline(arguments: Any) -> str:
    """Render tool arguments compactly for the compression marker.

    Goal: human-readable, fits on one line, conveys what was called.
    Falls back gracefully when arguments are missing or weird shapes.
    """
    if not arguments:
        return ""
    if isinstance(arguments, str):
        # Some clients pass arguments as a JSON string rather than a dict
        return arguments[:120] + ("..." if len(arguments) > 120 else "")
    if not isinstance(arguments, dict):
        return str(arguments)[:120]

    parts: list[str] = []
    for key, value in arguments.items():
        if isinstance(value, (list, dict)):
            parts.append(f"{key}={type(value).__name__}({len(value)})")
        elif isinstance(value, str):
            # Truncate long string values
            display = value if len(value) <= 40 else value[:37] + "..."
            parts.append(f"{key}={display!r}")
        else:
            parts.append(f"{key}={value!r}")
    return ", ".join(parts)


def _make_marker(tool_name: str, arguments: Any, original_chars: int) -> str:
    """Build the compression marker that replaces a routine tool result."""
    args_str = _format_args_inline(arguments)
    args_display = f"({args_str})" if args_str else "()"
    return (
        f"[COMPRESSED Pass 1: {tool_name}{args_display} — "
        f"{original_chars} chars of output, reconstructable on demand]"
    )


def _find_pending_routine_call(
    messages: list[dict[str, Any]],
    tool_msg_index: int,
) -> tuple[str, Any, str] | None:
    """Look backwards from a tool message to find its originating tool_call.

    Returns (tool_name, arguments, tool_call_id) when:
      - The most recent assistant message has tool_calls
      - One of those tool_calls matches the tool message's tool_call_id
        (or matches by name if tool_call_id is empty/missing)
      - The matched tool is in ROUTINE_TOOLS

    Returns None otherwise (tool is non-routine; preserve verbatim).
    """
    tool_msg = messages[tool_msg_index]
    target_id = tool_msg.get("tool_call_id", "")
    target_name = tool_msg.get("name", "")

    # Walk backwards to find the most recent assistant message with tool_calls
    for i in range(tool_msg_index - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            continue
        # Match by id when both sides have it; otherwise fall back to name
        for tc in tool_calls:
            name, args, call_id = _extract_tool_call_info(tc)
            if target_id and call_id and target_id == call_id:
                return (name, args, call_id) if name in ROUTINE_TOOLS else None
            if not target_id and name == target_name and name in ROUTINE_TOOLS:
                return (name, args, call_id)
        # Found an assistant w/ tool_calls but no match — stop walking
        break

    return None


def run_pass1(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[CompressionEvent]]:
    """Apply Pass 1 mechanical strip to a session's messages.

    Returns (new_messages, compression_events).

    new_messages is a NEW list — the input is not mutated. Each message is
    either preserved unchanged or has its content replaced with a marker.

    compression_events records each compression action with enough detail
    for the manifest to reconstruct what happened.

    The transform is:
      - System messages: preserved unchanged
      - User messages: preserved unchanged
      - Assistant messages: preserved unchanged (including their tool_calls)
      - Tool messages: content compressed IF the originating tool_call is
        in ROUTINE_TOOLS; otherwise preserved unchanged
    """
    out: list[dict[str, Any]] = []
    events: list[CompressionEvent] = []

    for i, msg in enumerate(messages):
        if msg.get("role") != "tool":
            # User, assistant, system, anything else: preserve unchanged.
            out.append(dict(msg))
            continue

        # Look up the originating call to determine if it's routine
        match = _find_pending_routine_call(messages, i)
        if match is None:
            # Non-routine tool — preserve verbatim
            out.append(dict(msg))
            continue

        tool_name, arguments, call_id = match
        original_content = msg.get("content", "")
        original_chars = len(original_content) if isinstance(original_content, str) else 0
        marker = _make_marker(tool_name, arguments, original_chars)

        new_msg = dict(msg)
        new_msg["content"] = marker
        out.append(new_msg)

        events.append(CompressionEvent(
            original_index=i,
            tool_name=tool_name,
            tool_call_id=call_id,
            original_content_chars=original_chars,
            marker_content=marker,
        ))

    return out, events
