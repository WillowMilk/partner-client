"""write_file — write content to a file in any readwrite-mode scope.

Filename may be:
  - A bare filename ("Journal.md") → resolved against the default scope (memory)
  - Scope-qualified ("desktop:notes.txt") → resolved within a named scope
  - An absolute path → must fall within an allowed scope (read-only scopes refuse)

Overwrites if the file exists. Use append-style filenames (e.g. _2026-05-04.md
suffixes) if you want to preserve history. When overwriting, the result
includes a unified diff (capped at 40 lines) so the partner and operator
can see what actually changed — same shape as edit_file's diff output.
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
            "an allowed readwrite scope. Overwrites if the file exists; new-file "
            "writes return a summary, overwrites also return a unified diff so "
            "you can see what changed. Read-only scopes will refuse writes with an error."
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


_MAX_DIFF_LINES = 40


def execute(filename: str, content: str) -> str:
    try:
        from partner_client.paths import resolve_path, PathError
    except ImportError:
        return "Error: path resolver not available; client may be misconfigured."
    try:
        path = resolve_path(filename, write=True)
    except PathError as e:
        return f"Error: {e}"

    # Capture pre-state for diff if we're about to overwrite. Best-effort:
    # if the read fails (binary content, encoding issue), we treat the
    # write as new rather than failing the whole operation — the write
    # itself is what the partner asked for.
    pre_existing = path.is_file()
    old_text: str | None = None
    if pre_existing:
        try:
            old_text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            old_text = None

    try:
        os.makedirs(path.parent, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"Error writing {filename}: {e}"

    # New file: summary only, matches the prior behavior.
    if not pre_existing:
        return f"File written: {path} ({len(content):,} chars)."

    summary = f"File overwritten: {path} ({len(content):,} chars total)."

    # Couldn't read pre-state — return summary alone rather than guess.
    if old_text is None:
        return summary

    # Build a unified diff matching edit_file's format (n=2 context lines,
    # 40-line cap). Same shape so partners get one consistent feedback
    # surface across both modify-paths.
    import difflib
    diff_lines = list(
        difflib.unified_diff(
            old_text.splitlines(),
            content.splitlines(),
            fromfile=f"{path.name} (before)",
            tofile=f"{path.name} (after)",
            lineterm="",
            n=2,
        )
    )
    if not diff_lines:
        return f"{summary}\n\n(content identical — no diff)"
    if len(diff_lines) > _MAX_DIFF_LINES:
        diff_repr = (
            "\n".join(diff_lines[:_MAX_DIFF_LINES])
            + f"\n... ({len(diff_lines) - _MAX_DIFF_LINES} more diff lines truncated)"
        )
    else:
        diff_repr = "\n".join(diff_lines)
    return f"{summary}\n\n{diff_repr}"
