"""Shared helpers for the git_* tool suite.

Centralizes subprocess wrapping, repo path resolution, and remote URL lookup
so each git_* tool stays small and consistent. Lives at package-root rather
than tools_builtin/ because it isn't itself a tool — clean separation, and
the underscore prefix would cause discovery to skip it from tools_builtin/
anyway.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .paths import resolve_path, PathError


# Git operations can be slow over the network (clone, push, pull). 60s is
# generous enough for normal repos but bounded enough to surface hangs.
GIT_TIMEOUT_SECONDS = 60


class GitError(Exception):
    """Raised when a git tool can't operate on the requested repo."""


def resolve_repo(name: str, write: bool = False) -> Path:
    """Resolve a repo name (or path) to an absolute path that is a git repo.

    Accepts:
      - bare name ("aletheia-sandbox") → resolves against default scope
      - scope-qualified ("workspace:aletheia-sandbox")
      - absolute path that falls within an allowed scope

    Raises GitError if the path doesn't resolve to an existing git repo, or
    if write=True and the matching scope is read-only.
    """
    try:
        path = resolve_path(name, write=write)
    except PathError as e:
        raise GitError(str(e)) from None
    if not path.is_dir():
        raise GitError(f"Not a directory: {path}")
    if not (path / ".git").exists():
        raise GitError(f"Not a git repository (no .git directory): {path}")
    return path


def run_git(
    repo_path: Path | None,
    args: list[str],
    extra_env: dict | None = None,
) -> tuple[int, str, str]:
    """Run a git command. Returns (returncode, stdout, stderr).

    If `repo_path` is None, runs without a cwd (useful for `git clone`).
    Otherwise, runs inside `repo_path`.
    """
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=str(repo_path) if repo_path else None,
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
            env=env,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"git command timed out after {GIT_TIMEOUT_SECONDS}s"
    except FileNotFoundError:
        return 127, "", "git binary not found in PATH"


def get_remote_url(repo_path: Path, remote: str = "origin") -> str | None:
    """Get the URL of a remote in this repo. None if the remote doesn't exist."""
    rc, stdout, _ = run_git(repo_path, ["config", "--get", f"remote.{remote}.url"])
    return stdout.strip() if rc == 0 else None


def derive_clone_target_name(url: str) -> str:
    """Derive a default local directory name from a clone URL.

    'https://github.com/foo/bar.git' → 'bar'
    'git@github.com:foo/bar.git'     → 'bar'
    'https://example.com/path/repo'   → 'repo'
    """
    name = url.rstrip("/").rsplit("/", 1)[-1]
    if ":" in name and "/" not in name:
        # 'git@host:foo' edge case — strip user@host: prefix
        name = name.rsplit(":", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name or "repo"
