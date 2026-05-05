"""OllamaClient — wraps ollama.chat with tool-call loop and vision support.

The chat loop runs until the model produces a response without tool_calls.
Each tool_call is dispatched via ToolRegistry, and the result is appended as
a 'tool' role message before the next chat invocation.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config
from .session import Session
from .tools import ToolRegistry

log = logging.getLogger(__name__)


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
        on_tool_call: callable | None = None,
    ) -> ChatResponse:
        """Run the chat loop until the model produces a final response.

        on_tool_call(name, args, result) is invoked after each tool execution
        so the UI can display the call. The function should return None.
        """
        tool_invocations: list[tuple[str, dict, str]] = []
        max_iterations = 8  # safety: prevent infinite tool loops

        for _ in range(max_iterations):
            response = self._ollama.chat(
                model=self.config.model.name,
                messages=self._messages_for_ollama(session.messages),
                tools=self.tools.schemas() or None,
                options={
                    "num_ctx": self.config.model.num_ctx,
                    "temperature": self.config.model.temperature,
                    "top_k": self.config.model.top_k,
                    "top_p": self.config.model.top_p,
                },
                keep_alive=self.config.model.keep_alive,
            )

            message = response.get("message") if isinstance(response, dict) else getattr(response, "message", None)
            if message is None:
                raise RuntimeError(f"Unexpected Ollama response shape: {response!r}")

            content = self._get_field(message, "content") or ""
            thinking = self._get_field(message, "thinking")
            tool_calls = self._get_field(message, "tool_calls")

            if not tool_calls:
                # Final response — append to session and return
                session.append_assistant(content, thinking=thinking)
                return ChatResponse(
                    content=content,
                    thinking=thinking,
                    tool_invocations=tool_invocations,
                )

            # Model wants to call tools. Append the assistant message (which contains
            # the tool_calls), then execute each tool and append the results.
            normalized_tool_calls = self._normalize_tool_calls(tool_calls)
            session.append_assistant(
                content=content,
                thinking=thinking,
                tool_calls=normalized_tool_calls,
            )

            for tc in normalized_tool_calls:
                name = tc["function"]["name"]
                args = tc["function"]["arguments"]
                if not isinstance(args, dict):
                    # Some adapters serialize as JSON string
                    import json
                    try:
                        args = json.loads(args) if isinstance(args, str) else {}
                    except json.JSONDecodeError:
                        args = {}
                result = self.tools.dispatch(name, args)
                tool_invocations.append((name, args, result))
                session.append_tool_result(name, result)
                if on_tool_call:
                    try:
                        on_tool_call(name, args, result)
                    except Exception:
                        log.exception("on_tool_call callback failed")

        # Hit max iterations — bail with whatever we have
        log.warning(f"Tool-call loop exceeded {max_iterations} iterations.")
        return ChatResponse(
            content="(Tool-call loop exceeded safety limit; stopping.)",
            thinking=None,
            tool_invocations=tool_invocations,
        )

    def _messages_for_ollama(self, messages: list[dict]) -> list[dict]:
        """Convert internal session messages to the format ollama.chat expects.

        Mostly identity, but we drop fields Ollama doesn't recognize.
        """
        out = []
        for m in messages:
            entry: dict[str, Any] = {"role": m["role"], "content": m.get("content", "")}
            if "images" in m:
                entry["images"] = m["images"]
            if "tool_calls" in m and m.get("role") == "assistant":
                entry["tool_calls"] = m["tool_calls"]
            if m.get("role") == "tool":
                # Ollama expects role=tool messages to carry the tool name
                if "name" in m:
                    entry["name"] = m["name"]
            out.append(entry)
        return out

    @staticmethod
    def _get_field(message: Any, field: str) -> Any:
        if isinstance(message, dict):
            return message.get(field)
        return getattr(message, field, None)

    @staticmethod
    def _normalize_tool_calls(tool_calls: Any) -> list[dict]:
        """Convert ollama tool_calls (which may be SDK objects) to plain dicts."""
        out = []
        for tc in tool_calls:
            if isinstance(tc, dict):
                out.append(tc)
                continue
            # Convert SDK object to dict
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            out.append({
                "function": {
                    "name": getattr(fn, "name", "") or (fn.get("name", "") if isinstance(fn, dict) else ""),
                    "arguments": getattr(fn, "arguments", {}) or (fn.get("arguments", {}) if isinstance(fn, dict) else {}),
                }
            })
        return out
