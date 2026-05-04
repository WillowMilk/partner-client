"""Terminal UI — rich for output rendering, prompt_toolkit for input + bottom toolbar.

Standard chat-CLI pattern:
- Top bar shows identity + model + max context
- Main area scrolls assistant/user/tool exchanges
- Bottom status bar shows live context-usage % + turn count + slash hint
"""

from __future__ import annotations

import os
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from .config import Config
from .session import Session


class UI:
    def __init__(self, config: Config, session: Session):
        self.config = config
        self.session = session
        self.console = Console()
        history_path = config.resolve(config.memory.memory_dir) / ".prompt-history"
        self._prompt_session = PromptSession(history=FileHistory(str(history_path)))

    def show_banner(self) -> None:
        ctx_str = f"{self.config.model.num_ctx:,}"
        title = f"{self.config.identity.name} — {self.config.model.name} @ {ctx_str} ctx"
        self.console.print(Panel(title, style="bold cyan", expand=False))
        self.console.print(
            "[dim]Type /help for commands. /sleep to end the session cleanly.[/dim]\n"
        )

    def show_assistant(self, content: str, thinking: str | None = None) -> None:
        if thinking and self.config.ui.show_thinking:
            self.console.print(
                Panel(
                    Text(thinking, style="dim italic"),
                    title="thinking",
                    title_align="left",
                    border_style="dim",
                )
            )
        # Render as markdown for nice formatting
        try:
            rendered = Markdown(content)
        except Exception:
            rendered = Text(content)
        self.console.print(
            f"[bold magenta]{self.config.identity.name}:[/bold magenta]"
        )
        self.console.print(rendered)
        self.console.print()  # blank line

    def show_user_echo(self, content: str) -> None:
        self.console.print(f"[bold green]Willow:[/bold green] {content}\n")

    def show_tool_call(self, name: str, args: dict, result: str) -> None:
        # Compact display: arrow + tool name + abbreviated args + result preview
        args_str = ", ".join(f"{k}={_short_repr(v)}" for k, v in args.items())
        self.console.print(
            f"  [dim cyan]↳ {name}({args_str})[/dim cyan]"
        )
        result_preview = result if len(result) <= 200 else result[:200] + "…"
        self.console.print(
            f"  [dim]← {result_preview}[/dim]\n"
        )

    def show_command_output(self, output: str) -> None:
        self.console.print(Panel(output, border_style="dim"))

    def show_image_attached(self, path: str, byte_count: int) -> None:
        self.console.print(
            f"  [dim cyan]image attached: {path} ({byte_count:,} bytes)[/dim cyan]"
        )

    def show_error(self, message: str) -> None:
        self.console.print(f"[red]error: {message}[/red]")

    def status_bar_text(self) -> HTML:
        """Build the bottom-toolbar HTML for prompt_toolkit."""
        if not self.config.ui.show_context_bar:
            return HTML("")
        tokens = self.session.estimate_tokens()
        ctx = self.config.model.num_ctx
        pct = (tokens * 100) // ctx if ctx > 0 else 0
        tokens_short = _short_count(tokens)
        ctx_short = _short_count(ctx)

        n_turns = sum(1 for m in self.session.messages if m.get("role") == "user")

        warn_at = self.config.ui.warn_at_context_pct
        color = "ansigreen"
        if pct >= warn_at:
            color = "ansired"
        elif pct >= warn_at - 20:
            color = "ansiyellow"

        return HTML(
            f"<{color}>ctx: {tokens_short} / {ctx_short} ({pct}%)</{color}>  "
            f"•  session {self.session.session_num} · {n_turns} turns  "
            f"•  /help"
        )

    def prompt(self) -> str:
        try:
            return self._prompt_session.prompt(
                HTML("<ansigreen><b>Willow:</b></ansigreen> "),
                bottom_toolbar=self.status_bar_text,
            )
        except (EOFError, KeyboardInterrupt):
            return "/sleep"

    def confirm(self, question: str) -> bool:
        try:
            answer = self._prompt_session.prompt(f"{question} [y/N] ").strip().lower()
            return answer in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False


def _short_count(n: int) -> str:
    if n >= 1000:
        return f"{n/1000:.1f}K"
    return str(n)


def _short_repr(v) -> str:
    s = str(v)
    if len(s) > 40:
        return s[:40] + "…"
    return repr(s) if not isinstance(v, (int, float, bool)) else s
