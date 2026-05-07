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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .config import Config
from .session import Session
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
        os.environ["PARTNER_CLIENT_HUB_DIR"] = config.hub.path
        os.environ["PARTNER_CLIENT_HUB_PARTNER"] = (
            config.hub.partner_name or config.identity.name.lower()
        )
    else:
        os.environ.pop("PARTNER_CLIENT_HUB_DIR", None)
        os.environ.pop("PARTNER_CLIENT_HUB_PARTNER", None)

    return all_scopes


class OllamaClient:
    def __init__(self, config: Config, tools: ToolRegistry):
        self.config = config
        self.tools = tools
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
        """
        tool_invocations: list[tuple[str, dict, str]] = []
        # Chat-loop iteration cap (configurable via [model] max_tool_iterations).
        # Each iteration is one model invocation; multi-tool plans accumulate
        # iterations as `tool_call → response → tool_call → response → …`.
        max_iterations = self.config.model.max_tool_iterations

        for _ in range(max_iterations):
            content_buf: list[str] = []
            thinking_buf: list[str] = []
            tool_calls: list = []
            stream_open_emitted = False

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
            finally:
                if stream_open_emitted and ui is not None:
                    try:
                        ui.stream_close()
                    except Exception:
                        log.exception("ui.stream_close failed")

            full_content = "".join(content_buf)
            full_thinking = "".join(thinking_buf) if thinking_buf else None

            if not tool_calls:
                # Final response — append to session and return
                session.append_assistant(full_content, thinking=full_thinking)
                return ChatResponse(
                    content=full_content,
                    thinking=full_thinking,
                    tool_invocations=tool_invocations,
                )

            # Model wants to call tools. Append the assistant message (which
            # contains the tool_calls), then execute each tool and append results.
            normalized_tool_calls = self._normalize_tool_calls(tool_calls)
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

                # Special-case: request_checkpoint is operator-gated.
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
                            try:
                                path = session.checkpoint(summary=reason)
                                base_msg = (
                                    f"Willow accepted your checkpoint request. "
                                    f"Session-status saved at {path}. "
                                    f"current.json was also snapshotted. "
                                    f"You may continue the conversation."
                                )
                                if custom_message:
                                    result = f"{base_msg}\n\nWillow added: \"{custom_message}\""
                                else:
                                    result = base_msg
                            except Exception as e:
                                result = f"Checkpoint failed: {e}"
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
                else:
                    result = self.tools.dispatch(name, args)

                tool_invocations.append((name, args, result))
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
