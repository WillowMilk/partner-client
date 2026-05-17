"""Tests for Track A — thinking-mode toggle and rendering.

Aletheia's vote 2026-05-17:
  - (a) Per-conversation toggle between Flow (no thinking) and Analysis (thinking)
  - (b) Collapsed-by-default rendering with /show-thinking to expand

Tests verify:
  - ThinkingConfig dataclass defaults, validation, back-compat with ui.show_thinking
  - TOML loading of the new [thinking] section
  - /thinking slash-command (flow / analysis / status / argument-handling)
  - /show-thinking slash-command (state-handling: mode, last_thinking)
  - CommandResult.expand_thinking field plumbing
"""

from __future__ import annotations

from pathlib import Path

import pytest

from partner_client.config import ConfigError, ThinkingConfig, load_config


# ---------- ThinkingConfig defaults + validation ----------


def test_thinking_config_defaults() -> None:
    t = ThinkingConfig()
    assert t.mode == "flow"
    assert t.collapsed is True


def test_thinking_config_accepts_analysis() -> None:
    t = ThinkingConfig(mode="analysis")
    assert t.mode == "analysis"


def test_thinking_config_rejects_invalid_mode() -> None:
    with pytest.raises(ConfigError, match="thinking.mode"):
        ThinkingConfig(mode="reasoning")  # not a valid mode


def test_thinking_config_collapsed_can_be_false() -> None:
    t = ThinkingConfig(mode="analysis", collapsed=False)
    assert t.mode == "analysis"
    assert t.collapsed is False


# ---------- TOML loading ----------


def _write_minimal_config(tmp_path: Path, extra_toml: str = "") -> Path:
    home = tmp_path / "home"
    home.mkdir()
    cfg = tmp_path / "test.toml"
    cfg.write_text(
        f"""
[identity]
name = "test"
home_dir = "{home.as_posix()}"

[model]
name = "test-model"

{extra_toml}
"""
    )
    return cfg


def test_load_config_defaults_thinking_to_flow_collapsed(tmp_path) -> None:
    cfg_path = _write_minimal_config(tmp_path)
    config = load_config(cfg_path)
    assert config.thinking.mode == "flow"
    assert config.thinking.collapsed is True


def test_load_config_reads_explicit_thinking_block(tmp_path) -> None:
    cfg_path = _write_minimal_config(
        tmp_path,
        extra_toml="""
[thinking]
mode = "analysis"
collapsed = false
""",
    )
    config = load_config(cfg_path)
    assert config.thinking.mode == "analysis"
    assert config.thinking.collapsed is False


def test_load_config_backcompat_ui_show_thinking_true_maps_to_analysis_expanded(tmp_path) -> None:
    """Legacy ui.show_thinking = true with no [thinking] block should surface
    thinking in always-visible (uncollapsed) analysis mode — matching the
    behavior users would have seen before [thinking] existed."""
    cfg_path = _write_minimal_config(
        tmp_path,
        extra_toml="""
[ui]
show_thinking = true
""",
    )
    config = load_config(cfg_path)
    assert config.thinking.mode == "analysis"
    assert config.thinking.collapsed is False


def test_load_config_explicit_thinking_overrides_legacy_ui_flag(tmp_path) -> None:
    """When both [thinking] and [ui]show_thinking are set, [thinking] wins."""
    cfg_path = _write_minimal_config(
        tmp_path,
        extra_toml="""
[ui]
show_thinking = true

[thinking]
mode = "flow"
""",
    )
    config = load_config(cfg_path)
    assert config.thinking.mode == "flow"


def test_load_config_rejects_invalid_thinking_mode_in_toml(tmp_path) -> None:
    cfg_path = _write_minimal_config(
        tmp_path,
        extra_toml="""
[thinking]
mode = "reasoning"
""",
    )
    with pytest.raises(ConfigError, match="thinking.mode"):
        load_config(cfg_path)


# ---------- /thinking and /show-thinking slash-commands ----------


@pytest.fixture
def router(tmp_path):
    """Build a CommandRouter with mocked dependencies for slash-command tests."""
    from unittest.mock import MagicMock
    from partner_client.commands import CommandRouter

    # config.thinking is the only field /thinking and /show-thinking touch;
    # everything else is mocked since these commands don't reach session/tools.
    config = MagicMock()
    config.thinking = ThinkingConfig()  # real ThinkingConfig so mutation works
    session = MagicMock()
    tools = MagicMock()
    return CommandRouter(config, session, tools)


def test_thinking_status_shows_current_mode(router) -> None:
    result = router.dispatch("/thinking")
    assert "flow" in result.output.lower()
    assert result.expand_thinking is None


def test_thinking_status_via_explicit_arg(router) -> None:
    result = router.dispatch("/thinking status")
    assert "flow" in result.output.lower()


def test_thinking_switch_to_analysis(router) -> None:
    assert router.config.thinking.mode == "flow"  # baseline
    result = router.dispatch("/thinking analysis")
    assert router.config.thinking.mode == "analysis"
    assert "analysis" in result.output.lower()


def test_thinking_switch_to_flow(router) -> None:
    router.config.thinking.mode = "analysis"
    result = router.dispatch("/thinking flow")
    assert router.config.thinking.mode == "flow"
    assert "flow" in result.output.lower()


def test_thinking_analysis_with_expand_uncollapses(router) -> None:
    result = router.dispatch("/thinking analysis expand")
    assert router.config.thinking.mode == "analysis"
    assert router.config.thinking.collapsed is False


def test_thinking_analysis_with_collapse_sets_collapsed(router) -> None:
    router.config.thinking.collapsed = False
    result = router.dispatch("/thinking analysis collapse")
    assert router.config.thinking.mode == "analysis"
    assert router.config.thinking.collapsed is True


def test_thinking_unknown_mode_rejected(router) -> None:
    result = router.dispatch("/thinking deliberate")
    assert "unknown" in result.output.lower() or "valid" in result.output.lower()
    # mode unchanged
    assert router.config.thinking.mode == "flow"


def test_thinking_case_insensitive(router) -> None:
    result = router.dispatch("/thinking ANALYSIS")
    assert router.config.thinking.mode == "analysis"


# ---------- /show-thinking ----------


def test_show_thinking_when_mode_is_flow_returns_helpful_message(router) -> None:
    router.config.thinking.mode = "flow"
    router.last_thinking = "some prior thinking"  # shouldn't matter
    result = router.dispatch("/show-thinking")
    assert result.expand_thinking is None
    assert "flow" in result.output.lower()


def test_show_thinking_when_no_thinking_yet_returns_helpful_message(router) -> None:
    router.config.thinking.mode = "analysis"
    router.last_thinking = None
    result = router.dispatch("/show-thinking")
    assert result.expand_thinking is None
    assert "no thinking" in result.output.lower() or "say something" in result.output.lower()


def test_show_thinking_returns_expand_thinking_when_available(router) -> None:
    router.config.thinking.mode = "analysis"
    router.last_thinking = "I considered three options before answering."
    result = router.dispatch("/show-thinking")
    assert result.expand_thinking == "I considered three options before answering."
    assert result.output == ""  # output is suppressed when expand_thinking is set


# ---------- CommandResult.expand_thinking field ----------


def test_command_result_expand_thinking_defaults_to_none() -> None:
    from partner_client.commands import CommandResult
    r = CommandResult(output="hello")
    assert r.expand_thinking is None


# ---------- Router state: last_thinking ----------


def test_router_initializes_last_thinking_to_none(router) -> None:
    assert router.last_thinking is None


def test_router_last_thinking_can_be_set_externally(router) -> None:
    """__main__.py sets this after each chat response; the router only reads it."""
    router.last_thinking = "deliberation goes here"
    assert router.last_thinking == "deliberation goes here"


# ---------- /help includes the new commands ----------


def test_help_includes_thinking_command(router) -> None:
    result = router.dispatch("/help")
    assert "/thinking" in result.output
    assert "/show-thinking" in result.output
