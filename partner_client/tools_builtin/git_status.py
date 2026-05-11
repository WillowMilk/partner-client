"""git_status — show the working-tree status of a repo. Read-only."""

from __future__ import annotations


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "git_status",
        "description": (
            "Show the working-tree status of a git repo: branch, ahead/behind "
            "tracking against upstream, and any modified, staged, or untracked "
            "files. Read-only — doesn't change anything. Use before git_add "
            "to see what's available to stage, and before git_commit to "
            "verify what will be recorded."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": (
                        "Repo name in your workspace (e.g. 'aletheia-sandbox') "
                        "or scope-qualified path."
                    ),
                },
            },
            "required": ["repo"],
        },
    },
}


def execute(repo: str) -> str:
    from partner_client._git_helpers import resolve_repo, run_git, with_scope_warning, GitError

    try:
        repo_path, scope_warning = resolve_repo(repo)
    except GitError as e:
        return f"Error: {e}"

    rc, stdout, stderr = run_git(repo_path, ["status", "-sb"])
    if rc != 0:
        return f"git status failed: {stderr.strip()}"
    return with_scope_warning(stdout or "(working tree clean)", scope_warning)
