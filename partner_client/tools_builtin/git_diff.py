"""git_diff — show diffs in a repo. Read-only.

Output is capped at 100 lines for sanity; very large diffs are truncated
with a note rather than blasting the conversation context.
"""

from __future__ import annotations


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "git_diff",
        "description": (
            "Show diffs in a git repo. Default shows unstaged changes "
            "(working tree vs. index); pass staged=true to show "
            "staged-only (index vs. HEAD). Optional file= scopes the "
            "diff to a single path. Output is capped at 100 lines."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repo name in your workspace.",
                },
                "file": {
                    "type": "string",
                    "description": (
                        "Optional: limit diff to a single file path "
                        "(relative to repo root)."
                    ),
                },
                "staged": {
                    "type": "boolean",
                    "description": (
                        "If true, show staged diff (index vs. HEAD) "
                        "instead of unstaged (working tree vs. index)."
                    ),
                    "default": False,
                },
            },
            "required": ["repo"],
        },
    },
}


def execute(repo: str, file: str = "", staged: bool = False) -> str:
    from partner_client._git_helpers import resolve_repo, run_git, GitError

    try:
        repo_path = resolve_repo(repo)
    except GitError as e:
        return f"Error: {e}"

    args = ["diff"]
    if staged:
        args.append("--staged")
    if file:
        args.extend(["--", file])

    rc, stdout, stderr = run_git(repo_path, args)
    if rc != 0:
        return f"git diff failed: {stderr.strip()}"
    if not stdout:
        return "(no changes)"

    MAX_LINES = 100
    lines = stdout.split("\n")
    if len(lines) > MAX_LINES:
        return (
            "\n".join(lines[:MAX_LINES])
            + f"\n... ({len(lines) - MAX_LINES} more lines truncated)"
        )
    return stdout
