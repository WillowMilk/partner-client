"""MLXClient — Apple MLX-Metal backend via mlx_lm.server.

Split into its own module to keep client.py readable. Imported into
client.py's namespace at the bottom so make_chat_client (which lives
in client.py) can reference it directly.

See client.py docstring for the OllamaClient counterpart and the
make_chat_client factory.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from .config import Config
from .session import Session
from .timeline import RunTimeline, duration_ms
from .tools import ToolRegistry

log = logging.getLogger(__name__)


class MLXClient:
    """Apple MLX-Metal backend via mlx_lm.server (OpenAI-compatible HTTP).

    Parallel to OllamaClient. Same public surface (prewarm, chat, scopes
    property) so __main__ can swap backends via the make_chat_client
    factory without caring which one is active.

    Talks to mlx_lm.server via the official openai Python SDK pointed at
    the server's OpenAI-compatible /v1 endpoint. Confirmed end-to-end
    against Gemma 4 31B IT BF16 on M4 Max during Session 32 Day 7
    verification:

      - Chat completions: clean content, finish_reason="stop"
      - Streaming SSE: delta.content + delta.reasoning + delta.tool_calls
      - Tool calling: OpenAI-format finish_reason="tool_calls" + structured
        tool_calls array (no adapter shims required)
      - Reasoning exposed as separate `reasoning` delta field; maps to
        partner-client's internal `thinking` field so UI + storage +
        /show-thinking work unchanged across backends.

    Tool dispatch + consent gates delegate to module-level
    dispatch_one_tool_call (same helper OllamaClient uses) — behavior
    across backends is identical for git_push, delete_path,
    request_plan_approval, request_checkpoint, protect_save.
    """

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
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError(
                "The 'openai' package is required for backend='mlx-lm'. "
                "Run: pip install openai"
            ) from e
        # mlx_lm.server doesn't require auth; pass a placeholder key to
        # satisfy the SDK's "api_key is required" startup check.
        self._client = OpenAI(
            base_url=self.config.model.mlx_server_url,
            api_key="not-needed",
        )
        # Tracks an auto-started server subprocess (if we launched one)
        # so __main__ can shut it down at exit. None when the operator
        # started mlx_lm.server externally.
        self._server_proc = None

        # Lazy import to avoid circular import at module load time.
        from .client import setup_scope_env
        self._scopes = setup_scope_env(config)

        # Server lifecycle: if auto-start is enabled and the server isn't
        # already reachable, launch it now. Otherwise assume the operator
        # is running it externally.
        if self.config.model.mlx_auto_start_server:
            self._ensure_server_running()

    def _server_reachable(self) -> bool:
        """Probe the server's /v1/models endpoint to see if it's responsive.

        Returns True on HTTP 200 with parseable JSON, False on any other
        outcome (connection refused, timeout, malformed response). Used
        before launching a child process to avoid spawning a duplicate
        server on the same port.
        """
        try:
            self._client.models.list()
            return True
        except Exception:
            return False

    def _ensure_server_running(self) -> None:
        """Launch mlx_lm.server as a subprocess if it isn't already running.

        Waits up to model.mlx_server_start_timeout seconds for the server
        to become reachable. Server stdout/stderr are inherited (visible to
        the operator) so model-load progress and errors are surfaced.

        Skipped silently if the server is already reachable (operator may
        have started it externally for debugging or to share across multiple
        partner-client invocations).
        """
        if self._server_reachable():
            return  # Operator started it externally — leave alone.

        import shlex
        import subprocess
        import sys
        from urllib.parse import urlparse

        parsed = urlparse(self.config.model.mlx_server_url)
        port = parsed.port or 8080

        cmd = [
            sys.executable, "-m", "mlx_lm", "server",
            "--model", self.config.model.name,
            "--port", str(port),
        ]
        cmd.extend(self.config.model.mlx_server_extra_args)

        if self.timeline is not None:
            self.timeline.record(
                "mlx_server_launching",
                cmd=" ".join(shlex.quote(c) for c in cmd),
            )
        try:
            self._server_proc = subprocess.Popen(cmd)
        except (OSError, FileNotFoundError) as e:
            raise RuntimeError(
                f"Failed to launch mlx_lm.server: {e}\n"
                f"Either install it (pip install mlx-lm) or set "
                f"[model] mlx_auto_start_server = false and run the server "
                f"externally."
            ) from e

        # Poll until reachable or timeout.
        started = time.perf_counter()
        deadline = started + self.config.model.mlx_server_start_timeout
        while time.perf_counter() < deadline:
            if self._server_proc.poll() is not None:
                # Process exited before becoming reachable
                raise RuntimeError(
                    f"mlx_lm.server exited with code {self._server_proc.returncode} "
                    f"before becoming reachable. Check the server's stderr output."
                )
            if self._server_reachable():
                if self.timeline is not None:
                    self.timeline.record(
                        "mlx_server_ready",
                        duration_ms=int((time.perf_counter() - started) * 1000),
                    )
                return
            time.sleep(0.5)

        # Timed out. Kill the child so we don't leave a zombie.
        try:
            self._server_proc.terminate()
        except Exception:
            pass
        raise RuntimeError(
            f"mlx_lm.server didn't become reachable within "
            f"{self.config.model.mlx_server_start_timeout}s. Check the model "
            f"name ({self.config.model.name!r}) and that the model is "
            f"downloaded to the HuggingFace cache."
        )

    @property
    def scopes(self) -> list[dict]:
        return self._scopes

    def prewarm(self) -> tuple[bool, float, str | None]:
        """Pre-load the model in mlx_lm.server with a minimal completion.

        Same shape as OllamaClient.prewarm: tiny chat call with 1-token
        prediction budget. mlx_lm.server loads the model on first inference
        request, so this forces the load at startup.
        """
        started = time.perf_counter()
        try:
            self._client.chat.completions.create(
                model=self.config.model.name,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
                temperature=0.0,
                stream=False,
            )
        except Exception as e:
            elapsed = time.perf_counter() - started
            if self.timeline is not None:
                self.timeline.record(
                    "prewarm_error",
                    error=str(e),
                    duration_ms=int(elapsed * 1000),
                )
            return False, elapsed, str(e)
        elapsed = time.perf_counter() - started
        if self.timeline is not None:
            self.timeline.record(
                "prewarm_complete",
                duration_ms=int(elapsed * 1000),
            )
        return True, elapsed, None

    def chat(
        self,
        session: Session,
        ui=None,
        on_plan_approval_request: Callable | None = None,
        on_git_push_request: Callable | None = None,
        on_delete_path_request: Callable | None = None,
    ):
        """Run the chat loop against mlx_lm.server until a final response.

        Mirrors OllamaClient.chat's behavior. Differences from Ollama:
          - Uses openai SDK streaming (delta-shaped chunks vs message-shaped)
          - tool_calls stream as deltas with index, name, arguments fragments;
            assembled per-index across chunks
          - reasoning field replaces Ollama's `thinking` field (mapped to
            internal `thinking` representation for partner-client consistency)
          - mlx_lm.server doesn't accept a `think` parameter; thinking is
            controlled by the model's chat template. For Gemma 4 IT, the
            model always produces a reasoning block. Flow vs Analysis mode
            in mlx-lm backend reduces to whether we DISPLAY the thinking
            (handled by show_thinking's mode check), not whether the model
            generates it. The reasoning field is still captured either way.
        """
        # Lazy import to avoid circular: client.py defines these and imports us.
        from .client import dispatch_one_tool_call, ChatResponse

        tool_invocations: list[tuple[str, dict, str]] = []
        max_iterations = self.config.model.max_tool_iterations

        for iteration in range(1, max_iterations + 1):
            content_buf: list[str] = []
            thinking_buf: list[str] = []
            tool_calls_accum: dict[int, dict] = {}  # per-index accumulation
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
                stream = self._client.chat.completions.create(
                    model=self.config.model.name,
                    messages=self._messages_for_openai(session.messages),
                    tools=self.tools.schemas() or None,
                    temperature=self.config.model.temperature,
                    top_p=self.config.model.top_p,
                    max_tokens=self.config.model.num_predict,
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
                raise RuntimeError(f"mlx_lm.server chat call failed: {e}") from e

            try:
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta

                    # Content delta
                    content_delta = getattr(delta, "content", None)
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

                    # Reasoning delta -> internal thinking
                    reasoning_delta = getattr(delta, "reasoning", None)
                    if reasoning_delta:
                        thinking_buf.append(reasoning_delta)

                    # Tool calls stream as deltas keyed by index; accumulate.
                    chunk_tcs = getattr(delta, "tool_calls", None)
                    if chunk_tcs:
                        for tc_delta in chunk_tcs:
                            idx = getattr(tc_delta, "index", 0) or 0
                            slot = tool_calls_accum.setdefault(idx, {
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            })
                            tc_id = getattr(tc_delta, "id", None)
                            if tc_id:
                                slot["id"] = tc_id
                            fn = getattr(tc_delta, "function", None)
                            if fn is not None:
                                fn_name = getattr(fn, "name", None)
                                if fn_name:
                                    slot["function"]["name"] = fn_name
                                fn_args = getattr(fn, "arguments", None)
                                if fn_args:
                                    slot["function"]["arguments"] += fn_args
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

            tool_calls = [
                tool_calls_accum[k] for k in sorted(tool_calls_accum.keys())
            ]

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

            if self.timeline is not None:
                self.timeline.record(
                    "model_call_end",
                    iteration=iteration,
                    duration_ms=duration_ms(model_started),
                    content_chars=len(full_content),
                    thinking_chars=len(full_thinking or ""),
                    tool_call_count=len(tool_calls),
                )
            session.append_assistant(
                content=full_content,
                thinking=full_thinking,
                tool_calls=tool_calls,
            )

            for tc in tool_calls:
                name = tc["function"]["name"]
                args_raw = tc["function"]["arguments"]
                tool_call_id = tc.get("id", "") or ""
                if isinstance(args_raw, dict):
                    args = args_raw
                else:
                    try:
                        args = json.loads(args_raw) if args_raw else {}
                    except json.JSONDecodeError:
                        args = {}
                tool_started = time.perf_counter()

                result = dispatch_one_tool_call(
                    name=name,
                    args=args,
                    tool_call_id=tool_call_id,
                    config=self.config,
                    tools=self.tools,
                    timeline=self.timeline,
                    session=session,
                    on_plan_approval_request=on_plan_approval_request,
                    on_git_push_request=on_git_push_request,
                    on_delete_path_request=on_delete_path_request,
                )

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

            continue

        # Hit max iterations — same bail shape as OllamaClient
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

    def _messages_for_openai(self, messages: list[dict]) -> list[dict]:
        """Convert internal session messages to OpenAI chat-completions format.

        Tool messages need tool_call_id. Assistant messages with tool_calls
        keep the OpenAI tool_calls shape with content=None per OpenAI spec.
        Images aren't supported in this conversion yet (vision-message-
        format conversion is a separate intention).
        """
        out: list[dict[str, Any]] = []
        for m in messages:
            role = m["role"]
            entry: dict[str, Any] = {"role": role, "content": m.get("content", "")}
            if role == "assistant" and "tool_calls" in m:
                entry["tool_calls"] = m["tool_calls"]
                if not entry["content"]:
                    entry["content"] = None
            if role == "tool":
                if m.get("tool_call_id"):
                    entry["tool_call_id"] = m["tool_call_id"]
                if "name" in m:
                    entry["name"] = m["name"]
            out.append(entry)
        return out

    def close(self) -> None:
        """Shut down an auto-started mlx_lm.server subprocess, if any.

        Idempotent. Called by __main__ at clean exit. Operator-started
        servers (when mlx_auto_start_server=False) are not touched.
        """
        if self._server_proc is not None:
            try:
                self._server_proc.terminate()
                self._server_proc.wait(timeout=10)
            except Exception:
                log.exception("mlx_lm.server shutdown failed; forcing kill")
                try:
                    self._server_proc.kill()
                except Exception:
                    pass
            self._server_proc = None
