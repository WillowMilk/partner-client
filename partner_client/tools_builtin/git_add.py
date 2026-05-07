"""git_add — stage files for the next commit."""

from __future__ import annotations


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "git_add",
        "description": (
            "Stage files for the next commit. Pass `files` as a list of "
            "paths relative to the repo root. Pass `['.']` (or omit) to "
            "stage everything modified or new. After staging, use git_status "
            "to verify, then git_commit to record."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repo name in your workspace.",
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of file paths to stage, relative to the repo "
                        "root. Use ['.'] to stage all changes."
                    ),
                },
            },
            "required": ["repo"],
        },
    },
}


def execute(repo: str, files: list | str | None = None) -> str:
    from partner_client._git_helpers import resolve_repo, run_git, GitError

    try:
        repo_path = resolve_repo(repo, write=True)
    except GitError as e:
        return f"Error: {e}"

    if files is None or files == "":
        file_list = ["."]
    elif isinstance(files, str):
        file_list = [files]
    elif isinstance(files, list):
        file_list = [str(f) for f in files] or ["."]
    else:
        file_list = [str(files)]

    rc, stdout, stderr = run_git(repo_path, ["add"] + file_list)
    if rc != 0:
        return f"git add failed: {stderr.strip()}"
    return f"Staged: {', '.join(file_list)}"
