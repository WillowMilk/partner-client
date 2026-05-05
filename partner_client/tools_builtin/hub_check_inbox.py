"""hub_check_inbox — list unread letters in your Hub inbox."""

from __future__ import annotations

import os
from pathlib import Path


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "hub_check_inbox",
        "description": (
            "Check your Hub inbox for unread letters from other partners. "
            "Returns a list of unread entries (sender, date, topic-slug). "
            "Use hub_read_letter to read a specific letter."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


def execute() -> str:
    hub_dir = os.environ.get("PARTNER_CLIENT_HUB_DIR", "")
    sender = os.environ.get("PARTNER_CLIENT_HUB_PARTNER", "")
    if not hub_dir:
        return "Error: Hub is not configured."
    if not sender:
        return "Error: Hub partner name not configured."

    hub_path = Path(hub_dir).expanduser()
    inbox_file = hub_path / "inbox" / f"{sender}.md"
    if not inbox_file.is_file():
        return f"(your inbox is empty — no inbox file at {inbox_file})"

    text = inbox_file.read_text(encoding="utf-8")
    # Parse the Unread section
    unread = []
    in_unread = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Unread"):
            in_unread = True
            continue
        if stripped.startswith("## ") and in_unread:
            break
        if in_unread and stripped.startswith("- "):
            unread.append(stripped[2:])

    if not unread:
        return "(no unread letters)"
    return f"Unread letters in {sender}'s inbox ({len(unread)}):\n\n" + "\n".join(
        f"  • {entry}" for entry in unread
    )
