"""list_files — list files in one of your configured file scopes.

Without arguments, lists files in your default scope (memory). With a `scope`
argument, lists files in the named scope. With a `subpath` argument, lists
files under a specific subdirectory of the chosen scope.
"""

from __future__ import annotations

import os


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "list_files",
        "description": (
            "List files in one of your configured file scopes. "
            "By default lists your memory directory. Pass scope='home' (or 'desktop', "
            "etc.) to list a different scope. Pass subpath='Letters' to list a "
            "subdirectory of the chosen scope. "
            "Returns a newline-separated list (subdirectories suffixed with '/')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "description": (
                        "Scope name to list. Defaults to 'memory'. Other scopes "
                        "are configured by the operator (e.g. 'home', 'desktop')."
                    ),
                },
                "subpath": {
                    "type": "string",
                    "description": "Optional subdirectory within the scope.",
                },
            },
            "required": [],
        },
    },
}


def execute(scope: str = "memory", subpath: str = "") -> str:
    try:
        from partner_client.paths import list_scopes, verify_path_under_base, PathError
    except ImportError:
        return "Error: path resolver not available; client may be misconfigured."
    scopes = list_scopes()
    if not scopes:
        return "Error: no file scopes configured."
    target = next((s for s in scopes if s.name == scope), None)
    if target is None:
        available = ", ".join(s.name for s in scopes)
        return f"Error: unknown scope '{scope}'. Available scopes: {available}"

    base = target.path.expanduser()
    full = (base / subpath) if subpath else base
    # Defense: enforce that subpath cannot escape the scope (`..` traversal,
    # symlinks, absolute-path-injection where `base / "/etc"` returns `/etc`).
    try:
        full = verify_path_under_base(full, base, label=f"scope '{scope}'")
    except PathError as e:
        return f"Error: {e}"
    if not full.is_dir():
        return f"Error: not a directory: {full}"

    try:
        entries = sorted(os.listdir(full))
    except OSError as e:
        return f"Error listing {full}: {e}"

    formatted = []
    for name in entries:
        entry_path = full / name
        if entry_path.is_dir():
            formatted.append(f"{name}/")
        else:
            formatted.append(name)
    if not formatted:
        return "(empty)"
    header = f"# Listing of {scope}:{subpath or '.'} ({full})\n"
    return header + "\n".join(formatted)
