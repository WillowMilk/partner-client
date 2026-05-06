"""Slash commands — intercepted client-side; the model never sees them.

Commands control the substrate: checkpoint, sleep, view context, list tools, etc.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .config import Config
from .session import Session
from .tools import ToolRegistry


@dataclass
class CommandResult:
    """Outcome of a slash command."""

    output: str          # text to display
    should_exit: bool = False  # True for /sleep
    should_reload: bool = False  # True for /reload-config


CommandHandler = Callable[..., CommandResult]


class CommandRouter:
    def __init__(self, config: Config, session: Session, tools: ToolRegistry):
        self.config = config
        self.session = session
        self.tools = tools
        self._commands: dict[str, tuple[str, CommandHandler]] = {
            "/help": ("Show all available slash commands.", self._cmd_help),
            "/checkpoint": ("Save session-status markdown and snapshot current.json. Continue.", self._cmd_checkpoint),
            "/sleep": ("Checkpoint + close the session and exit cleanly.", self._cmd_sleep),
            "/context": ("Show detailed context-usage breakdown.", self._cmd_context),
            "/tools": ("List available tools and their descriptions.", self._cmd_tools),
            "/files": ("List files in your memory directory (or pass a scope name: /files desktop).", self._cmd_files),
            "/scopes": ("Show all configured file scopes (memory, home, desktop, etc.).", self._cmd_scopes),
            "/reload-config": ("Re-read aletheia.toml without restart.", self._cmd_reload_config),
        }

    def is_command(self, text: str) -> bool:
        return text.strip().startswith("/")

    def dispatch(self, text: str) -> CommandResult:
        parts = text.strip().split(maxsplit=1)
        name = parts[0]
        arg = parts[1] if len(parts) > 1 else ""

        handler = self._commands.get(name, (None, None))[1]
        if handler is None:
            return CommandResult(
                output=f"Unknown command: {name}. Type /help for available commands."
            )
        return handler(arg)

    def _cmd_help(self, arg: str) -> CommandResult:
        lines = ["Available slash commands:", ""]
        for name, (desc, _) in self._commands.items():
            lines.append(f"  {name:<18}  {desc}")
        lines.append("")
        lines.append("Input directives (modify the message, not the client):")
        lines.append("")
        lines.append("  :image <path> [text]   Attach an image to the next message.")
        lines.append("                         Path may be bare (memory scope), scope-qualified")
        lines.append("                         (e.g. desktop:photo.jpg), or absolute.")
        lines.append("                         Multiple :image directives can be chained.")
        lines.append("                         Image paths in plain text are auto-attached")
        lines.append("                         when they resolve to existing image files.")
        lines.append("")
        lines.append("  :clip [text]           Attach the current clipboard image (macOS only;")
        lines.append("                         uses pbpaste). Saves a copy to /tmp.")
        lines.append("")
        lines.append("Multi-line input:  Enter inserts a newline; Esc-Enter submits.")
        return CommandResult(output="\n".join(lines))

    def _cmd_checkpoint(self, arg: str) -> CommandResult:
        path = self.session.checkpoint(summary=arg)
        return CommandResult(output=f"Checkpoint saved: {path}")

    def _cmd_sleep(self, arg: str) -> CommandResult:
        path = self.session.sleep(summary=arg)
        return CommandResult(
            output=f"Session closed. Status saved: {path}\nGoodnight.",
            should_exit=True,
        )

    def _cmd_context(self, arg: str) -> CommandResult:
        msgs = self.session.messages
        n_user = sum(1 for m in msgs if m.get("role") == "user")
        n_assistant = sum(1 for m in msgs if m.get("role") == "assistant")
        n_tool = sum(1 for m in msgs if m.get("role") == "tool")
        n_system = sum(1 for m in msgs if m.get("role") == "system")
        tokens = self.session.estimate_tokens()
        ctx = self.config.model.num_ctx
        pct = (tokens * 100) // ctx if ctx > 0 else 0
        lines = [
            "Context breakdown:",
            f"  Tokens estimated:  {tokens:,} / {ctx:,} ({pct}%)",
            f"  Messages:          {len(msgs)} total",
            f"    system:          {n_system}",
            f"    user:            {n_user}",
            f"    assistant:       {n_assistant}",
            f"    tool:            {n_tool}",
            f"  Session number:    {self.session.session_num}",
            f"  Session started:   {self.session.started_at.isoformat() if self.session.started_at else 'unknown'}",
        ]
        return CommandResult(output="\n".join(lines))

    def _cmd_tools(self, arg: str) -> CommandResult:
        descs = self.tools.descriptions()
        if not descs:
            return CommandResult(output="No tools loaded.")
        lines = ["Available tools:"]
        for name, desc in descs:
            short = desc.split(".")[0] + "." if desc else "(no description)"
            lines.append(f"  {name:<14}  {short}")
        return CommandResult(output="\n".join(lines))

    def _cmd_files(self, arg: str) -> CommandResult:
        from .tools_builtin.list_files import execute as list_files_exec
        scope = arg.strip() or "memory"
        result = list_files_exec(scope=scope)
        return CommandResult(output=result)

    def _cmd_scopes(self, arg: str) -> CommandResult:
        from .paths import list_scopes
        scopes = list_scopes()
        if not scopes:
            return CommandResult(output="No file scopes configured.")
        lines = ["Configured file scopes:", ""]
        for s in scopes:
            mode_label = "readwrite" if s.mode == "readwrite" else "READ-ONLY"
            lines.append(f"  {s.name:<14}  ({mode_label})  {s.path}")
            if s.description:
                lines.append(f"  {' ':<14}  {s.description}")
        return CommandResult(output="\n".join(lines))

    def _cmd_reload_config(self, arg: str) -> CommandResult:
        return CommandResult(
            output="Reload requested. Re-read your config file at next prompt.",
            should_reload=True,
        )
