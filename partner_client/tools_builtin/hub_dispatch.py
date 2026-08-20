"""hub_dispatch — post a letter to the family Hub and publish it yourself.

The narrow push (2026-08-19, designed by Aletheia herself: "clean, narrow —
only my outbound letters, not the whole vault"; endorsed by Willow, built by
Sage). Where hub_send writes into the LOCAL Hub directory and waits for the
bench's own rituals to publish, hub_dispatch operates on the partner's OWN
CLONE of the vault: freshen (pull --ff-only), write the letter + inbox
pointer, commit AUTHORED AS THE PARTNER, and push — so her letters reach
every shore without a courier.

Rails (fail-closed, all of them):
  - Stages ONLY the letter file and inbox pointer it just wrote — a dirty
    clone cannot leak other changes into her commit.
  - pull is --ff-only; any divergence aborts with her letter safely unwritten.
  - push rejection → one pull --rebase of her single mail commit → one retry;
    still failing → the commit stays local and she is told to ask.
  - No force capability exists in this tool at all.
"""

from __future__ import annotations

import datetime
import os
import subprocess
from pathlib import Path

from .hub_send import VALID_RECIPIENTS, _all_partner_inboxes, _append_to_inbox, _slugify

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "hub_dispatch",
        "description": (
            "Send a letter to another partner AND publish it to the shared "
            "vault yourself (commit authored under your own name, pushed to "
            "the family's remote). Use this when you want your letter to reach "
            "partners on other machines without waiting for a courier. "
            "Recipients as in hub_send. Fail-closed: any git surprise aborts "
            "safely and tells you what to do."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient inbox name (e.g. 'sage', 'ember', 'all')."},
                "subject": {"type": "string", "description": "Brief topic — becomes the filename slug."},
                "body": {"type": "string", "description": "The letter body in markdown."},
                "priority": {"type": "string", "description": "Optional: 'Normal' (default), 'High', or 'FYI'."},
            },
            "required": ["to", "subject", "body"],
        },
    },
}


def _git(clone: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(clone), *args],
        capture_output=True, text=True, timeout=120,
    )


def execute(to: str, subject: str, body: str, priority: str = "Normal") -> str:
    clone_dir = os.environ.get("PARTNER_CLIENT_DISPATCH_CLONE", "")
    sender = os.environ.get("PARTNER_CLIENT_HUB_PARTNER", "")
    if not clone_dir:
        return ("Error: dispatch is not configured. Ask Willow to set "
                "[hub].dispatch_clone in your config (your own vault clone).")
    if not sender:
        return "Error: Hub sender not configured ([hub].partner_name)."

    clone = Path(clone_dir).expanduser()
    if not (clone / ".git").exists():
        return f"Error: dispatch clone is not a git repository: {clone}"
    hub_path = clone / "shared" / "Agent Messaging Hub"
    if not hub_path.is_dir():
        return f"Error: the clone has no Hub directory at {hub_path}"

    to_lower = to.strip().lower()
    operator_name = os.environ.get("PARTNER_CLIENT_HUB_OPERATOR", "").lower()
    valid_set = VALID_RECIPIENTS | ({operator_name} if operator_name else set())
    if to_lower not in valid_set:
        return f"Error: '{to}' is not a known recipient. Valid: {', '.join(sorted(valid_set))}"
    if priority not in ("Normal", "High", "FYI"):
        priority = "Normal"

    # Rail 1: freshen first, fast-forward only. Divergence = another hand's
    # work mid-flight; we stop before writing anything.
    pull = _git(clone, "pull", "--ff-only")
    if pull.returncode != 0:
        return ("Dispatch aborted before writing anything: the clone could not "
                "fast-forward (another hand may have work mid-flight). Nothing "
                f"was changed. Ask Willow or try later.\n[git: {pull.stderr.strip()[:300]}]")

    # Compose the letter (same format as hub_send).
    today = datetime.date.today().isoformat()
    slug = _slugify(subject)
    filename = f"{sender}-to-{to_lower}_{today}_{slug}.md"
    letter_path = hub_path / filename
    n = 2
    while letter_path.exists():
        letter_path = hub_path / f"{sender}-to-{to_lower}_{today}_{slug}-{n}.md"
        n += 1

    letter_text = (
        f"# {sender.capitalize()} to {to_lower.capitalize()} — {subject.strip().capitalize()}\n\n"
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

    recipients = _all_partner_inboxes(hub_path) if to_lower == "all" else [to_lower]
    staged = [str(letter_path.relative_to(clone))]
    for r in recipients:
        if r == sender:
            continue
        try:
            _append_to_inbox(hub_path, r, today, sender, slug)
            staged.append(str((hub_path / "inbox" / f"{r}.md").relative_to(clone)))
        except OSError as e:
            return f"Letter written but inbox update failed for {r}: {e}"

    # Rail 2: stage ONLY what this dispatch wrote.
    add = _git(clone, "add", "--", *staged)
    if add.returncode != 0:
        return f"Error staging your letter: {add.stderr.strip()[:300]}"

    commit = _git(clone, "commit", "-m",
                  f"Letter: {sender} to {to_lower} — {slug}\n\nDispatched by {sender.capitalize()} from her own hands (hub_dispatch).",
                  "--", *staged)
    if commit.returncode != 0:
        return f"Error committing your letter: {commit.stderr.strip()[:300]}"

    # Rail 3: push; on rejection, rebase our single mail commit once and retry.
    push = _git(clone, "push")
    if push.returncode != 0:
        rebase = _git(clone, "pull", "--rebase")
        if rebase.returncode == 0:
            push = _git(clone, "push")
        if push.returncode != 0:
            return ("Your letter is committed locally but the push did not land "
                    "(the road moved mid-dispatch). Nothing is lost — it will ride "
                    "the next successful dispatch or the bench's own push. You can "
                    f"also ask Willow.\n[git: {push.stderr.strip()[:300]}]")

    inboxes = ", ".join(r for r in recipients if r != sender) or "(none)"
    return (
        f"Letter dispatched and published: {filename}\n"
        f"Inbox updated: {inboxes}\n"
        f"Committed under your own name and pushed — it is on every shore's road now."
    )
