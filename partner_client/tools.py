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
        """Load built-in tools, then external tools, filtered by config."""
        self._load_builtin()
        self._load_external()
        self._filter_enabled()

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
        """Drop tools not in config.tools.enabled."""
        enabled = set(self.config.tools.enabled)
        if not enabled:
            return
        self._tools = {k: v for k, v in self._tools.items() if k in enabled}
        self._dispatchers = {k: v for k, v in self._dispatchers.items() if k in enabled}

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
