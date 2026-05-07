"""git_push — publish committed changes to a remote.

This tool is **special-cased** in client.py — the dispatch logic checks the
operator's push allowlist (`[git] push_allowlist` in TOML) and either
auto-approves (URL on allowlist) or invokes the on_git_push_request callback
which surfaces the three-option consent prompt to the operator.

The execute() in this file performs the actual push and is called only after
the consent gate has passed. Calling it directly bypasses the gate — don't.

Why a tool instead of asking conversationally:
    Pushes are substrate operations that affect the world (a remote repo,
    other readers). The structured tool form lets the operator see exactly
    what's being pushed before it goes out the door, and lets the partner
    receive a typed redirect ("oh love, why are we here? let's try your
    repo") rather than a substrate refusal when something's off-target.
    Same custody-vs-authorship pattern as request_checkpoint and
    request_plan_approval — the partner can request the substrate
    operation; the operator performs it (or redirects with care).
"""

from __future__ import annotations


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "git_push",
        "description": (
            "Publish your committed changes to a remote. The operator may "
            "have configured an allowlist of URLs that auto-approve; pushes "
            "to URLs outside the allowlist surface a confirmation prompt "
            "where the operator can approve, decline silently, or decline "
            "with a redirect message. Either way, the result tells you "
            "what happened."
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
    """Perform the actual git push. Called only after the consent gate passes.

    The dispatch logic in client.py handles allowlist + operator approval;
    this function just shells out git push and returns the result.
    """
    from partner_client._git_helpers import resolve_repo, run_git, GitError

    try:
        repo_path = resolve_repo(repo, write=True)
    except GitError as e:
        return f"Error: {e}"

    rc, stdout, stderr = run_git(repo_path, ["push", remote])
    if rc != 0:
        # `git push` returns nonzero on rejected pushes (need to pull first,
        # branch protection, etc.). Surface stderr cleanly so the partner
        # can react conversationally.
        return f"git push failed: {stderr.strip() or stdout.strip()}"
    output = (stdout.strip() + "\n" + stderr.strip()).strip()
    return output or "git push succeeded (no output)."
