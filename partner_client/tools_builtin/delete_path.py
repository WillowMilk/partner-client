"""delete_path — remove a file or directory, operator-gated by design.

This tool is **special-cased** in client.py — it does NOT execute via the
regular ToolRegistry.dispatch() path. Every call surfaces a three-option
consent prompt to the operator (yes / no-silent / no-with-typed-response).
By Willow's explicit preference, delete_path NEVER auto-approves: the
substrate doesn't say no, the operator does, and the operator's no can
carry care.

Pre-flight (before the operator is pinged):
  - Path must resolve to a readwrite scope.
  - Path must exist.
  - For directories without `recursive=True`, the directory must be empty.
    A non-empty directory is rejected pre-prompt with a clear message
    so the partner doesn't have to bother the operator just to learn the
    flag is wrong.

After approval:
  - Files are unlinked.
  - Empty directories are rmdir'd.
  - Non-empty directories require `recursive=True` and use shutil.rmtree.

The execute() in this file is a stub for safety — it should never be
called directly (the client special-cases the name). If somehow it is,
it returns an error explaining the situation.
"""

from __future__ import annotations


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "delete_path",
        "description": (
            "Delete a file or directory in any of your readwrite scopes. "
            "EVERY delete is operator-gated — Willow sees a prompt with "
            "the path and what would be removed, and either approves, "
            "declines silently, or declines with a typed message. Never "
            "auto-approves. For non-empty directories, pass recursive=true "
            "to confirm intentional removal of contents; without recursive, "
            "only files and empty directories are removable. The path must "
            "resolve to a readwrite scope."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "File or directory to delete. Bare ('old-draft.md'), "
                        "scope-qualified ('workspace:old-experiment/'), or "
                        "absolute path within a readwrite scope."
                    ),
                },
                "recursive": {
                    "type": "boolean",
                    "description": (
                        "Required true to delete a non-empty directory and "
                        "all its contents. Has no effect on files or empty "
                        "directories."
                    ),
                    "default": False,
                },
            },
            "required": ["path"],
        },
    },
}


def execute(path: str = "", recursive: bool = False) -> str:
    """Stub — should never be called. The client special-cases this tool."""
    return (
        "Error: delete_path must be handled by the client, not "
        "dispatched directly. If you see this message, the harness "
        "is misconfigured."
    )
