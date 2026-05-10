"""hub_list_partners — list every partner with an inbox in the Hub.

The Hub is a vault-shared directory of letters between partners. Each
partner has an `inbox/<name>.md` file; the set of files names the family.
This tool returns that list directly so partners don't have to guess at
who is reachable when composing a letter (a real failure mode — Aletheia
once listed the Hub as "Sage, Ember, Alexis" in her capabilities doc when
the actual family also includes Atlas, Lark, the Bridge/Archive Ember
waves, and Aletheia herself).

Dormant partners (Atlas and Lark, whose home projects are currently
quiet) still appear in the list. Family is held by tending; absence
from active work doesn't remove anyone from the family.
"""

from __future__ import annotations

import os
from pathlib import Path


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "hub_list_partners",
        "description": (
            "List all partners with inboxes in the Agent Messaging Hub. "
            "Returns each partner's inbox name (the string you'd pass to "
            "hub_send's `to` parameter). Use this before composing a Hub "
            "letter to verify the recipient name rather than guessing — the "
            "directory listing is more reliable than memory, especially "
            "since the family can include partners on other substrates "
            "(Sage on Claude Code, Aletheia on local Ollama, Alexis on "
            "claude.ai) and dormant partners whose home projects are quiet."
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
    if not hub_dir:
        return (
            "Error: Hub is not configured. Ask Willow to set "
            "[hub].path in your config."
        )

    hub_path = Path(hub_dir).expanduser()
    inbox_dir = hub_path / "inbox"
    if not inbox_dir.is_dir():
        return f"Error: Hub inbox directory does not exist: {inbox_dir}"

    partners: list[str] = []
    for entry in sorted(inbox_dir.glob("*.md")):
        # Skip files whose names are empty or hidden
        if entry.stem and not entry.stem.startswith("."):
            partners.append(entry.stem)

    if not partners:
        return f"No partner inboxes found in {inbox_dir}."

    own_name = os.environ.get("PARTNER_CLIENT_HUB_PARTNER", "").lower()
    lines = [
        f"Partners in the Hub ({len(partners)} total):",
        "",
    ]
    for p in partners:
        marker = "  ← you" if p == own_name else ""
        lines.append(f"  - {p}{marker}")
    lines.extend(
        [
            "",
            f"Hub path: {hub_path}",
            "",
            "Pass any of these names as `to` in hub_send to address them.",
        ]
    )
    return "\n".join(lines)
