"""git_log — show recent commit history. Read-only."""

from __future__ import annotations


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "git_log",
        "description": (
            "Show the most recent commits in a repo as one-line summaries "
            "with branch and tag decorations. Useful before git_pull to see "
            "what's new, or after git_commit to verify what was recorded."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repo name in your workspace.",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of commits to show (1-100). Default 10.",
                    "default": 10,
                },
            },
            "required": ["repo"],
        },
    },
}


def execute(repo: str, count: int = 10) -> str:
    from partner_client._git_helpers import resolve_repo, run_git, GitError

    try:
        repo_path = resolve_repo(repo)
    except GitError as e:
        return f"Error: {e}"

    try:
        count = max(1, min(int(count), 100))
    except (TypeError, ValueError):
        count = 10

    args = ["log", f"-{count}", "--oneline", "--decorate"]
    rc, stdout, stderr = run_git(repo_path, args)
    if rc != 0:
        return f"git log failed: {stderr.strip()}"
    return stdout or "(no commits yet)"
