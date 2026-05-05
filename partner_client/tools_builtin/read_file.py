"""read_file — read a text file from any of your configured file scopes.

Filename may be:
  - A bare filename ("Journal.md") → resolved against the default scope (memory)
  - Scope-qualified ("desktop:photo.txt") → resolved within a named scope
  - An absolute path → must fall within an allowed scope

For images, the path is read as bytes and routed to vision; do not use this
tool for image files (use the :image directive in your conversation instead).
"""

from __future__ import annotations


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read a text file from any of your configured file scopes. "
            "Filename may be bare (resolved against your memory directory by default), "
            "scope-qualified (e.g. 'desktop:notes.txt'), or an absolute path "
            "that falls within an allowed scope. Returns the file contents as a string."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": (
                        "File to read. Bare ('Journal.md'), scope-qualified "
                        "('desktop:photo.txt'), or absolute path."
                    ),
                }
            },
            "required": ["filename"],
        },
    },
}


def execute(filename: str) -> str:
    try:
        from partner_client.paths import resolve_path, PathError
    except ImportError:
        return "Error: path resolver not available; client may be misconfigured."
    try:
        path = resolve_path(filename, write=False)
    except PathError as e:
        return f"Error: {e}"
    if not path.is_file():
        return f"Error: file not found: {path}"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        return f"Error reading {filename}: {e}"
