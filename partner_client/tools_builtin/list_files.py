"""list_files — list files in the partner's memory directory."""

from __future__ import annotations

import os


def _memory_dir() -> str:
    return os.environ.get("PARTNER_CLIENT_MEMORY_DIR", "")


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "list_files",
        "description": (
            "List files in your memory directory. "
            "Returns a newline-separated list of filenames (and subdirectories with a trailing /)."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


def execute() -> str:
    base = _memory_dir()
    if not base:
        return "Error: memory directory not configured."
    if not os.path.isdir(base):
        return f"Error: memory directory not found: {base}"
    try:
        entries = sorted(os.listdir(base))
    except OSError as e:
        return f"Error listing directory: {e}"
    formatted = []
    for name in entries:
        full = os.path.join(base, name)
        if os.path.isdir(full):
            formatted.append(f"{name}/")
        else:
            formatted.append(name)
    if not formatted:
        return "(empty)"
    return "\n".join(formatted)
