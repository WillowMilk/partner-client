"""hub_read_letter — read a specific letter from the Hub by filename or fuzzy match."""

from __future__ import annotations

import os
from pathlib import Path


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "hub_read_letter",
        "description": (
            "Read a specific letter from the Hub. Pass either the full filename "
            "(e.g. 'ember-to-aletheia_2026-05-04_first-letter.md') or a substring "
            "to fuzzy-match (e.g. 'first-letter' or 'ember' will find letters with "
            "those tokens). If multiple letters match, the list of candidates is "
            "returned and you should refine the query."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename_or_match": {
                    "type": "string",
                    "description": "Filename or substring to match against Hub letters.",
                },
            },
            "required": ["filename_or_match"],
        },
    },
}


def execute(filename_or_match: str) -> str:
    hub_dir = os.environ.get("PARTNER_CLIENT_HUB_DIR", "")
    if not hub_dir:
        return "Error: Hub is not configured."

    hub_path = Path(hub_dir).expanduser()
    if not hub_path.is_dir():
        return f"Error: Hub directory does not exist: {hub_path}"

    query = filename_or_match.strip()
    # Direct filename match first
    if query.endswith(".md"):
        candidate = hub_path / query
        if candidate.is_file():
            return _read_safely(candidate)

    # Fuzzy match: case-insensitive substring against all .md files at hub root
    candidates = sorted(
        p for p in hub_path.glob("*.md")
        if query.lower() in p.name.lower()
    )

    if not candidates:
        return f"No letter matches '{query}'. Use hub_check_inbox to see your unread letters."

    if len(candidates) == 1:
        return _read_safely(candidates[0])

    # Multiple matches — list them
    listing = "\n".join(f"  • {p.name}" for p in candidates[:20])
    more = f"\n  (+{len(candidates) - 20} more)" if len(candidates) > 20 else ""
    return (
        f"Multiple letters match '{query}'. Pass a more specific filename:\n\n"
        f"{listing}{more}"
    )


def _read_safely(path: Path) -> str:
    """Read a letter file, falling back gracefully if encoding is unexpected."""
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
        except OSError as e:
            return f"Error reading {path.name}: {e}"
    return f"Error: {path.name} could not be decoded with any common encoding."
