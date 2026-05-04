"""read_file — read a text file from the partner's memory directory.

Returns the file contents as a string. For images, use a separate vision-aware
flow at the client layer (this tool is text-only).
"""

from __future__ import annotations

import os

# The client sets these via environment variables before invoking tools.
# This keeps tool modules independent of the Config object.
def _memory_dir() -> str:
    return os.environ.get("PARTNER_CLIENT_MEMORY_DIR", "")


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read a text file from your memory directory. "
            "Use this for .md, .txt, .json files. Returns the file contents as a string. "
            "Image files are handled separately by the client and should not be read with this tool."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Filename relative to the memory directory (e.g. 'Journal.md')."
                }
            },
            "required": ["filename"],
        },
    },
}


def execute(filename: str) -> str:
    base = _memory_dir()
    if not base:
        return "Error: memory directory not configured."
    path = os.path.join(base, filename)
    if not os.path.isfile(path):
        return f"Error: file not found: {filename}"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        return f"Error reading {filename}: {e}"
