"""write_file — write content to a file in any readwrite-mode scope.

Filename may be:
  - A bare filename ("Journal.md") → resolved against the default scope (memory)
  - Scope-qualified ("desktop:notes.txt") → resolved within a named scope
  - An absolute path → must fall within an allowed scope (read-only scopes refuse)

Overwrites if the file exists. Use append-style filenames (e.g. _2026-05-04.md
suffixes) if you want to preserve history.
"""

from __future__ import annotations

import os


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": (
            "Write content to a file in any of your configured readwrite scopes. "
            "Filename may be bare (resolved against your memory directory by default), "
            "scope-qualified (e.g. 'desktop:notes.txt'), or an absolute path within "
            "an allowed readwrite scope. Overwrites if the file exists. "
            "Read-only scopes will refuse writes with an error."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": (
                        "File to write. Bare ('Journal.md'), scope-qualified "
                        "('desktop:notes.txt'), or absolute path."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "The content to write. Supports markdown, plain text, JSON, etc.",
                }
            },
            "required": ["filename", "content"],
        },
    },
}


def execute(filename: str, content: str) -> str:
    try:
        from partner_client.paths import resolve_path, PathError
    except ImportError:
        return "Error: path resolver not available; client may be misconfigured."
    try:
        path = resolve_path(filename, write=True)
    except PathError as e:
        return f"Error: {e}"
    try:
        os.makedirs(path.parent, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"File written: {path} ({len(content)} chars)."
    except OSError as e:
        return f"Error writing {filename}: {e}"
