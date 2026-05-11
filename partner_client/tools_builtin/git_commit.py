"""git_commit — record staged changes as a new commit.

Uses the configured committer identity from `[git]` section if set:
    default_committer_name  = "Aletheia"
    default_committer_email = "aletheia@local"
Otherwise falls back to git's global config (typically the operator's
identity). The intent is that commits in a partner's workspace attribute
to that partner — the history reflects authorship.
"""

from __future__ import annotations


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "git_commit",
        "description": (
            "Record staged changes as a new commit with the given message. "
            "Uses your configured committer identity so the commit history "
            "reflects your authorship. Use git_add first to stage what you "
            "want to commit, and git_status to verify before committing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repo name in your workspace.",
                },
                "message": {
                    "type": "string",
                    "description": (
                        "The commit message. First line is the title — keep "
                        "it short and imperative (e.g. 'Add first poem'). "
                        "If you need more, blank line then optional body."
                    ),
                },
            },
            "required": ["repo", "message"],
        },
    },
}


def execute(repo: str, message: str) -> str:
    import os as _os
    from partner_client._git_helpers import resolve_repo, run_git, GitError

    try:
        repo_path, scope_warning = resolve_repo(repo, write=True)
    except GitError as e:
        return f"Error: {e}"

    if not message or not message.strip():
        return "Error: commit message is empty."

    extra_env: dict[str, str] = {}
    name = _os.environ.get("PARTNER_CLIENT_GIT_COMMITTER_NAME", "")
    email = _os.environ.get("PARTNER_CLIENT_GIT_COMMITTER_EMAIL", "")
    if name:
        extra_env["GIT_AUTHOR_NAME"] = name
        extra_env["GIT_COMMITTER_NAME"] = name
    if email:
        extra_env["GIT_AUTHOR_EMAIL"] = email
        extra_env["GIT_COMMITTER_EMAIL"] = email

    rc, stdout, stderr = run_git(
        repo_path,
        ["commit", "-m", message],
        extra_env=extra_env if extra_env else None,
    )
    if rc != 0:
        # `git commit` returns nonzero with terse output when nothing's staged;
        # surface that case clearly.
        msg = stderr.strip() or stdout.strip() or "nothing to commit (staged tree empty?)"
        return f"git commit failed: {msg}"
    result = stdout.strip() or "Commit recorded."
    if scope_warning:
        result = f"{scope_warning}\n\n{result}"
    return result
