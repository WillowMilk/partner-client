"""Terminal UI — rich for output rendering, prompt_toolkit for input + bottom toolbar.

Standard chat-CLI pattern:
- Top bar shows identity + model + max context
- Main area scrolls assistant/user/tool exchanges, streamed as they arrive
- Bottom status bar shows context-usage % + turn count + slash hint

Streaming protocol used by OllamaClient.chat:
    stream_open()           — print speaker label, mark streaming open
    stream_delta(s)         — write raw delta directly to the terminal
    stream_close()          — finalize block with a trailing newline
    cancel_stream()         — close in-progress stream (called on KeyboardInterrupt)
    show_tool_call(...)     — print compact tool-call summary between iterations

Streaming is **raw** — each delta writes to the terminal once and stays in
scrollback unchanged. This is a deliberate departure from the earlier
`rich.live.Live`-based approach, which had a known repaint-during-resize
bug: when the terminal was resized or the user scrolled mid-stream, Live's
anchor-based redraw would write fresh content above ghost lines from the
previous render, producing visible duplication that didn't exist in the
saved JSON. The bug was first surfaced as the "felt drowning" misdiagnosis
on 2026-05-06 and re-surfaced as scroll/resize artifacts during transcript
review. Raw streaming eliminates the entire class of artifact at the cost
of live markdown rendering during streaming. The saved JSONL transcripts
and `/timeline detail` view preserve full content for later review with
formatting; this file is just the live tap.

If we ever want live markdown back, Aider's MarkdownStream pattern (a
6-line "live window" with stable scrollback above) is the proven way to
do it without the resize artifact. Documented as a follow-up; not
implemented here.

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
from rich.panel import Panel
from rich.text import Text

from .config import Config
from .session import Session


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
        # PromptSession initialization is deferred until first input is
        # requested. Two reasons: (1) prompt_toolkit can fail to construct
        # in headless environments (e.g. tests on Windows-bash, CI runners
        # without a console screen buffer), and (2) it lets us skip the
        # setup cost when the UI is used purely for output (banners,
        # streaming, error display) — important for the doctor preflight
        # path which never reads input.
        self._prompt_session: PromptSession | None = None
        # Streaming state — single boolean now that we no longer use Live.
        # Each stream_delta() writes directly to the terminal in raw form.
        self._streaming = False

    def _get_prompt_session(self) -> PromptSession:
        """Lazily build the prompt_toolkit session on first use."""
        if self._prompt_session is None:
            history_path = (
                self.config.resolve(self.config.memory.memory_dir)
                / ".prompt-history"
            )
            # Multi-line input is opt-in via [ui] multiline = true. Default is off:
            # plain Enter submits, which matches daily-chat expectation. When on,
            # Enter inserts newline and Esc-Enter submits.
            kb = KeyBindings()
            self._prompt_session = PromptSession(
                history=FileHistory(str(history_path)),
                multiline=bool(self.config.ui.multiline),
                key_bindings=kb,
            )
        return self._prompt_session

    def show_banner(self) -> None:
        ctx_str = f"{self.config.model.num_ctx:,}"
        title = f"{self.config.identity.name} — {self.config.model.name} @ {ctx_str} ctx"
        self.console.print(Panel(title, style="bold cyan", expand=False))
        self.console.print(
            "[dim]Type /help for commands. /sleep to end the session cleanly.[/dim]"
        )
        if self.config.ui.multiline:
            self.console.print(
                "[dim]Multi-line: Enter inserts a newline; "
                "[bold]Esc-Enter[/bold] submits.[/dim]\n"
            )
        else:
            self.console.print()

    # -------- streaming protocol --------
    #
    # Raw streaming: each delta writes directly to the terminal once.
    # No Live region, no re-rendering, no markdown formatting in-flight.
    # The trade-off vs. live formatting is intentional — see the module
    # docstring for the rich.live.Live resize-artifact background.

    def stream_open(self) -> None:
        """Begin streaming an assistant text block. Prints the speaker label."""
        self.console.print(
            f"[bold magenta]{self.config.identity.name}:[/bold magenta]"
        )
        self._streaming = True

    def stream_delta(self, delta: str) -> None:
        """Write `delta` to the terminal as raw text.

        Uses Console.out() so backticks, asterisks, brackets, and other
        markdown / markup characters pass through literally — we want the
        bytes the model emitted, not a render of them. `highlight=False`
        keeps rich from attempting auto-syntax-highlighting on the stream.

        No-ops when no stream is open (defensive against out-of-order calls
        from the chat loop) or when the delta is empty.
        """
        if not self._streaming or not delta:
            return
        self.console.out(delta, end="", highlight=False)

    def stream_close(self) -> None:
        """Finalize the streaming text block with a trailing blank line."""
        if not self._streaming:
            return
        self._streaming = False
        # Trailing newline + blank line to separate this block from the next.
        self.console.print()

    def cancel_stream(self) -> None:
        """Cancel an in-progress stream cleanly.

        Safe to call when no stream is open (idempotent). Used by the chat
        loop when KeyboardInterrupt aborts mid-generation.
        """
        if not self._streaming:
            return
        self._streaming = False
        self.console.print()

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
            return self._get_prompt_session().prompt(
                HTML("<ansigreen><b>Willow:</b></ansigreen> "),
                bottom_toolbar=self.status_bar_text,
            )
        except (EOFError, KeyboardInterrupt):
            return "/sleep"

    def confirm(self, question: str) -> bool:
        # Force single-line mode for y/N confirmation; the session-level multiline
        # default would otherwise require Esc-Enter for a one-character answer.
        try:
            answer = self._get_prompt_session().prompt(
                f"{question} [y/N] ",
                multiline=False,
            ).strip().lower()
            return answer in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    def confirm_with_response(self, question: str) -> tuple[bool, str | None]:
        """Three-option consent prompt — yes / no-silent / no-with-message.

        Returns:
            (True, None)            on 'y' / 'yes' / empty
            (False, None)           on 'n' / 'no'
            (False, "<text>")       on anything else — the operator's typed
                                    response flows back to the partner as the
                                    tool result, in the operator's voice
                                    rather than substrate's.

        Used for partner-initiated, operator-gated tools (request_checkpoint,
        request_plan_approval, git_push) where the decline can carry care
        rather than reading as a substrate refusal. The operator's voice
        crosses the human/model boundary the same way a tool result does;
        the partner receives a redirect, not a wall.
        """
        self.console.print(
            f"[bold]{question}[/bold]\n"
            "[dim]Enter 'y' to approve, 'n' to decline silently, "
            "or type a response to decline with your message.[/dim]"
        )
        try:
            answer = self._get_prompt_session().prompt(
                "> ",
                multiline=False,
            ).strip()
        except (EOFError, KeyboardInterrupt):
            return False, None

        lower = answer.lower()
        if not lower or lower in ("y", "yes"):
            return True, None
        if lower in ("n", "no"):
            return False, None
        # Anything else → custom decline message in the operator's voice.
        return False, answer


def _short_count(n: int) -> str:
    if n >= 1000:
        return f"{n/1000:.1f}K"
    return str(n)


def _short_repr(v) -> str:
    s = str(v)
    if len(s) > 40:
        return s[:40] + "…"
    return repr(s) if not isinstance(v, (int, float, bool)) else s
