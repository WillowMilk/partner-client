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
            "/intentions": ("Surface pending items from your Intentions.md (prospective memory).", self._cmd_intentions),
            "/plans": ("List recent durable plans (or filter by status, or show one plan by id).", self._cmd_plans),
            "/timeline": ("Show recent run-timeline events. Filter by N, category, or detail <index>.", self._cmd_timeline),
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

    def _cmd_intentions(self, arg: str) -> CommandResult:
        """Surface pending items from <memory_dir>/Intentions.md (prospective memory)."""
        memory_dir = self.config.resolve(self.config.memory.memory_dir)
        intentions_path = memory_dir / "Intentions.md"
        if not intentions_path.is_file():
            return CommandResult(
                output=(
                    f"No intentions file found at {intentions_path}.\n\n"
                    "Prospective memory (Intentions.md) is optional. To start "
                    "using it, create the file with markdown checkboxes:\n"
                    "  - [ ] item to remember\n"
                    "  - [x] completed item\n\n"
                    "Then /intentions will surface what's pending."
                )
            )
        try:
            content = intentions_path.read_text(encoding="utf-8")
        except OSError as e:
            return CommandResult(output=f"Error reading {intentions_path}: {e}")
        return CommandResult(
            output=f"Intentions ({intentions_path}):\n\n{content}"
        )

    # Aliases for `/plans <status>` filtering. `open` is a friendlier alias
    # for the literal `proposed` status that PlanStore writes.
    _PLAN_STATUS_ALIASES: dict[str, str] = {
        "open": "proposed",
        "proposed": "proposed",
        "approved": "approved",
        "declined": "declined",
    }

    def _cmd_plans(self, arg: str) -> CommandResult:
        """Surface durable plan records from <memory_dir>/plans.

        Usage:
            /plans                  - list recent plans (any status)
            /plans <status>         - list plans matching status
                                      (open/proposed/approved/declined)
            /plans <plan-id>        - show full detail for one plan
        """
        from .plans import PlanStore
        store = PlanStore(self.config)
        arg = arg.strip()
        if not arg:
            return CommandResult(output=store.format_recent())

        status = self._PLAN_STATUS_ALIASES.get(arg.lower())
        if status is not None:
            return CommandResult(
                output=store.format_recent(status_filter=status)
            )

        # Otherwise treat as a plan id for detail view.
        return CommandResult(output=store.format_detail(arg))

    def _cmd_timeline(self, arg: str) -> CommandResult:
        """Surface recent timeline events from the run-timeline JSONL.

        Usage:
            /timeline                     - last 20 events, oldest visible first
            /timeline <N>                 - last N events
            /timeline <category>          - last 20 events of one category
                                            (tools, errors, approvals, model,
                                            user, session)
            /timeline detail <index>      - full fields for one event from
                                            the most recent listing
        """
        from .timeline import TIMELINE_CATEGORIES, TimelineReader

        reader = TimelineReader(self.config)
        parts = arg.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else ""
        rest = parts[1] if len(parts) > 1 else ""

        if sub == "detail":
            target = rest.strip()
            try:
                idx = int(target)
            except ValueError:
                return CommandResult(
                    output="Usage: /timeline detail <index> (1-based, from /timeline listing)"
                )
            return CommandResult(output=reader.format_detail(idx))

        if sub in TIMELINE_CATEGORIES:
            return CommandResult(
                output=reader.format_recent(
                    limit=20,
                    event_types=TIMELINE_CATEGORIES[sub],
                    category_label=sub,
                )
            )

        if sub:
            try:
                n = int(sub)
                if n <= 0:
                    raise ValueError
            except ValueError:
                categories = ", ".join(sorted(TIMELINE_CATEGORIES.keys()))
                return CommandResult(
                    output=(
                        "Usage: /timeline [N | <category> | detail <index>]\n"
                        f"Categories: {categories}"
                    )
                )
            return CommandResult(output=reader.format_recent(limit=n))

        return CommandResult(output=reader.format_recent(limit=20))

    def _cmd_reload_config(self, arg: str) -> CommandResult:
        return CommandResult(
            output="Reload requested. Re-read your config file at next prompt.",
            should_reload=True,
        )
