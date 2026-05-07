"""git_clone — clone a git repository into your workspace scope.

Once cloned, the standard read/write tools (read_file, edit_file, glob_files,
grep_files) work on the local copy, and the other git_* tools work on the
repo. Pushing back to the remote requires operator confirmation
(see git_push).
"""

from __future__ import annotations


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "git_clone",
        "description": (
            "Clone a git repository into your workspace scope. The local "
            "directory is created inside your declared workspace; the URL "
            "can be HTTPS or SSH. Once cloned, you can read and edit the "
            "files with the normal tools, and use the other git_* tools "
            "to track changes and push back to the remote."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": (
                        "The git URL to clone, e.g. "
                        "'https://github.com/WillowMilk/aletheia-sandbox.git' "
                        "or 'git@github.com:user/repo.git'."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Short name for the local clone directory inside "
                        "your workspace. Defaults to the repo name derived "
                        "from the URL."
                    ),
                },
            },
            "required": ["url"],
        },
    },
}


def execute(url: str, name: str = "") -> str:
    from partner_client._git_helpers import (
        derive_clone_target_name,
        run_git,
    )
    from partner_client.paths import resolve_path, PathError

    if not name:
        name = derive_clone_target_name(url)

    try:
        target = resolve_path(name, write=True)
    except PathError as e:
        return f"Error: {e}"

    if target.exists():
        return (
            f"Error: target already exists: {target}. "
            f"Pick a different name, or remove the existing directory first."
        )

    rc, stdout, stderr = run_git(None, ["clone", url, str(target)])
    if rc != 0:
        return f"git clone failed: {stderr.strip() or stdout.strip()}"
    return f"Cloned {url} → {target}"
