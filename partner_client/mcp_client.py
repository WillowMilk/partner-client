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
import os
import re
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any


# ${VAR} / ${VAR:-default} reference pattern for env-value expansion.
# Secrets live in a .env file (loaded into os.environ by config._load_dotenv)
# or the live shell environment; the TOML references them by name so the
# config never contains plaintext secrets. Per Willow's 2026-05-28 call:
# reference > hardcode for reliability + future-proofing + painless rotation.
_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env_refs(env: dict[str, str]) -> dict[str, str]:
    """Expand ${VAR} and ${VAR:-default} references in env-dict values
    against os.environ. Unset vars with no default expand to empty string
    (and log a warning — a missing API key is worth surfacing). Values
    without references pass through unchanged.
    """
    expanded: dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(value, str):
            expanded[key] = value
            continue

        def _sub(m: "re.Match[str]") -> str:
            var_name = m.group(1)
            default = m.group(2)
            if var_name in os.environ:
                return os.environ[var_name]
            if default is not None:
                return default
            log.warning(
                "MCP env reference ${%s} is unset (no .env entry, no shell var); "
                "expanding to empty string", var_name
            )
            return ""

        expanded[key] = _ENV_REF_RE.sub(_sub, value)
    return expanded

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

    Architecture (revised after first end-to-end smoke test, 2026-05-28):

    The MCP SDK uses anyio cancel scopes that REQUIRE enter + exit to happen
    in the same async task. Our first implementation entered the context
    in start_server's _do_start() coro and tried to exit it in shutdown's
    _do_shutdown() coro — different tasks, anyio rightly screamed.

    Canonical pattern: each MCP server gets a dedicated long-running task
    that:
        1. Enters the stdio_client + ClientSession context managers
        2. Calls initialize() + list_tools()
        3. Waits on an asyncio.Queue for tool-call requests
        4. Processes each request and pushes the result back via a Future
        5. On receiving a sentinel (None), exits the context managers in
           THIS SAME task (anyio is happy)

    Sync entry points (start_server/call_tool/shutdown_all) communicate with
    these tasks via thread-safe asyncio.run_coroutine_threadsafe + per-server
    request queues. All public methods remain synchronous + thread-safe.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._loop_ready = threading.Event()
        # Per-server async state, only touched from the background loop:
        #   queues[name] = asyncio.Queue of (op, payload, future)
        #     op = "call" payload = (tool_name, arguments)
        #     op = "shutdown" payload = None  -> drains + exits
        # Task references kept so we can await their completion on shutdown.
        self._queues: dict[str, "asyncio.Queue[Any]"] = {}
        self._tasks: dict[str, "asyncio.Task[Any]"] = {}
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
    # The per-server task — handles full session lifecycle in ONE task
    # ─────────────────────────────────────────────────────────────────

    async def _server_task(
        self,
        spec: McpServerSpec,
        queue: "asyncio.Queue[Any]",
        ready: "asyncio.Future[list[McpToolHandle]]",
    ) -> None:
        """Long-running task that owns a single MCP server's session.

        Holds the AsyncExitStack open from start to shutdown, signaling
        readiness via the `ready` future once tools are discovered. Pulls
        tool-call requests off `queue` and dispatches them. Exits the
        stack cleanly when a None sentinel is queued (shutdown signal).
        """
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        try:
            # Expand ${VAR} references in env values against os.environ
            # (populated from the .env file by config._load_dotenv + the
            # live shell environment). The MCP subprocess receives the
            # resolved secrets without them ever living in the TOML.
            #
            # CRITICAL: merge the spec env OVER the inherited default
            # environment (PATH, HOME, etc.) rather than replacing it.
            # StdioServerParameters with a bare {TAVILY_API_KEY: ...} dict
            # would strip PATH and break `npx`/`uvx` launchers. We start
            # from the MCP SDK's get_default_environment() (a safety-filtered
            # subset of the parent env) and overlay the resolved secrets.
            resolved_env = None
            if spec.env:
                try:
                    from mcp.client.stdio import get_default_environment
                    base_env = dict(get_default_environment())
                except Exception:
                    # Fallback: pass through the parent's PATH + HOME at minimum
                    base_env = {
                        k: v for k, v in os.environ.items()
                        if k in ("PATH", "HOME", "USER", "SHELL", "LANG", "TMPDIR")
                    }
                base_env.update(_expand_env_refs(spec.env))
                resolved_env = base_env
            params = StdioServerParameters(
                command=spec.command,
                args=spec.args,
                env=resolved_env,
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools_result = await session.list_tools()
                    handles: list[McpToolHandle] = []
                    for tool in tools_result.tools:
                        if spec.allowed_tools and tool.name not in spec.allowed_tools:
                            continue
                        handles.append(McpToolHandle(
                            server_name=spec.name,
                            tool_name=tool.name,
                            namespaced_name=f"mcp_{spec.name}_{tool.name}",
                            description=tool.description or "",
                            input_schema=dict(tool.inputSchema) if tool.inputSchema else {},
                        ))
                    if not ready.done():
                        ready.set_result(handles)

                    # Request loop — process tool calls until shutdown sentinel
                    while True:
                        item = await queue.get()
                        if item is None:
                            return  # exits the contexts in this task
                        tool_name, arguments, fut = item
                        try:
                            result = await session.call_tool(tool_name, arguments)
                            parts: list[str] = []
                            for content in result.content:
                                text = getattr(content, "text", None)
                                if text is not None:
                                    parts.append(text)
                            if result.isError:
                                joined = "\n".join(parts) if parts else "(no error detail)"
                                text_out = f"[MCP error from {spec.name}.{tool_name}] {joined}"
                            else:
                                text_out = "\n".join(parts) if parts else "(no text content)"
                            if not fut.done():
                                fut.set_result(text_out)
                        except Exception as e:
                            if not fut.done():
                                fut.set_exception(e)
        except Exception as e:
            # Surface startup errors via the ready future so start_server()
            # can raise them synchronously to the caller.
            if not ready.done():
                ready.set_exception(e)
            else:
                log.exception("MCP server '%s' task crashed mid-flight", spec.name)

    # ─────────────────────────────────────────────────────────────────
    # Server lifecycle (public sync API)
    # ─────────────────────────────────────────────────────────────────

    def start_server(self, spec: McpServerSpec, timeout: float = 30.0) -> list[McpToolHandle]:
        """Launch an MCP server + initialize it + discover its tools.

        Spawns a dedicated task in the background loop that owns the
        session's full lifecycle. Blocks until tools are discovered (or
        the task fails) then returns the handles.

        Idempotent: re-calling on an already-started server returns the
        cached handles.
        """
        with self._lock:
            if spec.name in self._tasks:
                return list(self._tool_handles.get(spec.name, []))

        if self._loop is None:
            raise RuntimeError("MCP loop not initialized")

        async def _do_spawn():
            queue: asyncio.Queue[Any] = asyncio.Queue()
            ready: asyncio.Future[list[McpToolHandle]] = self._loop.create_future()  # type: ignore
            task = asyncio.create_task(self._server_task(spec, queue, ready))
            handles = await ready
            return queue, task, handles

        queue, task, handles = self._submit(_do_spawn(), timeout=timeout)

        with self._lock:
            self._queues[spec.name] = queue
            self._tasks[spec.name] = task
            self._tool_handles[spec.name] = handles
        log.info("MCP server '%s' started; %d tools registered (after allowlist filter)", spec.name, len(handles))
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

        Queues the request on the server's task; blocks until the result
        future resolves (or times out). Raises RuntimeError if the server
        hasn't been started.
        """
        with self._lock:
            queue = self._queues.get(server_name)
        if queue is None:
            raise RuntimeError(f"MCP server '{server_name}' is not started")

        if self._loop is None:
            raise RuntimeError("MCP loop not initialized")

        # Build the future on the background loop (it lives there)
        async def _enqueue():
            fut: asyncio.Future[str] = self._loop.create_future()  # type: ignore
            await queue.put((tool_name, arguments, fut))
            return await fut

        return self._submit(_enqueue(), timeout=timeout)

    def shutdown_all(self) -> None:
        """Clean shutdown of all servers + the background loop.

        Sends None sentinel to each server's queue so the task exits its
        context managers in the same task that entered them (anyio-safe).
        Idempotent; registered via atexit.
        """
        if self._shutdown_called:
            return
        self._shutdown_called = True

        if self._loop is None or not self._loop.is_running():
            return

        async def _do_shutdown():
            with self._lock:
                queues = list(self._queues.items())
                tasks = list(self._tasks.items())
                self._queues.clear()
                self._tasks.clear()
                self._tool_handles.clear()
            # Send shutdown sentinels
            for _, queue in queues:
                await queue.put(None)
            # Wait for tasks to finish (with timeout per task)
            for name, task in tasks:
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except asyncio.TimeoutError:
                    log.warning("MCP server '%s' did not shut down within 5s; cancelling", name)
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass
                except Exception as e:
                    log.warning("Error awaiting MCP server '%s' task: %s", name, e)

        try:
            future = asyncio.run_coroutine_threadsafe(_do_shutdown(), self._loop)
            future.result(timeout=15.0)
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
