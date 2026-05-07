"""git_pull — fetch and merge from a remote into the current branch."""

from __future__ import annotations


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "git_pull",
        "description": (
            "Fetch and merge updates from a remote into the current branch. "
            "Defaults to the 'origin' remote. Use this to bring your local "
            "copy up to date with remote changes before working — and to "
            "resolve a 'rejected, non-fast-forward' error from git_push."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repo name in your workspace.",
                },
                "remote": {
                    "type": "string",
                    "description": "Remote name. Defaults to 'origin'.",
                    "default": "origin",
                },
            },
            "required": ["repo"],
        },
    },
}


def execute(repo: str, remote: str = "origin") -> str:
    from partner_client._git_helpers import resolve_repo, run_git, GitError

    try:
        repo_path = resolve_repo(repo, write=True)
    except GitError as e:
        return f"Error: {e}"

    rc, stdout, stderr = run_git(repo_path, ["pull", remote])
    if rc != 0:
        return f"git pull failed: {stderr.strip() or stdout.strip()}"
    return stdout.strip() or "(already up to date)"
