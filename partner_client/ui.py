"""Terminal UI — rich for output rendering, prompt_toolkit for input + bottom toolbar.

Standard chat-CLI pattern:
- Top bar shows identity + model + max context
- Main area scrolls assistant/user/tool exchanges, streamed live as they arrive
- Bottom status bar shows context-usage % + turn count + slash hint

Streaming protocol used by OllamaClient.chat:
    stream_open()           — print speaker label, begin a Live region
    stream_delta(s)         — append delta, refresh Live render
    stream_close()          — finalize Live, blank line
    cancel_stream()         — close any open Live region (called on KeyboardInterrupt)
    show_tool_call(...)     — print compact tool-call summary between iterations

Multi-line input: prompt_toolkit's `multiline=True` mode is enabled. Plain
Enter inserts a newline; **Esc-Enter** (Option-Enter on Mac iTerm/Ghostty
with "use Option as Esc+" enabled) submits. The banner reminds the user.
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from .config import Config
from .session import Session


# Markdown code-block theme — applied to streamed content for syntax-highlighted code.
# Pygments themes available: "monokai", "dracula", "one-dark", "github-dark", etc.
_CODE_THEME = "monokai"

# Image preview cap: don't blast huge images into the terminal escape stream.
_INLINE_PREVIEW_MAX_BYTES = 2_000_000  # 2MB


def _terminal_supports_iterm2_images() -> bool:
    """True if the current terminal supports the iTerm2 inline-image protocol.

    Covers iTerm2, Ghostty, WezTerm, and anything else that sets the standard
    iTerm2 sentinels. Returns False for plain Terminal.app, which silently
    drops the OSC 1337 sequence.
    """
    term_program = os.environ.get("TERM_PROGRAM", "").lower()
    for marker in ("iterm", "ghostty", "wezterm"):
        if marker in term_program:
            return True
    if os.environ.get("LC_TERMINAL", "").lower() == "iterm2":
        return True
    return False


class UI:
    def __init__(self, config: Config, session: Session):
        self.config = config
        self.session = session
        self.console = Console()
        history_path = config.resolve(config.memory.memory_dir) / ".prompt-history"
        # Multi-line input: Enter inserts newline, Esc-Enter submits.
        # See banner for the user-facing reminder.
        kb = KeyBindings()
        self._prompt_session = PromptSession(
            history=FileHistory(str(history_path)),
            multiline=True,
            key_bindings=kb,
        )
        # Streaming state
        self._stream_live: Live | None = None
        self._stream_buffer: list[str] = []

    def show_banner(self) -> None:
        ctx_str = f"{self.config.model.num_ctx:,}"
        title = f"{self.config.identity.name} — {self.config.model.name} @ {ctx_str} ctx"
        self.console.print(Panel(title, style="bold cyan", expand=False))
        self.console.print(
            "[dim]Type /help for commands. /sleep to end the session cleanly.[/dim]"
        )
        self.console.print(
            "[dim]Multi-line: Enter inserts a newline; "
            "[bold]Esc-Enter[/bold] submits.[/dim]\n"
        )

    # -------- streaming protocol --------

    def stream_open(self) -> None:
        """Begin streaming an assistant text block. Prints the speaker label."""
        # Print the label outside the Live region so it stays in scrollback.
        self.console.print(
            f"[bold magenta]{self.config.identity.name}:[/bold magenta]"
        )
        self._stream_buffer = []
        self._stream_live = Live(
            "",
            console=self.console,
            refresh_per_second=12,
            vertical_overflow="visible",
            transient=False,
        )
        self._stream_live.start()

    def stream_delta(self, delta: str) -> None:
        """Append `delta` to the running stream and refresh the rendered view."""
        if self._stream_live is None:
            return
        self._stream_buffer.append(delta)
        rendered = self._render_stream_buffer()
        try:
            self._stream_live.update(rendered)
        except Exception:
            # Render failed (rare); fall back to plain text
            try:
                self._stream_live.update(Text("".join(self._stream_buffer)))
            except Exception:
                pass

    def stream_close(self) -> None:
        """Finalize the streaming text block."""
        if self._stream_live is None:
            return
        try:
            self._stream_live.stop()
        except Exception:
            pass
        self._stream_live = None
        self._stream_buffer = []
        self.console.print()  # blank line after each assistant block

    def cancel_stream(self) -> None:
        """Close any open Live region without finalizing. Safe to call when no stream is open."""
        if self._stream_live is None:
            return
        try:
            self._stream_live.stop()
        except Exception:
            pass
        self._stream_live = None
        self._stream_buffer = []

    def _render_stream_buffer(self):
        """Build the rich renderable for the current stream buffer.

        Auto-closes a trailing unclosed code fence so partial markdown renders
        without breaking downstream styling.
        """
        text = "".join(self._stream_buffer)
        # Count unfenced ``` occurrences; if odd, append a closing fence to render
        # the in-progress block as a code block rather than as truncated markup.
        if text.count("```") % 2 == 1:
            text = text + "\n```"
        try:
            return Markdown(text, code_theme=_CODE_THEME)
        except Exception:
            return Text(text)

    # -------- non-streaming display helpers --------

    def show_thinking(self, thinking: str) -> None:
        """Render a thinking block (used after streaming when show_thinking=True)."""
        if not thinking or not self.config.ui.show_thinking:
            return
        self.console.print(
            Panel(
                Text(thinking, style="dim italic"),
                title="thinking",
                title_align="left",
                border_style="dim",
            )
        )

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

    def show_image_attached(
        self,
        path: str,
        byte_count: int,
        image_bytes: bytes | None = None,
    ) -> None:
        """Announce an image attachment. On supported terminals, render an inline preview.

        Pass `image_bytes` to enable the inline preview on iTerm2/Ghostty/WezTerm
        (uses the iTerm2 OSC 1337 protocol). Plain Terminal.app silently ignores
        the escape; we suppress the preview attempt there to keep scrollback clean.
        """
        self.console.print(
            f"  [dim cyan]image attached: {path} ({byte_count:,} bytes)[/dim cyan]"
        )
        if image_bytes is None:
            return
        if len(image_bytes) > _INLINE_PREVIEW_MAX_BYTES:
            return
        if not _terminal_supports_iterm2_images():
            return
        try:
            encoded = base64.b64encode(image_bytes).decode("ascii")
            name = Path(path).name or "image"
            seq = f"\x1b]1337;File=name={name};inline=1;height=12:{encoded}\a"
            sys.stdout.write(seq)
            sys.stdout.write("\n")
            sys.stdout.flush()
        except Exception:
            pass

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
        # Force single-line mode for y/N confirmation; the session-level multiline
        # default would otherwise require Esc-Enter for a one-character answer.
        try:
            answer = self._prompt_session.prompt(
                f"{question} [y/N] ",
                multiline=False,
            ).strip().lower()
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
