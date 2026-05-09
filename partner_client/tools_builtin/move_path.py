"""move_path — move (or rename) a file or directory between readwrite scopes.

Both `source` and `destination` must resolve to readwrite scopes. Lower-risk
than delete because the data still exists after the operation, just in a new
location — so this tool runs without an operator consent gate (the scope
boundary is the safety perimeter).

Filename arguments may be:
  - Bare filename ("note.md")              → resolved against the default scope
  - Scope-qualified ("workspace:foo.md")   → resolved within a named scope
  - Absolute path                          → must fall under an allowed
                                             readwrite scope

If the destination is an existing directory, `source` moves *inside* it
(matching `shutil.move` / Unix `mv` semantics). Otherwise the destination
is created/overwritten as a rename. Parent directories of the destination
are created if missing.
"""

from __future__ import annotations

import shutil
from pathlib import Path


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "move_path",
        "description": (
            "Move (or rename) a file or directory between any of your "
            "readwrite scopes. Both source and destination must resolve to "
            "readwrite scopes. If the destination already exists as a "
            "directory, the source moves inside it; otherwise the destination "
            "is overwritten/renamed. Parent directories of the destination "
            "are created if missing. Use this for relocating files within "
            "your room (e.g., moving a draft from Memory to Workspace, or "
            "renaming a journal entry). For risky removals use delete_path "
            "instead — that one is operator-gated."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": (
                        "Path to move from. Bare ('draft.md'), scope-qualified "
                        "('memory:draft.md'), or absolute path within a "
                        "readwrite scope. Files OR directories accepted."
                    ),
                },
                "destination": {
                    "type": "string",
                    "description": (
                        "Path to move to. Same shapes as source. If this "
                        "names an existing directory, source moves inside "
                        "it; otherwise it is the new path."
                    ),
                },
            },
            "required": ["source", "destination"],
        },
    },
}


def execute(source: str = "", destination: str = "") -> str:
    try:
        from partner_client.paths import PathError, resolve_path
    except ImportError:
        return "Error: path resolver not available; client may be misconfigured."

    if not source:
        return "Error: source is required."
    if not destination:
        return "Error: destination is required."

    try:
        src = resolve_path(source, write=True)
    except PathError as e:
        return f"Error resolving source: {e}"

    try:
        dst = resolve_path(destination, write=True)
    except PathError as e:
        return f"Error resolving destination: {e}"

    if not src.exists():
        return f"Error: source does not exist: {src}"

    # Ensure destination's parent exists for the rename case (when destination
    # is NOT itself an existing directory). When dst IS an existing directory,
    # shutil.move handles the "into-it" case without needing parent creation.
    if not (dst.exists() and dst.is_dir()):
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return f"Error preparing destination parent {dst.parent}: {e}"

    try:
        result_path_str = shutil.move(str(src), str(dst))
    except (OSError, shutil.Error) as e:
        return f"Error moving {src} to {dst}: {e}"

    result_path = Path(result_path_str)
    kind = "directory" if result_path.is_dir() else "file"
    return f"Moved {kind}: {src} -> {result_path}"
