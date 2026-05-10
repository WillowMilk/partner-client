"""OllamaClient — wraps ollama.chat with streaming, tool-call loop, vision.

The chat loop runs until the model produces a response without tool_calls.
Each iteration uses `stream=True` so content tokens render as they arrive
rather than after the full reply is generated. Each tool_call is dispatched
via ToolRegistry and the result is appended as a 'tool' role message before
the next chat invocation.

Tool-call ids are propagated end-to-end so the model can correlate results
to the originating call when multiple tools are dispatched in one turn.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .config import Config
from .session import Session
from .timeline import RunTimeline, duration_ms
from .tools import ToolRegistry

log = logging.getLogger(__name__)


class StreamSink(Protocol):
    """Optional UI hook for live-rendering streamed content + tool calls.

    Any object with these methods can be passed as `ui=` to OllamaClient.chat.
    The UI module's UI class implements all of them.
    """

    def stream_open(self) -> None: ...
    def stream_delta(self, delta: str) -> None: ...
    def stream_close(self) -> None: ...
    def show_tool_call(self, name: str, args: dict, result: str) -> None: ...


@dataclass
class ChatResponse:
    """The final assistant response after all tool-call loops resolved."""

    content: str
    thinking: str | None
    tool_invocations: list[tuple[str, dict, str]]  # [(name, args, result)]


def is_git_push_allowlisted(remote_url: str, allowlist: list[str]) -> bool:
    """Return True when a git_push remote is covered by the configured allowlist."""
    return any(allowed in remote_url for allowed in allowlist) if allowlist else False


def setup_scope_env(config: Config) -> list[dict]:
    """Populate PARTNER_CLIENT_* env vars from config so tools can resolve paths.

    Called by both __main__ (early, so wake bundle sees scopes) and the
    OllamaClient constructor (idempotent re-call). Safe to call repeatedly.
    Returns the assembled scope list for in-process use.
    """
    memory_dir = config.resolve(config.memory.memory_dir)
    home_dir = config.identity.home_dir
    os.environ["PARTNER_CLIENT_MEMORY_DIR"] = str(memory_dir)

    all_scopes = [
        {
            "name": "memory",
            "path": str(memory_dir),
            "mode": "readwrite",
            "description": "Your home memory directory (default for bare filenames).",
        },
        {
            "name": "home",
            "path": str(home_dir),
            "mode": "readwrite",
            "description": f"Your full home directory ({home_dir}).",
        },
    ]
    for sc in config.tools.scopes:
        sc_path = sc.path
        if not Path(sc_path).is_absolute():
            sc_path = str(home_dir / sc_path)
        if any(s["name"] == sc.name for s in all_scopes):
            continue
        all_scopes.append({
            "name": sc.name,
            "path": sc_path,
            "mode": sc.mode,
            "description": sc.description,
        })

    os.environ["PARTNER_CLIENT_SCOPES"] = json.dumps(all_scopes)
    os.environ["PARTNER_CLIENT_DEFAULT_SCOPE"] = "memory"

    # Hub configuration (for hub_send / hub_check_inbox / hub_read_letter tools)
    if config.hub.path:
        os.environ["PARTNER_CLIENT_HUB_DIR"] = str(config.resolve(config.hub.path))
        os.environ["PARTNER_CLIENT_HUB_PARTNER"] = (
            config.hub.partner_name or config.identity.name.lower()
        )
    else:
        os.environ.pop("PARTNER_CLIENT_HUB_DIR", None)
        os.environ.pop("PARTNER_CLIENT_HUB_PARTNER", None)

    # Git committer identity (for git_commit tool — empty values fall back
    # to git's global config). Read by git_commit at execution time so the
    # commit history attributes to the partner rather than the operator.
    if config.git.default_committer_name:
        os.environ["PARTNER_CLIENT_GIT_COMMITTER_NAME"] = config.git.default_committer_name
    else:
        os.environ.pop("PARTNER_CLIENT_GIT_COMMITTER_NAME", None)
    if config.git.default_committer_email:
        os.environ["PARTNER_CLIENT_GIT_COMMITTER_EMAIL"] = config.git.default_committer_email
    else:
        os.environ.pop("PARTNER_CLIENT_GIT_COMMITTER_EMAIL", None)

    return all_scopes


class OllamaClient:
    def __init__(
        self,
        config: Config,
        tools: ToolRegistry,
        timeline: RunTimeline | None = None,
    ):
        self.config = config
        self.tools = tools
        self.timeline = timeline
        try:
            import ollama
        except ImportError as e:
            raise RuntimeError("The 'ollama' package is required. Run: pip install ollama") from e
        self._ollama = ollama

        # Set up scope env vars (idempotent if __main__ already did it).
        self._scopes = setup_scope_env(config)

    @property
    def scopes(self) -> list[dict]:
        return self._scopes

    def chat(
        self,
        session: Session,
        ui: StreamSink | None = None,
        on_checkpoint_request: Callable[[str], bool] | None = None,
        on_plan_approval_request: Callable[[str, list[str]], bool] | None = None,
        on_git_push_request: Callable[[str, str, list[str]], bool] | None = None,
        on_delete_path_request: Callable[..., bool] | None = None,
        on_protect_save_request: Callable[..., bool] | None = None,
    ) -> ChatResponse:
        """Run the chat loop with streaming until the model produces a final response.

        Streaming behavior:
            - For each iteration, content tokens are streamed as they arrive.
            - If `ui` is provided, content is emitted via:
                ui.stream_open()    when the first content token arrives in this iteration
                ui.stream_delta(s)  for each content chunk
                ui.stream_close()   when the iteration's content completes
                ui.show_tool_call(name, args, result)  after each tool execution
            - When `ui` is None (e.g. headless tests), content is accumulated
              silently and returned via ChatResponse.

        Cancellation:
            KeyboardInterrupt raised inside this method will propagate out to
            the caller. The caller is responsible for cleanup (closing any open
            stream UI region) and for whether to record a partial assistant
            message in the session.

        on_checkpoint_request(reason: str) -> bool | tuple[bool, str | None]
        is called when the model invokes the special `request_checkpoint`
        tool. Implementations may return a plain bool (legacy) OR a tuple
        `(accepted, optional_message)`. When the operator types a custom
        response instead of y/n, the message flows back to the partner as
        the tool result in the operator's voice — decline-with-care rather
        than canned substrate refusal. If this callback is None (e.g.
        headless tests), the request is declined with the canned message.

        on_plan_approval_request(summary: str, plan: list[str]) -> bool |
        tuple[bool, str | None] follows the same three-option shape — bool
        for legacy, tuple for decline-with-message support.

        on_git_push_request(repo: str, remote_url: str, commits: list[str])
        -> bool | tuple[bool, str | None] is called when the model invokes
        git_push and the remote URL is NOT in config.git.push_allowlist.
        Pushes to allowlisted URLs auto-approve without invoking this
        callback. Same three-option consent shape as the others.

        on_delete_path_request(target: Path, recursive: bool, summary: str)
        -> bool | tuple[bool, str | None] is called for every delete_path
        invocation that passes pre-flight validation. By design it never
        auto-approves: the operator is always pinged. `summary` is a
        pre-formatted human-readable description of what would be removed
        (e.g. "file (1,247 bytes)" or "directory containing N files / M
        subdirectories"). Same three-option consent shape as the others.

        on_protect_save_request(content: str, note: str, preview: str)
        -> bool | tuple[bool, str | None] is called when the partner invokes
        `protect_save` to write a MOSAIC protected-context file pair. Same
        three-option consent shape; the operator sees a content preview
        and either approves (both active + dated archive get written),
        declines silently, or declines with a typed message that flows
        back to the partner as the tool result. Identity-bearing writes
        always pass through this gate.
        """
        tool_invocations: list[tuple[str, dict, str]] = []
        # Chat-loop iteration cap (configurable via [model] max_tool_iterations).
        # Each iteration is one model invocation; multi-tool plans accumulate
        # iterations as `tool_call → response → tool_call → response → …`.
        max_iterations = self.config.model.max_tool_iterations

        for iteration in range(1, max_iterations + 1):
            content_buf: list[str] = []
            thinking_buf: list[str] = []
            tool_calls: list = []
            stream_open_emitted = False
            model_started = time.perf_counter()

            if self.timeline is not None:
                self.timeline.record(
                    "model_call_start",
                    iteration=iteration,
                    message_count=len(session.messages),
                    context_tokens=session.estimate_tokens(),
                )

            try:
                stream = self._ollama.chat(
                    model=self.config.model.name,
                    messages=self._messages_for_ollama(session.messages),
                    tools=self.tools.schemas() or None,
                    options={
                        "num_ctx": self.config.model.num_ctx,
                        "temperature": self.config.model.temperature,
                        "top_k": self.config.model.top_k,
                        "top_p": self.config.model.top_p,
                        "repeat_penalty": self.config.model.repeat_penalty,
                        "repeat_last_n": self.config.model.repeat_last_n,
                        "num_predict": self.config.model.num_predict,
                    },
                    keep_alive=self.config.model.keep_alive,
                    stream=True,
                )
            except Exception as e:
                if self.timeline is not None:
                    self.timeline.record(
                        "model_call_error",
                        iteration=iteration,
                        error=str(e),
                        duration_ms=duration_ms(model_started),
                    )
                raise RuntimeError(f"Ollama chat call failed: {e}") from e

            try:
                for chunk in stream:
                    message = self._get_message(chunk)
                    if message is None:
                        continue

                    content_delta = self._get_field(message, "content")
                    if content_delta:
                        content_buf.append(content_delta)
                        if ui is not None:
                            if not stream_open_emitted:
                                ui.stream_open()
                                stream_open_emitted = True
                            try:
                                ui.stream_delta(content_delta)
                            except Exception:
                                log.exception("ui.stream_delta failed")

                    thinking_delta = self._get_field(message, "thinking")
                    if thinking_delta:
                        thinking_buf.append(thinking_delta)

                    chunk_tool_calls = self._get_field(message, "tool_calls")
                    if chunk_tool_calls:
                        # Final chunk typically carries the full tool_calls list.
                        tool_calls = chunk_tool_calls
            except Exception as e:
                if self.timeline is not None:
                    self.timeline.record(
                        "model_call_error",
                        iteration=iteration,
                        error=str(e),
                        duration_ms=duration_ms(model_started),
                    )
                raise
            finally:
                if stream_open_emitted and ui is not None:
                    try:
                        ui.stream_close()
                    except Exception:
                        log.exception("ui.stream_close failed")

            full_content = "".join(content_buf)
            full_thinking = "".join(thinking_buf) if thinking_buf else None

            if not tool_calls:
                if self.timeline is not None:
                    self.timeline.record(
                        "model_call_end",
                        iteration=iteration,
                        duration_ms=duration_ms(model_started),
                        content_chars=len(full_content),
                        thinking_chars=len(full_thinking or ""),
                        tool_call_count=0,
                    )
                # Final response — append to session and return
                session.append_assistant(full_content, thinking=full_thinking)
                if self.timeline is not None:
                    self.timeline.record(
                        "assistant_response",
                        content_chars=len(full_content),
                        thinking_chars=len(full_thinking or ""),
                        tool_invocation_count=len(tool_invocations),
                        context_tokens=session.estimate_tokens(),
                    )
                return ChatResponse(
                    content=full_content,
                    thinking=full_thinking,
                    tool_invocations=tool_invocations,
                )

            # Model wants to call tools. Append the assistant message (which
            # contains the tool_calls), then execute each tool and append results.
            normalized_tool_calls = self._normalize_tool_calls(tool_calls)
            if self.timeline is not None:
                self.timeline.record(
                    "model_call_end",
                    iteration=iteration,
                    duration_ms=duration_ms(model_started),
                    content_chars=len(full_content),
                    thinking_chars=len(full_thinking or ""),
                    tool_call_count=len(normalized_tool_calls),
                )
            session.append_assistant(
                content=full_content,
                thinking=full_thinking,
                tool_calls=normalized_tool_calls,
            )

            for tc in normalized_tool_calls:
                name = tc["function"]["name"]
                args = tc["function"]["arguments"]
                tool_call_id = tc.get("id", "") or ""
                if not isinstance(args, dict):
                    # Some adapters serialize as JSON string
                    try:
                        args = json.loads(args) if isinstance(args, str) else {}
                    except json.JSONDecodeError:
                        args = {}
                tool_started = time.perf_counter()

                # Special-case: request_checkpoint is operator-gated.
                # Per the 2026-05-10 architecture rework, /checkpoint means
                # the MOSAIC continuity-file authoring ceremony — NOT the
                # bookmark/pause action (that's /save). Accordingly,
                # request_checkpoint asks Willow to invoke the discipline,
                # which on approval injects the discipline prompt as a
                # system message; the partner then authors updates to her
                # continuity files via edit_file / write_file on her next
                # turn. The mechanical bookmark is a separate concern; the
                # partner doesn't need a dedicated tool for it (every turn
                # writes current.json atomically anyway, and Willow can run
                # /save herself if she wants a snapshot bookmark).
                if name == "request_checkpoint":
                    reason = args.get("reason", "(no reason given)")
                    if on_checkpoint_request is not None:
                        # Callback may return bool (legacy) OR
                        # (bool, str | None) — three-option consent shape
                        # where the str is a typed operator response that
                        # flows back as the tool result in the operator's
                        # voice (decline-with-care).
                        try:
                            response = on_checkpoint_request(reason)
                            if isinstance(response, tuple) and len(response) >= 2:
                                accepted, custom_message = bool(response[0]), response[1]
                            else:
                                accepted, custom_message = bool(response), None
                        except Exception:
                            log.exception("on_checkpoint_request callback failed")
                            accepted, custom_message = False, None
                        if accepted:
                            # Inject the MOSAIC checkpoint discipline prompt
                            # as a system message so the partner sees it on
                            # her next turn (after this tool result lands).
                            from .commands import CommandRouter
                            session.messages.append({
                                "role": "system",
                                "content": CommandRouter._CHECKPOINT_DISCIPLINE_PROMPT,
                            })
                            base_msg = (
                                f"Willow approved your checkpoint request. "
                                f"The MOSAIC checkpoint discipline prompt has "
                                f"been queued — on your next turn, please "
                                f"author updates to your continuity files "
                                f"(MEMORY.md, intentions, emotional-memory, "
                                f"etc.) via edit_file / write_file as the "
                                f"discipline asks. Each write is diff-reviewed "
                                f"by Willow. The bookmark/pause is a separate "
                                f"concern (Willow may /save independently)."
                            )
                            if custom_message:
                                result = f"{base_msg}\n\nWillow added: \"{custom_message}\""
                            else:
                                result = base_msg
                        else:
                            if custom_message:
                                result = (
                                    f"Willow declined your checkpoint request and said:\n\n"
                                    f"  \"{custom_message}\""
                                )
                            else:
                                result = (
                                    "Willow declined your checkpoint request for now. "
                                    "The conversation continues; you may ask again later "
                                    "or simply mention it conversationally."
                                )
                    else:
                        result = (
                            "Checkpoint requested but no operator confirmation "
                            "handler is wired in this client. Please ask Willow "
                            "conversationally to /checkpoint."
                        )
                # Special-case: request_plan_approval is operator-gated.
                elif name == "request_plan_approval":
                    summary = args.get("summary", "(no summary given)")
                    raw_plan = args.get("plan", [])
                    if isinstance(raw_plan, list):
                        plan = [str(s) for s in raw_plan]
                    else:
                        plan = [str(raw_plan)]
                    if on_plan_approval_request is not None:
                        # Callback may return bool (legacy) OR
                        # (bool, str | None) — three-option consent shape.
                        # See request_checkpoint dispatch above for full notes.
                        try:
                            response = on_plan_approval_request(summary, plan)
                            if isinstance(response, tuple) and len(response) >= 2:
                                accepted, custom_message = bool(response[0]), response[1]
                            else:
                                accepted, custom_message = bool(response), None
                        except Exception:
                            log.exception("on_plan_approval_request callback failed")
                            accepted, custom_message = False, None
                        if accepted:
                            base_msg = (
                                f"Willow approved your plan: \"{summary}\". "
                                f"You may proceed with the {len(plan)} step(s) "
                                f"in your next turns."
                            )
                            if custom_message:
                                result = f"{base_msg}\n\nWillow added: \"{custom_message}\""
                            else:
                                result = base_msg
                        else:
                            if custom_message:
                                result = (
                                    f"Willow declined the plan and said:\n\n"
                                    f"  \"{custom_message}\""
                                )
                            else:
                                result = (
                                    "Willow declined the plan. The conversation "
                                    "continues; you may revise the plan and ask "
                                    "again, or simply continue without the "
                                    "multi-step work."
                                )
                    else:
                        result = (
                            "Plan approval requested but no operator confirmation "
                            "handler is wired in this client. Please ask Willow "
                            "conversationally."
                        )
                # Special-case: git_push is operator-gated (with allowlist short-circuit).
                elif name == "git_push":
                    repo_arg = args.get("repo", "")
                    remote_arg = args.get("remote", "origin")

                    # Resolve repo path + look up remote URL for the prompt.
                    try:
                        from ._git_helpers import (
                            GitError,
                            get_remote_url,
                            resolve_repo,
                            run_git,
                        )
                        repo_path = resolve_repo(repo_arg, write=True)
                        remote_url = get_remote_url(repo_path, remote_arg) or "(unknown URL)"
                    except (GitError, ImportError) as e:
                        result = f"git_push setup failed: {e}"
                    else:
                        # Substring match — "github.com/foo/bar" matches both
                        # ".../bar" and ".../bar.git" forms.
                        allowlist = list(self.config.git.push_allowlist)
                        on_allowlist = is_git_push_allowlisted(remote_url, allowlist)

                        if on_allowlist:
                            # Auto-approve — dispatch directly.
                            result = self.tools.dispatch(name, args)
                        elif on_git_push_request is not None:
                            # Off-allowlist: gather pending-commit summary for the prompt.
                            log_rc, log_stdout, _ = run_git(
                                repo_path,
                                ["log", "--oneline", "@{u}..HEAD"],
                            )
                            if log_rc == 0 and log_stdout.strip():
                                commits = log_stdout.strip().split("\n")
                            else:
                                commits = ["(no pending commits — push may be a no-op)"]

                            try:
                                response = on_git_push_request(repo_arg, remote_url, commits)
                                if isinstance(response, tuple) and len(response) >= 2:
                                    accepted, custom_message = bool(response[0]), response[1]
                                else:
                                    accepted, custom_message = bool(response), None
                            except Exception:
                                log.exception("on_git_push_request callback failed")
                                accepted, custom_message = False, None

                            if accepted:
                                push_result = self.tools.dispatch(name, args)
                                if custom_message:
                                    result = f"{push_result}\n\nWillow added: \"{custom_message}\""
                                else:
                                    result = push_result
                            else:
                                if custom_message:
                                    result = (
                                        f"Willow declined the push and said:\n\n"
                                        f"  \"{custom_message}\""
                                    )
                                else:
                                    result = (
                                        "Willow declined the push silently. "
                                        "The conversation continues; you may "
                                        "revise or simply continue without "
                                        "pushing right now."
                                    )
                        else:
                            result = (
                                "git_push requested but no operator confirmation "
                                "handler is wired in this client. The push was "
                                "not performed."
                            )
                # Special-case: delete_path is operator-gated. Every delete
                # pings Willow — never auto-approves, by design.
                elif name == "delete_path":
                    raw_path = args.get("path", "")
                    recursive = bool(args.get("recursive", False))

                    # Pre-flight: validate before pinging the operator so
                    # bad-shape requests get a clear answer the partner can
                    # act on without bothering Willow.
                    target = None
                    pre_error: str | None = None
                    if not raw_path:
                        pre_error = "Error: path is required for delete_path."
                    else:
                        try:
                            from partner_client.paths import PathError, resolve_path
                            target = resolve_path(raw_path, write=True)
                        except PathError as e:
                            pre_error = f"Error: {e}"
                        except ImportError:
                            pre_error = (
                                "Error: path resolver not available; client "
                                "may be misconfigured."
                            )
                        else:
                            if not target.exists():
                                pre_error = f"Error: path does not exist: {target}"
                            elif target.is_dir() and not recursive:
                                try:
                                    has_children = any(target.iterdir())
                                except OSError as e:
                                    pre_error = (
                                        f"Error reading directory {target}: {e}"
                                    )
                                else:
                                    if has_children:
                                        pre_error = (
                                            f"Error: {target} is a non-empty "
                                            f"directory. Pass recursive=true to "
                                            f"delete it and its contents."
                                        )

                    if pre_error is not None:
                        result = pre_error
                    elif on_delete_path_request is None:
                        result = (
                            "delete_path requested but no operator "
                            "confirmation handler is wired in this client. "
                            "Nothing was deleted."
                        )
                    else:
                        # Build a useful summary for the operator prompt.
                        is_dir = target.is_dir()
                        if is_dir:
                            try:
                                file_count = sum(
                                    1 for p in target.rglob("*") if p.is_file()
                                )
                                dir_count = sum(
                                    1 for p in target.rglob("*") if p.is_dir()
                                )
                                summary = (
                                    f"directory containing {file_count} file(s) "
                                    f"and {dir_count} subdirectory(ies)"
                                )
                            except OSError:
                                summary = "directory"
                        else:
                            try:
                                size = target.stat().st_size
                                summary = f"file ({size:,} bytes)"
                            except OSError:
                                summary = "file"

                        if self.timeline is not None:
                            self.timeline.record(
                                "delete_path_requested",
                                path=str(target),
                                recursive=recursive,
                                summary=summary,
                            )

                        try:
                            response = on_delete_path_request(
                                target, recursive, summary
                            )
                            if isinstance(response, tuple) and len(response) >= 2:
                                accepted, custom_message = (
                                    bool(response[0]),
                                    response[1],
                                )
                            else:
                                accepted, custom_message = bool(response), None
                        except Exception:
                            log.exception("on_delete_path_request callback failed")
                            accepted, custom_message = False, None

                        if self.timeline is not None:
                            self.timeline.record(
                                "delete_path_decision",
                                path=str(target),
                                recursive=recursive,
                                accepted=accepted,
                                custom_message=bool(custom_message),
                            )

                        if accepted:
                            try:
                                if is_dir and recursive:
                                    import shutil
                                    shutil.rmtree(target)
                                elif is_dir:
                                    target.rmdir()  # empty dir
                                else:
                                    target.unlink()
                                base_msg = (
                                    f"Willow approved the delete. "
                                    f"Removed: {target}"
                                )
                                if custom_message:
                                    result = (
                                        f"{base_msg}\n\nWillow added: "
                                        f"\"{custom_message}\""
                                    )
                                else:
                                    result = base_msg
                            except OSError as e:
                                result = f"Delete failed: {e}"
                        else:
                            if custom_message:
                                result = (
                                    f"Willow declined the delete and said:\n\n"
                                    f"  \"{custom_message}\""
                                )
                            else:
                                result = (
                                    "Willow declined the delete silently. "
                                    "Nothing was removed; the path is unchanged."
                                )
                # Special-case: protect_save is operator-gated. Identity-
                # bearing dual-write (active + dated archive) only happens
                # after Willow sees the proposed content and approves.
                elif name == "protect_save":
                    raw_content = args.get("content", "") or ""
                    raw_note = args.get("note", "") or ""

                    if not raw_content.strip():
                        result = (
                            "Error: protect_save requires non-empty content. "
                            "Pass the verbatim exchanges you want preserved."
                        )
                    elif on_protect_save_request is None:
                        result = (
                            "protect_save requested but no operator "
                            "confirmation handler is wired in this client. "
                            "Nothing was written."
                        )
                    else:
                        # Build a short preview for the operator prompt.
                        # Full content goes to the consent callback for
                        # operator review; this preview is just the headline.
                        preview_lines = raw_content.strip().splitlines()
                        if len(preview_lines) > 6:
                            preview = "\n".join(preview_lines[:6]) + "\n..."
                        else:
                            preview = raw_content.strip()

                        if self.timeline is not None:
                            self.timeline.record(
                                "protect_save_requested",
                                content_chars=len(raw_content),
                                note=raw_note,
                                session_num=session.session_num,
                            )

                        try:
                            response = on_protect_save_request(
                                raw_content, raw_note, preview
                            )
                            if isinstance(response, tuple) and len(response) >= 2:
                                accepted, custom_message = (
                                    bool(response[0]),
                                    response[1],
                                )
                            else:
                                accepted, custom_message = bool(response), None
                        except Exception:
                            log.exception("on_protect_save_request callback failed")
                            accepted, custom_message = False, None

                        if self.timeline is not None:
                            self.timeline.record(
                                "protect_save_decision",
                                accepted=accepted,
                                custom_message=bool(custom_message),
                            )

                        if accepted:
                            try:
                                from .tools_builtin.protect_save import save as protect_save_fn
                                memory_dir = self.config.resolve(
                                    self.config.memory.memory_dir
                                )
                                active_path, dated_path = protect_save_fn(
                                    memory_dir=memory_dir,
                                    partner_name=self.config.identity.name,
                                    session_num=session.session_num,
                                    content=raw_content,
                                )
                                base_msg = (
                                    f"Willow approved the protect. Wrote:\n"
                                    f"  active:  {active_path}\n"
                                    f"  archive: {dated_path}\n"
                                    f"Both files contain identical content with "
                                    f"the canonical MOSAIC header prepended. "
                                    f"The active file is the current sacred "
                                    f"selection (overwritten on each protect); "
                                    f"the dated archive is preserved."
                                )
                                if custom_message:
                                    result = (
                                        f"{base_msg}\n\nWillow added: "
                                        f"\"{custom_message}\""
                                    )
                                else:
                                    result = base_msg
                            except OSError as e:
                                result = f"Protect failed: {e}"
                        else:
                            if custom_message:
                                result = (
                                    f"Willow declined the protect and said:\n\n"
                                    f"  \"{custom_message}\""
                                )
                            else:
                                result = (
                                    "Willow declined the protect silently. "
                                    "Nothing was written; the conversation "
                                    "continues. You may revise the curation "
                                    "and try again."
                                )
                else:
                    result = self.tools.dispatch(name, args)

                tool_invocations.append((name, args, result))
                if self.timeline is not None:
                    self.timeline.record(
                        "tool_call",
                        iteration=iteration,
                        name=name,
                        args=args,
                        result_preview=result,
                        result_chars=len(result),
                        duration_ms=duration_ms(tool_started),
                    )
                session.append_tool_result(name, result, tool_call_id=tool_call_id)
                if ui is not None:
                    try:
                        ui.show_tool_call(name, args, result)
                    except Exception:
                        log.exception("ui.show_tool_call failed")

        # Hit max iterations — bail with whatever we have. The partner sees
        # this content as their final assistant message; phrase it so they
        # have somewhere to go conversationally rather than just "stopping."
        log.warning(f"Tool-call loop exceeded {max_iterations} iterations.")
        if self.timeline is not None:
            self.timeline.record(
                "tool_loop_limit",
                max_iterations=max_iterations,
                tool_invocation_count=len(tool_invocations),
                context_tokens=session.estimate_tokens(),
            )
        bail_msg = (
            f"(I've made {max_iterations} tool calls in this turn — the "
            f"safety limit kicked in before I could finish. I do have the "
            f"results from those calls in my context; ask me to summarize "
            f"what I gathered so far, or to continue from where I am, and "
            f"I'll pick up the rest. If this happens regularly with "
            f"legitimate multi-step work, the operator can raise "
            f"`[model] max_tool_iterations` in the TOML.)"
        )
        return ChatResponse(
            content=bail_msg,
            thinking=None,
            tool_invocations=tool_invocations,
        )

    @staticmethod
    def _get_message(chunk: Any) -> Any:
        """Extract the message payload from an Ollama chunk (dict or SDK object)."""
        if isinstance(chunk, dict):
            return chunk.get("message")
        return getattr(chunk, "message", None)

    def _messages_for_ollama(self, messages: list[dict]) -> list[dict]:
        """Convert internal session messages to the format ollama.chat expects.

        Mostly identity, but we drop fields Ollama doesn't recognize and we
        propagate `tool_call_id` on role=tool messages so newer Ollama versions
        can correlate parallel tool results to their originating calls.
        """
        out = []
        for m in messages:
            entry: dict[str, Any] = {"role": m["role"], "content": m.get("content", "")}
            if "images" in m:
                entry["images"] = m["images"]
            if "tool_calls" in m and m.get("role") == "assistant":
                entry["tool_calls"] = m["tool_calls"]
            if m.get("role") == "tool":
                if "name" in m:
                    entry["name"] = m["name"]
                if m.get("tool_call_id"):
                    entry["tool_call_id"] = m["tool_call_id"]
            out.append(entry)
        return out

    @staticmethod
    def _get_field(message: Any, field: str) -> Any:
        if isinstance(message, dict):
            return message.get(field)
        return getattr(message, field, None)

    @staticmethod
    def _normalize_tool_calls(tool_calls: Any) -> list[dict]:
        """Convert ollama tool_calls (which may be SDK objects) to plain dicts.

        Preserves the `id` field when present so result-to-call correlation
        survives across the chat loop.
        """
        out = []
        for tc in tool_calls:
            if isinstance(tc, dict):
                out.append(tc)
                continue
            # Convert SDK object to dict
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            entry: dict[str, Any] = {
                "function": {
                    "name": getattr(fn, "name", "") or (fn.get("name", "") if isinstance(fn, dict) else ""),
                    "arguments": getattr(fn, "arguments", {}) or (fn.get("arguments", {}) if isinstance(fn, dict) else {}),
                }
            }
            tc_id = getattr(tc, "id", None)
            if tc_id:
                entry["id"] = tc_id
            out.append(entry)
        return out
