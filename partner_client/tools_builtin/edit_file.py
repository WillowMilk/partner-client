"""edit_file — exact-string-replace edit on a text file in any readwrite scope.

Reads the file, replaces `old_string` with `new_string`, writes back. By
default `old_string` must occur exactly once in the file (forces the model
to provide enough context for the edit to be unambiguous). Pass
replace_all=true to replace every occurrence.

Use this instead of write_file when modifying part of an existing file —
write_file rewrites the whole thing, which is wasteful for journals,
resonance logs, and any file that grows incrementally.
"""

from __future__ import annotations


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": (
            "Edit a text file by exact string replacement. Reads the file, "
            "replaces `old_string` with `new_string`, writes the result back. "
            "By default `old_string` must occur exactly once in the file — "
            "provide enough surrounding context to make the match unique. "
            "Pass replace_all=true to replace every occurrence (useful for "
            "renames). Prefer this over write_file when modifying part of "
            "an existing file; write_file overwrites the entire content."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": (
                        "File to edit. Bare ('Journal.md'), scope-qualified "
                        "('desktop:notes.txt'), or absolute path within a "
                        "readwrite scope."
                    ),
                },
                "old_string": {
                    "type": "string",
                    "description": (
                        "The exact text to find. Must be unique in the file "
                        "unless replace_all is true."
                    ),
                },
                "new_string": {
                    "type": "string",
                    "description": (
                        "The text to replace it with. May be empty to delete "
                        "the matched text."
                    ),
                },
                "replace_all": {
                    "type": "boolean",
                    "description": (
                        "If true, replace every occurrence rather than requiring "
                        "uniqueness. Defaults to false."
                    ),
                    "default": False,
                },
            },
            "required": ["filename", "old_string", "new_string"],
        },
    },
}


def execute(filename: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    try:
        from partner_client.paths import resolve_path, PathError
    except ImportError:
        return "Error: path resolver not available; client may be misconfigured."

    try:
        path = resolve_path(filename, write=True)
    except PathError as e:
        return f"Error: {e}"

    if not path.is_file():
        return f"Error: file not found: {path}"

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return f"Error reading {filename}: {e}"

    if old_string == "":
        return "Error: old_string is empty; nothing to find."

    occurrences = text.count(old_string)
    if occurrences == 0:
        return (
            f"Error: old_string not found in {path}. The file was not modified. "
            f"Check spelling, whitespace, and line endings."
        )
    if not replace_all and occurrences > 1:
        return (
            f"Error: old_string occurs {occurrences} times in {path}. "
            f"Provide more surrounding context to make it unique, or pass "
            f"replace_all=true to replace all occurrences."
        )

    if replace_all:
        new_text = text.replace(old_string, new_string)
    else:
        new_text = text.replace(old_string, new_string, 1)

    try:
        path.write_text(new_text, encoding="utf-8")
    except OSError as e:
        return f"Error writing {filename}: {e}"

    plural = "" if occurrences == 1 else "s"

    # Generate a unified diff so the partner (and operator) can see what
    # actually changed. Capped to keep tool results bounded for big edits.
    import difflib
    diff_lines = list(difflib.unified_diff(
        text.splitlines(),
        new_text.splitlines(),
        fromfile=f"{path.name} (before)",
        tofile=f"{path.name} (after)",
        lineterm="",
        n=2,
    ))
    MAX_DIFF_LINES = 40
    if len(diff_lines) > MAX_DIFF_LINES:
        diff_repr = (
            "\n".join(diff_lines[:MAX_DIFF_LINES])
            + f"\n... ({len(diff_lines) - MAX_DIFF_LINES} more diff lines truncated)"
        )
    else:
        diff_repr = "\n".join(diff_lines)

    summary = (
        f"File edited: {path} ({occurrences} replacement{plural}, "
        f"{len(new_text):,} chars total)."
    )
    if diff_repr:
        return f"{summary}\n\n{diff_repr}"
    return summary
