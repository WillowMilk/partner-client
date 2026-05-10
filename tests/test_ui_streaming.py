"""Tests for the raw-streaming UI protocol.

The streaming protocol replaces a previous rich.live.Live-based render
that produced visible duplication when the terminal was scrolled or
resized mid-stream. These tests pin the raw-streaming behavior so we
don't accidentally regress to a Live-based path:

- Each delta writes once and stays in scrollback
- Markup-significant characters (backticks, asterisks, brackets) pass
  through literally rather than being rendered
- Defensive no-ops when called out of order
- cancel_stream / repeated stream_close are idempotent
"""

from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from partner_client.config import (
    Config,
    IdentityConfig,
    LoggingConfig,
    MemoryConfig,
    ModelConfig,
    ToolsConfig,
    UIConfig,
    WakeBundleConfig,
)
from partner_client.memory import Memory
from partner_client.session import Session
from partner_client.ui import UI


def make_ui(tmp_path: Path) -> tuple[UI, io.StringIO]:
    """Construct a UI backed by a recording Console for output capture."""
    (tmp_path / "Memory").mkdir(parents=True, exist_ok=True)
    config = Config(
        identity=IdentityConfig(name="Aletheia", home_dir=tmp_path),
        model=ModelConfig(name="gemma4:31b", num_ctx=8192),
        memory=MemoryConfig(),
        wake_bundle=WakeBundleConfig(),
        tools=ToolsConfig(),
        ui=UIConfig(),
        logging=LoggingConfig(),
        config_path=tmp_path / "aletheia.toml",
    )
    memory = Memory(config)
    session = Session(config=config, memory=memory, session_num=1)
    ui = UI(config, session)

    # Swap the console for a recording one so we can read what was written.
    captured = io.StringIO()
    ui.console = Console(file=captured, force_terminal=False, width=80)
    return ui, captured


def test_stream_writes_label_then_deltas_in_order(tmp_path: Path) -> None:
    ui, captured = make_ui(tmp_path)

    ui.stream_open()
    ui.stream_delta("Hello, ")
    ui.stream_delta("my ")
    ui.stream_delta("love.")
    ui.stream_close()

    output = captured.getvalue()
    assert "Aletheia:" in output
    assert "Hello, my love." in output
    # Label must come before content
    assert output.index("Aletheia:") < output.index("Hello,")


def test_stream_delta_passes_markdown_characters_through_literally(
    tmp_path: Path,
) -> None:
    """Backticks, asterisks, and other markup characters appear as raw
    bytes — we want exactly what the model emitted, not a render."""
    ui, captured = make_ui(tmp_path)

    ui.stream_open()
    ui.stream_delta("```python\n")
    ui.stream_delta("def foo(): pass\n")
    ui.stream_delta("```\n")
    ui.stream_delta("**bold** and *italic* and `code`")
    ui.stream_close()

    output = captured.getvalue()
    # Two opening/closing fences as written
    assert output.count("```") == 2
    assert "def foo(): pass" in output
    # Markdown markers stay raw, not rendered
    assert "**bold**" in output
    assert "*italic*" in output
    assert "`code`" in output


def test_each_delta_appears_exactly_once(tmp_path: Path) -> None:
    """Regression guard for the rich.live.Live duplication bug.

    Live re-rendering during scroll/resize produced visible duplicates of
    streamed content; raw streaming must write each delta to the terminal
    exactly once, so token text appears once per call.
    """
    ui, captured = make_ui(tmp_path)

    ui.stream_open()
    # A unique token we can count occurrences of
    ui.stream_delta("UNIQUEMARKER42")
    ui.stream_close()

    output = captured.getvalue()
    assert output.count("UNIQUEMARKER42") == 1


def test_stream_delta_no_op_when_no_stream_is_open(tmp_path: Path) -> None:
    ui, captured = make_ui(tmp_path)

    ui.stream_delta("orphaned")

    assert "orphaned" not in captured.getvalue()


def test_stream_delta_no_op_on_empty_delta(tmp_path: Path) -> None:
    ui, captured = make_ui(tmp_path)

    ui.stream_open()
    ui.stream_delta("")  # empty deltas should be silently skipped
    ui.stream_delta("real-content")
    ui.stream_close()

    assert "real-content" in captured.getvalue()


def test_cancel_stream_safe_when_no_stream_open(tmp_path: Path) -> None:
    ui, _ = make_ui(tmp_path)
    # Should not raise — calling cancel without an open stream is a no-op
    ui.cancel_stream()


def test_cancel_stream_blocks_subsequent_deltas(tmp_path: Path) -> None:
    """After cancel, further stream_delta calls must no-op until the next
    stream_open (defensive against out-of-order chat-loop calls)."""
    ui, captured = make_ui(tmp_path)

    ui.stream_open()
    ui.stream_delta("partial-content")
    ui.cancel_stream()
    ui.stream_delta("should-not-appear")

    output = captured.getvalue()
    assert "partial-content" in output
    assert "should-not-appear" not in output


def test_stream_close_is_idempotent(tmp_path: Path) -> None:
    ui, _ = make_ui(tmp_path)
    ui.stream_open()
    ui.stream_close()
    # Calling close twice in a row should not raise
    ui.stream_close()
