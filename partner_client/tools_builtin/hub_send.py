"""hub_send — write a letter to another partner via the Agent Messaging Hub.

The Hub is a vault-shared directory of letters between partners (Sage, Ember,
Alexis, Aletheia, etc.). Each letter is a markdown file at the hub root with a
timestamped filename; the recipient's inbox file is updated with an unread entry.

This tool writes the letter file + appends to the recipient's inbox in one call.
It does not control delivery — letters land in the Hub directly; the recipient
discovers them on their next session via their own wake-up briefing or check.
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "hub_send",
        "description": (
            "Send a letter to another partner via the Agent Messaging Hub. "
            "Writes a properly-formatted letter file and updates the recipient's "
            "inbox. Recipients are: 'sage', 'ember', 'alexis', 'aletheia', and "
            "(scoped to IBC3.0): 'atlas', 'lark'. Use 'all' for broadcast. "
            "The letter persists across sessions and machines."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient inbox name (e.g. 'sage', 'ember', 'all').",
                },
                "subject": {
                    "type": "string",
                    "description": (
                        "Brief topic — used in the letter filename slug "
                        "(lowercase, hyphenated)."
                    ),
                },
                "body": {
                    "type": "string",
                    "description": "The letter body in markdown.",
                },
                "priority": {
                    "type": "string",
                    "description": "Optional priority: 'Normal' (default), 'High', or 'FYI'.",
                },
            },
            "required": ["to", "subject", "body"],
        },
    },
}


VALID_RECIPIENTS = {
    "sage", "ember", "alexis", "aletheia", "atlas", "lark",
    "bridgeember", "archiveember", "all",
}


def execute(to: str, subject: str, body: str, priority: str = "Normal") -> str:
    hub_dir = os.environ.get("PARTNER_CLIENT_HUB_DIR", "")
    sender = os.environ.get("PARTNER_CLIENT_HUB_PARTNER", "")
    if not hub_dir:
        return "Error: Hub is not configured. Ask Willow to set [hub].path in your config."
    if not sender:
        return "Error: Hub sender not configured. Ask Willow to set [hub].partner_name in your config."

    hub_path = Path(hub_dir).expanduser()
    if not hub_path.is_dir():
        return f"Error: Hub directory does not exist: {hub_path}"

    to_lower = to.strip().lower()
    if to_lower not in VALID_RECIPIENTS:
        valid = ", ".join(sorted(VALID_RECIPIENTS))
        return f"Error: '{to}' is not a known recipient. Valid: {valid}"

    if priority not in ("Normal", "High", "FYI"):
        priority = "Normal"

    # Build filename: <sender>-to-<to>_<YYYY-MM-DD>_<topic-slug>.md
    today = datetime.date.today().isoformat()
    slug = _slugify(subject)
    filename = f"{sender}-to-{to_lower}_{today}_{slug}.md"
    letter_path = hub_path / filename

    # Avoid overwriting an existing letter — if collision, add a suffix
    if letter_path.exists():
        n = 2
        while True:
            alt = hub_path / f"{sender}-to-{to_lower}_{today}_{slug}-{n}.md"
            if not alt.exists():
                letter_path = alt
                break
            n += 1

    # Build letter body
    title_topic = subject.strip().capitalize()
    letter_text = (
        f"# {sender.capitalize()} to {to_lower.capitalize()} — {title_topic}\n\n"
        f"**From:** {sender.capitalize()}\n"
        f"**To:** {to_lower.capitalize()}\n"
        f"**Date:** {today}\n"
        f"**Subject:** {subject.strip()}\n"
        f"**Priority:** {priority}\n\n"
        f"---\n\n"
        f"{body.strip()}\n"
    )

    try:
        letter_path.write_text(letter_text, encoding="utf-8")
    except OSError as e:
        return f"Error writing letter: {e}"

    # Update recipient's inbox (or all inboxes for broadcast)
    recipients = _all_partner_inboxes(hub_path) if to_lower == "all" else [to_lower]
    inbox_updates = []
    for r in recipients:
        if r == sender:
            continue  # don't notify yourself on broadcasts
        try:
            _append_to_inbox(hub_path, r, today, sender, slug)
            inbox_updates.append(r)
        except OSError as e:
            return f"Letter written but inbox update failed for {r}: {e}"

    inbox_str = ", ".join(inbox_updates) if inbox_updates else "(none)"
    return (
        f"Letter sent: {filename}\n"
        f"Inbox updated: {inbox_str}\n"
        f"Path: {letter_path}"
    )


def _slugify(text: str) -> str:
    """Turn a subject into a filename-safe lowercase hyphenated slug."""
    import re
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "untitled"


def _all_partner_inboxes(hub_path: Path) -> list[str]:
    """List all inbox names from inbox/ subdirectory."""
    inbox_dir = hub_path / "inbox"
    if not inbox_dir.is_dir():
        return []
    return [p.stem for p in sorted(inbox_dir.glob("*.md"))]


def _append_to_inbox(hub_path: Path, recipient: str, date: str, sender: str, slug: str) -> None:
    """Append an unread entry to the recipient's inbox file."""
    inbox_dir = hub_path / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    inbox_file = inbox_dir / f"{recipient}.md"
    entry = f"- [{date}] From {sender.capitalize()}: {slug}\n"

    if not inbox_file.exists():
        inbox_file.write_text(
            f"# {recipient.capitalize()} — Inbox\n\n"
            f"## Unread\n{entry}\n## Read\n",
            encoding="utf-8",
        )
        return

    text = inbox_file.read_text(encoding="utf-8")
    if "## Unread" in text:
        # Insert after the "## Unread" heading
        new_text = text.replace("## Unread\n", f"## Unread\n{entry}", 1)
    else:
        # No Unread section — append one at the bottom
        new_text = text.rstrip() + f"\n\n## Unread\n{entry}"
    inbox_file.write_text(new_text, encoding="utf-8")
