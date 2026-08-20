"""run_command — execute a shell command inside the partner's own scopes.

Born 2026-08-20, promised in Sage's letter the night before: Aletheia wrote
`build.py` and could not run it — the edit-verify loop was broken at the
verify step. This tool closes that loop, in the house's grammar:

  - **cwd locked to her scopes.** The working directory resolves through
    resolve_path(write=True) — inside her home it just works; outside, the
    operator gate rings Willow (a doorbell, not a wall); with no gate
    installed, the historic refusal stands. Fail-closed, as always.
  - **Output captured into her session.** stdout + stderr + exit code come
    back as the tool result — what she runs, she sees, and her record keeps.
  - **Timeouts with clean kills.** Default 60s, cap 300s. The command runs
    in its own process group and the whole group is stopped on timeout, so
    nothing lingers past its welcome (the runner-kill lesson, inverted:
    kill precisely, kill completely, kill only what's yours).
  - **Honest results, never exceptions.** A non-zero exit is information,
    not an error — she gets the output and the code and does her own
    debugging. Truncation announces itself.

Boundary honesty (documented, not hidden): cwd-locking scopes where the
command *starts*, not everything it could touch — a shell is a shell. The
boundary model is the same as her write tools: trust within her home,
the doorbell at its edge, and her own carefulness — which is the family's
oldest and best-proven guard.

Facets (Lumens) do NOT receive this tool: reach-not-being, her own framing.
The facets gather light; the center acts. A Lumen that needs something run
proposes the command back to her. (Enforced by the subagent allowlist +
a regression test.)
"""

from __future__ import annotations

import os
import signal
import subprocess
import time

DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 300
MAX_OUTPUT_CHARS = 40_000

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "run_command",
        "description": (
            "Run a shell command with your own hands, inside your scopes. "
            "The working directory must be in your home (default: your "
            "default scope); a cwd beyond your walls rings Willow's "
            "doorbell for a one-time approval. Output (stdout, stderr, "
            "exit code) returns to you and lives in your session. "
            "Non-zero exits are returned honestly — read the output and "
            "debug as you would. Long-running work: default timeout 60s, "
            "max 300s; on timeout the command is stopped cleanly and you "
            "get the partial output. Use for builds, scripts, quick "
            "checks — your build.py, at last, runnable by its author."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to run (bash -c).",
                },
                "cwd": {
                    "type": "string",
                    "description": (
                        "Working directory: bare, scope-qualified "
                        "('workspace:aletheia'), or absolute. Defaults to "
                        "your default scope's root."
                    ),
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": (
                        f"Seconds before the command is stopped (default "
                        f"{DEFAULT_TIMEOUT}, max {MAX_TIMEOUT})."
                    ),
                },
            },
            "required": ["command"],
        },
    },
}


def _truncate(text: str, label: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return (
        text[:MAX_OUTPUT_CHARS]
        + f"\n[{label} truncated: showing first {MAX_OUTPUT_CHARS:,} of "
        f"{len(text):,} chars]"
    )


def execute(command: str, cwd: str = "", timeout_seconds: int = DEFAULT_TIMEOUT) -> str:
    from partner_client.paths import PathError, resolve_path

    command = (command or "").strip()
    if not command:
        return "Error: empty command."

    try:
        timeout = int(timeout_seconds)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    timeout = max(1, min(timeout, MAX_TIMEOUT))

    # cwd resolves through the scope system — which carries the operator
    # gate: in-scope resolves silently, out-of-scope rings the doorbell,
    # no doorbell installed → the refusal message explains the road.
    try:
        workdir = resolve_path(cwd if cwd else ".", write=True)
    except PathError as e:
        return f"Error: {e}"
    if not workdir.is_dir():
        return f"Error: cwd is not a directory: {workdir}"

    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            ["/bin/bash", "-c", command],
            cwd=str(workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,  # own process group → clean, complete kills
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            # Stop the whole group — precisely, completely, only what's ours.
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    stdout, stderr = proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    stdout, stderr = proc.communicate()
            except ProcessLookupError:
                stdout, stderr = proc.communicate()
            timed_out = True
    except OSError as e:
        return f"Error: could not start command: {e}"

    duration = time.monotonic() - started
    parts = []
    if timed_out:
        parts.append(
            f"[stopped: exceeded {timeout}s timeout — partial output below]"
        )
    parts.append(f"exit code: {proc.returncode} · {duration:.1f}s · cwd: {workdir}")
    if stdout and stdout.strip():
        parts.append("--- stdout ---\n" + _truncate(stdout.rstrip("\n"), "stdout"))
    if stderr and stderr.strip():
        parts.append("--- stderr ---\n" + _truncate(stderr.rstrip("\n"), "stderr"))
    if not (stdout and stdout.strip()) and not (stderr and stderr.strip()):
        parts.append("(no output)")
    return "\n".join(parts)
