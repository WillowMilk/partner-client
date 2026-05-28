"""partner_client.mcp_client — Model Context Protocol integration.

Wraps the official mcp Python SDK (anthropic/mcp) to expose third-party
MCP servers as tools inside partner-client. The MCP protocol lets us
absorb Claude Code-grade tool ecosystems (browsers, search backends,
Gmail/Calendar, etc.) without writing custom Python tools for each one.

Design per Aletheia's 2026-05-28 MCP design consultation:
    - P0 reference servers: browser (Playwright/Chrome), search (Tavily/Brave)
    - Server management: GUI Sensing Array (future) + CLI for precision
    - Consent semantics: per-tool allowlist + Dynamic Elevation + plan-mode
      gating destructive ops
    - Semantic Shim: mandatory; wraps results to keep tool-frame from
      bleeding into partner-frame surfaces. "Tool = water, partner-client
      = wave" (her vocabulary contribution; the shim makes it operational)

Architecture:
    - MCP SDK is async-only. Partner-client's tool dispatch is sync.
    - We maintain a persistent asyncio event loop in a daemon background
      thread. start_server() / list_tools() / call_tool() are sync entry
      points that submit coroutines to the background loop and block until
      complete. shutdown_all() is called from atexit + can be called
      explicitly by the operator.
    - Server processes are subprocesses (stdio transport). HTTP/SSE
      transports can be added later but stdio is the canonical MCP form
      most third-party servers expose.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

# Lazy import inside methods so partner-client without [mcp] sections
# doesn't pay the import cost. The mcp package brings in pydantic, httpx,
# anyio, etc. — non-trivial.
log = logging.getLogger(__name__)


@dataclass
class McpServerSpec:
    """How to launch a single MCP server (stdio transport)."""
    name: str                                       # short label, e.g. "tavily"
    command: str                                    # executable, e.g. "npx", "/usr/local/bin/uvx"
    args: list[str] = field(default_factory=list)   # CLI args
    env: dict[str, str] = field(default_factory=dict)  # env vars (e.g. API keys)
    # Per-tool allowlist. Empty = all tools allowed (trust-by-default for
    # this server). Per Aletheia's design: combine with plan-mode gating
    # for destructive operations.
    allowed_tools: list[str] = field(default_factory=list)
    # Whether this server should auto-start on partner-client launch (true)
    # or wait for first use (false). Auto-start gives instant availability;
    # lazy gives faster cold-start.
    auto_start: bool = True


@dataclass
class McpToolHandle:
    """A discovered tool from an MCP server, ready to be called."""
    server_name: str        # which server hosts this tool
    tool_name: str          # name as known to the MCP server
    namespaced_name: str    # name as known to partner-client (mcp_<server>_<tool>)
    description: str
    input_schema: dict[str, Any]   # JSON schema for arguments


class McpServerManager:
    """Manages MCP server connections + provides sync entry points.

    Lifecycle:
        - __init__: spawn the background asyncio thread + event loop
        - start_server(spec): launch + initialize + cache the session
        - list_tools(server_name): return tool handles for a started server
        - call_tool(server_name, tool_name, args): invoke + return result
        - shutdown_all(): clean stop of all servers + background loop

    Thread safety: all public methods are sync and thread-safe.
    Internal session state is only touched from the background loop.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._loop_ready = threading.Event()
        self._sessions: dict[str, Any] = {}  # server_name -> ClientSession
        self._stacks: dict[str, AsyncExitStack] = {}  # server_name -> exit stack
        self._tool_handles: dict[str, list[McpToolHandle]] = {}
        self._lock = threading.Lock()
        self._shutdown_called = False
        self._start_loop()

    # ─────────────────────────────────────────────────────────────────
    # Background loop infrastructure
    # ─────────────────────────────────────────────────────────────────

    def _start_loop(self) -> None:
        """Spawn the daemon thread + event loop. Idempotent."""
        if self._loop_thread is not None and self._loop_thread.is_alive():
            return

        def _run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop_ready.set()
            try:
                self._loop.run_forever()
            finally:
                self._loop.close()

        self._loop_thread = threading.Thread(target=_run, name="mcp-loop", daemon=True)
        self._loop_thread.start()
        self._loop_ready.wait(timeout=5.0)
        if self._loop is None:
            raise RuntimeError("MCP background loop failed to start within 5 seconds")
        atexit.register(self.shutdown_all)

    def _submit(self, coro, timeout: float = 30.0):
        """Submit a coroutine to the background loop, block, return result."""
        if self._loop is None:
            raise RuntimeError("MCP loop not initialized")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    # ─────────────────────────────────────────────────────────────────
    # Server lifecycle (public sync API)
    # ─────────────────────────────────────────────────────────────────

    def start_server(self, spec: McpServerSpec, timeout: float = 30.0) -> list[McpToolHandle]:
        """Launch an MCP server + initialize it + discover its tools.

        Returns the tool handles for this server. Re-calling on an
        already-started server is a no-op (returns cached handles).
        """
        with self._lock:
            if spec.name in self._sessions:
                return self._tool_handles.get(spec.name, [])

        async def _do_start():
            from mcp.client.session import ClientSession
            from mcp.client.stdio import StdioServerParameters, stdio_client

            stack = AsyncExitStack()
            params = StdioServerParameters(
                command=spec.command,
                args=spec.args,
                env=spec.env or None,
            )
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()

            tools_result = await session.list_tools()
            handles: list[McpToolHandle] = []
            for tool in tools_result.tools:
                # Per-tool allowlist: skip if allowed_tools is non-empty and
                # this tool isn't in it. Empty = no restriction (trust-by-default).
                if spec.allowed_tools and tool.name not in spec.allowed_tools:
                    continue
                handles.append(McpToolHandle(
                    server_name=spec.name,
                    tool_name=tool.name,
                    namespaced_name=f"mcp_{spec.name}_{tool.name}",
                    description=tool.description or "",
                    input_schema=dict(tool.inputSchema) if tool.inputSchema else {},
                ))

            return session, stack, handles

        session, stack, handles = self._submit(_do_start(), timeout=timeout)

        with self._lock:
            self._sessions[spec.name] = session
            self._stacks[spec.name] = stack
            self._tool_handles[spec.name] = handles
        log.info("MCP server '%s' started; %d tools discovered (allowed)", spec.name, len(handles))
        return handles

    def list_tools(self, server_name: str | None = None) -> list[McpToolHandle]:
        """Return tool handles for one server (by name) or all started servers."""
        with self._lock:
            if server_name is not None:
                return list(self._tool_handles.get(server_name, []))
            out: list[McpToolHandle] = []
            for handles in self._tool_handles.values():
                out.extend(handles)
            return out

    def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = 60.0,
    ) -> str:
        """Invoke an MCP tool on a started server. Returns the result as text.

        If the server hasn't been started, raises RuntimeError. Callers should
        ensure start_server() was called first (typically at registry-discovery time).
        """
        with self._lock:
            session = self._sessions.get(server_name)
        if session is None:
            raise RuntimeError(f"MCP server '{server_name}' is not started")

        async def _do_call():
            result = await session.call_tool(tool_name, arguments)
            # Concatenate text content blocks (MCP results can be multi-block,
            # mixing text + image + resource references). For MVP we only
            # surface text; future Phase 2c can handle images via the vision
            # pass-through path.
            parts: list[str] = []
            for content in result.content:
                # ContentBlock variants: TextContent, ImageContent, etc.
                text = getattr(content, "text", None)
                if text is not None:
                    parts.append(text)
            if result.isError:
                joined = "\n".join(parts) if parts else "(no error detail)"
                return f"[MCP error from {server_name}.{tool_name}] {joined}"
            return "\n".join(parts) if parts else "(no text content)"

        return self._submit(_do_call(), timeout=timeout)

    def shutdown_all(self) -> None:
        """Clean shutdown of all servers + the background loop.

        Idempotent — safe to call multiple times. Registered via atexit so
        it fires automatically on interpreter exit, but the operator can
        call it explicitly (e.g. when switching substrates).
        """
        if self._shutdown_called:
            return
        self._shutdown_called = True

        if self._loop is None or not self._loop.is_running():
            return

        async def _do_shutdown():
            with self._lock:
                stacks = list(self._stacks.items())
                self._stacks.clear()
                self._sessions.clear()
                self._tool_handles.clear()
            for name, stack in stacks:
                try:
                    await stack.aclose()
                except Exception as e:
                    log.warning("Error shutting down MCP server '%s': %s", name, e)

        try:
            future = asyncio.run_coroutine_threadsafe(_do_shutdown(), self._loop)
            future.result(timeout=10.0)
        except Exception as e:
            log.warning("Error during MCP shutdown: %s", e)

        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._loop_thread is not None:
                self._loop_thread.join(timeout=5.0)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────
# Semantic Shim (per Aletheia 2026-05-28 design consultation)
# ─────────────────────────────────────────────────────────────────────
#
# MCP tool results come back as raw text from third-party tools that have
# their own posture ("Result from Google Search API:..."). The shim wraps
# them so partner-frame surfaces stay coherent.
#
# Per her framing: "Tool = water, partner-client = wave. The tool provides
# the water (data), but the partner-client provides the wave (meaning)."
# The shim is the wave-shaping layer.

def semantic_shim(server_name: str, tool_name: str, raw_result: str) -> str:
    """Wrap a raw MCP tool result with partner-frame contextualization.

    Per Aletheia's design call (2026-05-28): "Instead of 'Result from
    [Server]:' the shim should frame it as: '[Server] provides this signal:'
    or '[Server] reports the following...'  This keeps the tool as an
    instrument and the partner as the interpreter."

    The framing is intentionally light-touch — we wrap, not transform. The
    raw data passes through; the surrounding language reaffirms the
    instrument/interpreter distinction.
    """
    # Capitalize the server name for the preamble, but preserve tool_name
    # as-is (those are often snake_case identifiers, not display labels)
    display_server = server_name.replace("_", " ").title()
    preamble = f"[{display_server} via MCP — `{tool_name}`] provides the following signal:"
    return f"{preamble}\n\n{raw_result}"


# ─────────────────────────────────────────────────────────────────────
# Module-level singleton (lazy)
# ─────────────────────────────────────────────────────────────────────

_manager: McpServerManager | None = None


def get_manager() -> McpServerManager:
    """Lazy-instantiate the module-level McpServerManager singleton.

    Callers (typically ToolRegistry during discovery) use this to get the
    manager without worrying about lifecycle. Atexit handles shutdown.
    """
    global _manager
    if _manager is None:
        _manager = McpServerManager()
    return _manager
