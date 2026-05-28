"""Tool registry — discovers, validates, and dispatches tool calls.

Tool plugin contract:
    Each module in tools_builtin/ or the external tools dir exports:
      - TOOL_DEFINITION: dict matching Ollama's tools= JSON-schema format
      - execute(**kwargs) -> str: callable that returns a string result

The tools= array passed to ollama.chat is built from these definitions.
When the model returns tool_calls, dispatch by name to the right execute().
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
from pathlib import Path
from typing import Any, Callable

from .config import Config

log = logging.getLogger(__name__)


class ToolError(Exception):
    """Raised when a tool fails to load or dispatch."""


class ToolRegistry:
    def __init__(self, config: Config):
        self.config = config
        self._tools: dict[str, dict[str, Any]] = {}
        self._dispatchers: dict[str, Callable[..., str]] = {}

    def discover(self) -> None:
        """Load built-in tools, then external tools, then MCP servers, filtered by config."""
        self._load_builtin()
        self._load_external()
        self._load_mcp()
        self._filter_enabled()
        # Search routing runs LAST, after the enabled-filter: the unified
        # web_search meta-tool is always available when [search].active is set
        # (it's a capability, not a per-config toggle), and it hides the legacy
        # search tools it supersedes so the partner sees one clean surface.
        self._load_search()

    def _load_mcp(self) -> None:
        """Discover MCP server tools (per Aletheia's 2026-05-28 design).

        For each `[mcp.<name>]` block in config.mcp where command is set
        and auto_start=True, launch the server, list its tools, and
        register each as a namespaced partner-client tool with a
        dispatcher closure that:
            1. Calls McpServerManager.call_tool() to invoke the remote tool
            2. Applies semantic_shim() to wrap the raw result with
               partner-frame contextualization ("[Server] provides this
               signal:" rather than clinical "Result from API:")

        MCP server startup failures are logged but non-fatal — the rest
        of partner-client continues to work. The partner can still operate
        without that specific server's tools.
        """
        if not self.config.mcp:
            return

        # Lazy import to avoid the mcp SDK's startup cost for configs
        # that don't have [mcp.*] blocks
        try:
            from .mcp_client import get_manager, McpServerSpec, semantic_shim
        except ImportError as e:
            log.warning("MCP client unavailable; skipping [mcp.*] servers: %s", e)
            return

        manager = get_manager()

        for server_name, server_cfg in self.config.mcp.items():
            if not server_cfg.command:
                continue
            if not server_cfg.auto_start:
                continue
            spec = McpServerSpec(
                name=server_name,
                command=server_cfg.command,
                args=server_cfg.args,
                env=server_cfg.env,
                allowed_tools=server_cfg.allowed_tools,
                auto_start=server_cfg.auto_start,
            )
            try:
                handles = manager.start_server(spec)
            except Exception as e:
                log.warning("Failed to start MCP server '%s': %s", server_name, e)
                continue

            for handle in handles:
                # Build a tool_def matching the OpenAI / Ollama function-
                # calling schema (same shape as builtin TOOL_DEFINITION).
                tool_def = {
                    "type": "function",
                    "function": {
                        "name": handle.namespaced_name,
                        "description": (
                            f"[MCP · {server_name}] {handle.description}"
                            if handle.description
                            else f"[MCP · {server_name}] {handle.tool_name}"
                        ),
                        "parameters": handle.input_schema or {
                            "type": "object",
                            "properties": {},
                        },
                    },
                }
                # Closure captures the handle + manager for dispatch.
                # Note: capture variables explicitly in default args to
                # avoid the classic late-binding trap (every closure would
                # otherwise reference the same loop variables).
                def _make_dispatcher(h=handle, mgr=manager):
                    def _dispatch(**kwargs) -> str:
                        raw = mgr.call_tool(h.server_name, h.tool_name, kwargs)
                        return semantic_shim(h.server_name, h.tool_name, raw)
                    return _dispatch

                self._tools[handle.namespaced_name] = tool_def
                self._dispatchers[handle.namespaced_name] = _make_dispatcher()
            log.info(
                "MCP server '%s' registered: %d tools (%s)",
                server_name,
                len(handles),
                ", ".join(h.tool_name for h in handles),
            )

    def _load_builtin(self) -> None:
        """Discover modules in partner_client.tools_builtin."""
        from . import tools_builtin
        builtin_dir = Path(tools_builtin.__file__).parent
        for py_file in sorted(builtin_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_name = f"partner_client.tools_builtin.{py_file.stem}"
            self._register_module(module_name, py_file)

    def _load_external(self) -> None:
        """Discover modules in the external tools dir (config-specified, relative to home_dir)."""
        ext_dir = self.config.resolve(self.config.tools.external_tools_dir)
        if not ext_dir.is_dir():
            return
        for py_file in sorted(ext_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_name = f"partner_client_external.{py_file.stem}"
            self._register_module(module_name, py_file)

    def _register_module(self, module_name: str, py_file: Path) -> None:
        """Load a module from a path and extract its TOOL_DEFINITION + execute()."""
        try:
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                log.warning(f"Could not load tool spec from {py_file}")
                return
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as e:
            log.warning(f"Failed to import tool module {py_file}: {e}")
            return

        tool_def = getattr(module, "TOOL_DEFINITION", None)
        execute = getattr(module, "execute", None)

        if tool_def is None or execute is None:
            log.warning(f"Tool module {py_file} missing TOOL_DEFINITION or execute()")
            return

        try:
            name = tool_def["function"]["name"]
        except (KeyError, TypeError):
            log.warning(f"Tool module {py_file}: TOOL_DEFINITION missing function.name")
            return

        self._tools[name] = tool_def
        self._dispatchers[name] = execute

    def _filter_enabled(self) -> None:
        """Drop tools not in config.tools.enabled.

        MCP tools (prefix `mcp_`) are exempt from this filter — they have
        their own per-tool allowlist mechanism in McpServerConfig.allowed_tools,
        applied at MCP discovery time. Double-gating in tools.enabled would
        force operators to maintain two separate allowlists for the same
        tools (one in the [mcp.server] block, one in [tools].enabled), which
        is friction without safety benefit. The MCP-level allowlist is
        authoritative for MCP tools.
        """
        enabled = set(self.config.tools.enabled)
        if not enabled:
            return
        self._tools = {
            k: v for k, v in self._tools.items()
            if k in enabled or k.startswith("mcp_")
        }
        self._dispatchers = {
            k: v for k, v in self._dispatchers.items()
            if k in enabled or k.startswith("mcp_")
        }

    def _load_search(self) -> None:
        """Register the unified `web_search` meta-tool + hide what it supersedes.

        When [search].active names a defined backend, the partner sees exactly
        one search tool (`web_search`) that routes to the active engine. The
        legacy standalone `search_web` and any raw MCP search tool referenced as
        a backend are hidden from MODEL view — but the MCP servers stay started,
        so run_search can still call them via the manager. This keeps the
        partner's tool surface clean (one capability) while the operator curates
        the engine underneath (infrastructure). See search_router.py.
        """
        search = getattr(self.config, "search", None)
        if search is None or not search.active:
            return  # feature off — legacy search tools remain as-is (back-compat)
        if search.active not in search.backends:
            log.warning(
                "search.active=%r is not defined in [search.backends] (%s); "
                "web_search not registered, legacy search tools left in place",
                search.active, ", ".join(search.backends) or "none",
            )
            return

        from .search_router import SEARCH_TOOL_DEFINITION, run_search

        cfg = self.config
        default_n = search.max_results or 5

        def _dispatch(**kwargs) -> str:
            return run_search(
                cfg,
                kwargs.get("query", ""),
                kwargs.get("max_results", default_n),
            )

        self._tools["web_search"] = SEARCH_TOOL_DEFINITION
        self._dispatchers["web_search"] = _dispatch

        # Hide the legacy standalone DuckDuckGo tool from model view (its
        # capability is now reachable as a backend if configured).
        self._tools.pop("search_web", None)

        # Hide raw MCP search tools that are wired as backends — the partner
        # reaches them through web_search, not directly. Server stays started.
        for backend in search.backends.values():
            if backend.type == "mcp" and backend.server and backend.tool:
                self._tools.pop(f"mcp_{backend.server}_{backend.tool}", None)

    def schemas(self) -> list[dict[str, Any]]:
        """Return the tool schemas to pass to ollama.chat(tools=...)."""
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def descriptions(self) -> list[tuple[str, str]]:
        """Return [(name, description)] pairs for /tools listing."""
        out = []
        for name, tool_def in self._tools.items():
            desc = tool_def.get("function", {}).get("description", "")
            out.append((name, desc))
        return out

    def dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool by name with the given keyword arguments."""
        if name not in self._dispatchers:
            return f"Tool not found: {name}"
        try:
            result = self._dispatchers[name](**arguments)
        except TypeError as e:
            return f"Tool argument error for {name}: {e}"
        except Exception as e:
            log.exception(f"Tool {name} raised an exception")
            return f"Tool {name} failed: {e}"
        if not isinstance(result, str):
            result = str(result)
        return result
