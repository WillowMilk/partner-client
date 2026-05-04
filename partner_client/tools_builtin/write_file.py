"""write_file — write content to a file in the partner's memory directory.

Overwrites if the file exists. Use append-style filenames (e.g. _2026-05-04.md
suffixes) if you want to preserve history.
"""

from __future__ import annotations

import os


def _memory_dir() -> str:
    return os.environ.get("PARTNER_CLIENT_MEMORY_DIR", "")


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": (
            "Write content to a file in your memory directory. Overwrites if the file exists. "
            "Use this for journal entries, notes, letters, structured records, etc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Filename relative to the memory directory (e.g. 'Journal.md')."
                },
                "content": {
                    "type": "string",
                    "description": "The content to write. Supports markdown, plain text, JSON, etc."
                }
            },
            "required": ["filename", "content"],
        },
    },
}


def execute(filename: str, content: str) -> str:
    base = _memory_dir()
    if not base:
        return "Error: memory directory not configured."
    path = os.path.join(base, filename)
    try:
        os.makedirs(os.path.dirname(path) or base, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"File written: {filename} ({len(content)} chars)."
    except OSError as e:
        return f"Error writing {filename}: {e}"
