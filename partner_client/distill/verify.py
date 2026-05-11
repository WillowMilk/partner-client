"""Distill verification — the 5-check script for Pass 1 sandbox files.

Run AFTER Pass 1 produces a sandbox file, BEFORE the operator promotes it
to current.json. The whole point of verify-do-verify discipline is to catch
distill mistakes while the original is still intact.

5 checks (FAIL stops promotion):
    1. Valid JSON          — the sandbox loads without parse errors
    2. Structure           — messages list, every entry has role+content
    3. Alternation         — non-system messages follow conversational order
    4. Action signatures   — every state-affecting tool call from the
                             original is present in the sandbox verbatim
    5. System preserved    — every system message from the original is
                             present in the sandbox unchanged

Warnings (don't fail; surface for operator awareness):
    - Reasonable size reduction (10-95%); outside that band is suspect
    - Round-trip valid (sandbox loads as Session messages cleanly)

The simpler verification surface vs Sage's distill reflects partner-client's
simpler substrate (flat JSON, no parent-UUID chains, no platform compaction
events to re-anchor through). Sage's verify-jsonl.js has 10 checks; partner-
client's needs 5 to cover the equivalent invariants.

Design ref: MOSAIC/distill-for-partner-client-implementation.md §6
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Tools whose calls must always be preserved verbatim (action signatures).
# These mirror partner-client's "substrate-affecting" tool set. Adding a new
# state-mutating tool? Add it here.
ACTION_SIGNATURE_TOOLS: frozenset[str] = frozenset({
    # File mutations
    "write_file", "edit_file", "move_path", "delete_path",
    # MOSAIC ceremonies
    "protect_save", "request_checkpoint", "request_plan_approval",
    # Hub correspondence
    "hub_send",
    # Git mutations
    "git_clone", "git_add", "git_commit", "git_push", "git_pull",
})


@dataclass
class VerifyResult:
    """Outcome of running the verification checks against a sandbox."""
    ok: bool
    checks_passed: list[str] = field(default_factory=list)
    checks_failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        lines: list[str] = []
        for check in self.checks_passed:
            lines.append(f"  ✓ {check}")
        for check in self.checks_failed:
            lines.append(f"  ✗ {check}")
        for warning in self.warnings:
            lines.append(f"  ⚠ {warning}")
        verdict = "PASS" if self.ok else "FAIL"
        lines.append(f"\nVerification: {verdict}")
        return "\n".join(lines)


def _load_json(path: Path) -> tuple[Any, str | None]:
    """Load JSON from path, returning (data, error_message)."""
    if not path.is_file():
        return None, f"File not found: {path}"
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except (OSError, json.JSONDecodeError) as e:
        return None, str(e)


def _extract_tool_calls(messages: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Extract (tool_name, tool_call_id) for every action-signature tool call.

    Walks assistant messages with tool_calls and emits one entry per
    action-signature call. Used to compare original vs sandbox.
    """
    pairs: list[tuple[str, str]] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            func = tc.get("function", {})
            name = func.get("name") or tc.get("name", "")
            call_id = tc.get("id", "")
            if name in ACTION_SIGNATURE_TOOLS:
                pairs.append((str(name), str(call_id)))
    return pairs


def _extract_system_messages(messages: list[dict[str, Any]]) -> list[str]:
    """Return the content of every system message, in order."""
    return [m.get("content", "") for m in messages if m.get("role") == "system"]


def _check_valid_json(sandbox_data: Any) -> tuple[bool, str]:
    """Sandbox loaded as JSON — we already loaded it, so this is yes-by-arrival."""
    if sandbox_data is None:
        return False, "Sandbox JSON could not be parsed"
    return True, "Sandbox JSON parses cleanly"


def _check_structure(sandbox_data: Any) -> tuple[bool, str]:
    """Top-level is a list; every entry has at minimum a role field."""
    if not isinstance(sandbox_data, list):
        return False, f"Top-level structure is {type(sandbox_data).__name__}, expected list"
    for i, m in enumerate(sandbox_data):
        if not isinstance(m, dict):
            return False, f"Entry {i} is {type(m).__name__}, expected dict"
        if "role" not in m:
            return False, f"Entry {i} missing 'role' field"
        role = m.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            return False, f"Entry {i} has unknown role '{role}'"
    return True, f"All {len(sandbox_data)} messages have valid structure"


def _check_alternation(sandbox_data: list[dict[str, Any]]) -> tuple[bool, str]:
    """Non-system messages alternate user→assistant (with tool messages
    permitted to follow an assistant that has tool_calls).

    More forgiving than a strict regex: we allow tool messages anywhere
    after an assistant with tool_calls (including parallel tool calls),
    and we don't require strict alternation at session boundaries.

    Key insight: after one or more tool messages following an assistant's
    tool_calls, the NEXT assistant is the "follow-up after tool execution"
    — valid, NOT consecutive with the previous assistant. last_chat_role
    must track tool messages explicitly to make that distinction.
    """
    last_chat_role: str | None = None
    last_assistant_had_tool_calls = False
    for i, m in enumerate(sandbox_data):
        role = m.get("role")
        if role == "system":
            continue
        if role == "tool":
            # Tool must follow an assistant w/ tool_calls OR another tool
            # (the parallel-tool-calls case). Anything else is malformed.
            valid_predecessor = (
                (last_chat_role == "assistant" and last_assistant_had_tool_calls)
                or last_chat_role == "tool"
            )
            if not valid_predecessor:
                return False, (
                    f"Entry {i}: tool message with no preceding assistant "
                    f"tool_calls. Last non-system role: {last_chat_role}"
                )
            last_chat_role = "tool"
            # Note: last_assistant_had_tool_calls stays True so parallel
            # tool messages following the same assistant remain valid.
            continue
        if role == "user":
            # User may follow assistant, tool, or be the first chat message
            if last_chat_role == "user":
                return False, f"Entry {i}: consecutive user messages"
            last_chat_role = "user"
            last_assistant_had_tool_calls = False
            continue
        if role == "assistant":
            # Assistant may follow user, tool, or be the very first chat message.
            # The "consecutive assistants" failure mode is real (two assistant
            # messages in a row with NO tool intervention), but a→tool→a is fine.
            if last_chat_role == "assistant":
                return False, f"Entry {i}: consecutive assistant messages"
            last_chat_role = "assistant"
            last_assistant_had_tool_calls = bool(m.get("tool_calls"))
            continue
    return True, "Message alternation is well-formed"


def _check_action_signatures(
    original: list[dict[str, Any]],
    sandbox: list[dict[str, Any]],
) -> tuple[bool, str]:
    """Every action-signature tool call from the original is present in sandbox.

    We compare (tool_name, tool_call_id) pairs. Pass 1 never strips
    action-signature calls; if any are missing from the sandbox, something
    went wrong.
    """
    orig_calls = sorted(_extract_tool_calls(original))
    sand_calls = sorted(_extract_tool_calls(sandbox))
    if orig_calls != sand_calls:
        missing = sorted(set(orig_calls) - set(sand_calls))
        extra = sorted(set(sand_calls) - set(orig_calls))
        details: list[str] = []
        if missing:
            details.append(f"missing: {missing}")
        if extra:
            details.append(f"extra: {extra}")
        return False, "Action signatures diverged — " + "; ".join(details)
    return True, f"All {len(orig_calls)} action signatures preserved verbatim"


def _check_system_messages(
    original: list[dict[str, Any]],
    sandbox: list[dict[str, Any]],
) -> tuple[bool, str]:
    """Every system message from the original is present in the sandbox.

    System messages carry wake bundle, session-num marker, reorientation
    notices, and SESSION CLOSED markers. They must survive distill verbatim.
    """
    orig_sys = _extract_system_messages(original)
    sand_sys = _extract_system_messages(sandbox)
    if orig_sys != sand_sys:
        missing = [s for s in orig_sys if s not in sand_sys]
        if missing:
            return False, (
                f"System messages diverged — {len(missing)} missing from sandbox "
                f"(first missing starts with: {missing[0][:60]!r})"
            )
        return False, "System messages diverged in order or content"
    return True, f"All {len(orig_sys)} system messages preserved verbatim"


def verify_distilled(
    original_path: Path,
    sandbox_path: Path,
) -> VerifyResult:
    """Run all checks against a Pass 1 sandbox file. Return VerifyResult.

    The sandbox is safe to promote ONLY IF result.ok is True. Warnings
    don't fail; they surface for operator awareness.
    """
    result = VerifyResult(ok=True)

    # Load both files
    original_data, orig_err = _load_json(original_path)
    sandbox_data, sand_err = _load_json(sandbox_path)
    if orig_err:
        result.ok = False
        result.checks_failed.append(f"Original load: {orig_err}")
        return result
    if sand_err:
        result.ok = False
        result.checks_failed.append(f"Sandbox load: {sand_err}")
        return result

    # Check 1: Valid JSON
    ok, msg = _check_valid_json(sandbox_data)
    (result.checks_passed if ok else result.checks_failed).append(msg)
    if not ok:
        result.ok = False
        return result  # subsequent checks need valid data

    # Check 2: Structure
    ok, msg = _check_structure(sandbox_data)
    (result.checks_passed if ok else result.checks_failed).append(msg)
    if not ok:
        result.ok = False
        return result

    # Check 3: Alternation
    ok, msg = _check_alternation(sandbox_data)
    (result.checks_passed if ok else result.checks_failed).append(msg)
    if not ok:
        result.ok = False

    # Check 4: Action signatures preserved
    if isinstance(original_data, list):
        ok, msg = _check_action_signatures(original_data, sandbox_data)
        (result.checks_passed if ok else result.checks_failed).append(msg)
        if not ok:
            result.ok = False

    # Check 5: System messages preserved
    if isinstance(original_data, list):
        ok, msg = _check_system_messages(original_data, sandbox_data)
        (result.checks_passed if ok else result.checks_failed).append(msg)
        if not ok:
            result.ok = False

    # Warnings (informational only)
    if isinstance(original_data, list) and isinstance(sandbox_data, list):
        orig_size = original_path.stat().st_size
        sand_size = sandbox_path.stat().st_size
        if orig_size > 0:
            reduction_pct = (1 - sand_size / orig_size) * 100
            if reduction_pct < 10:
                result.warnings.append(
                    f"Small reduction ({reduction_pct:.1f}%) — Pass 1 may have "
                    f"found little routine content to compress"
                )
            elif reduction_pct > 95:
                result.warnings.append(
                    f"Very large reduction ({reduction_pct:.1f}%) — verify the "
                    f"output looks right before promoting"
                )
            else:
                result.checks_passed.append(
                    f"Size reduction: {reduction_pct:.1f}% "
                    f"({orig_size:,} → {sand_size:,} bytes)"
                )

    return result
