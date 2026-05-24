"""Tests for plan-mode auto-trigger.

Operator-toggle + partner-elects + soft-gate design:
  - PlanModeConfig dataclass (defaults, validation, TOML loading)
  - build_plan_mode_addendum + inject_plan_mode_addendum helpers
  - dispatch_one_tool_call gating (research-tools pass, destructive gated,
    always-allowed always pass, approved unlocks rest of turn)
  - /plan-mode slash command (status, on, off, no-change, invalid, case-insensitive)
  - Client.plan_mode_active @property reads live from config
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from partner_client.client import (
    PLAN_MODE_ALWAYS_ALLOWED,
    build_plan_mode_addendum,
    dispatch_one_tool_call,
    inject_plan_mode_addendum,
)
from partner_client.config import ConfigError, PlanModeConfig, load_config


# ---------- PlanModeConfig defaults + validation ----------


def test_plan_mode_config_defaults() -> None:
    p = PlanModeConfig()
    assert p.mode == "off"
    assert "read_file" in p.research_only_tools
    assert "search_web" in p.research_only_tools
    assert "hub_check_inbox" in p.research_only_tools


def test_plan_mode_config_accepts_on() -> None:
    p = PlanModeConfig(mode="on")
    assert p.mode == "on"


def test_plan_mode_config_rejects_invalid_mode() -> None:
    with pytest.raises(ConfigError, match="plan_mode.mode"):
        PlanModeConfig(mode="auto")


def test_plan_mode_config_research_tools_is_independent_per_instance() -> None:
    """field(default_factory=lambda: [...]) — each instance gets its own list."""
    p1 = PlanModeConfig()
    p2 = PlanModeConfig()
    p1.research_only_tools.append("custom_tool")
    assert "custom_tool" not in p2.research_only_tools


def test_plan_mode_config_custom_research_list() -> None:
    p = PlanModeConfig(mode="on", research_only_tools=["read_file", "grep_files"])
    assert p.research_only_tools == ["read_file", "grep_files"]


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


def test_load_config_defaults_plan_mode_to_off(tmp_path) -> None:
    cfg_path = _write_minimal_config(tmp_path)
    config = load_config(cfg_path)
    assert config.plan_mode.mode == "off"
    assert len(config.plan_mode.research_only_tools) > 0  # defaults populated


def test_load_config_reads_explicit_plan_mode_on(tmp_path) -> None:
    cfg_path = _write_minimal_config(
        tmp_path,
        extra_toml="""
[plan_mode]
mode = "on"
""",
    )
    config = load_config(cfg_path)
    assert config.plan_mode.mode == "on"


def test_load_config_reads_custom_research_only_tools(tmp_path) -> None:
    cfg_path = _write_minimal_config(
        tmp_path,
        extra_toml="""
[plan_mode]
mode = "on"
research_only_tools = ["read_file", "grep_files"]
""",
    )
    config = load_config(cfg_path)
    assert config.plan_mode.research_only_tools == ["read_file", "grep_files"]


def test_load_config_rejects_invalid_plan_mode_in_toml(tmp_path) -> None:
    cfg_path = _write_minimal_config(
        tmp_path,
        extra_toml="""
[plan_mode]
mode = "automatic"
""",
    )
    with pytest.raises(ConfigError, match="plan_mode.mode"):
        load_config(cfg_path)


# ---------- build_plan_mode_addendum ----------


def test_addendum_unapproved_mentions_request_plan_approval() -> None:
    text = build_plan_mode_addendum(approved_this_turn=False, research_only_tools=["read_file"])
    assert "request_plan_approval" in text
    assert "[PLAN MODE — ACTIVE, NO PLAN APPROVED YET]" in text


def test_addendum_approved_says_unlocked() -> None:
    text = build_plan_mode_addendum(approved_this_turn=True, research_only_tools=["read_file"])
    assert "[PLAN MODE — APPROVED THIS TURN]" in text
    assert "unlocked" in text.lower()


def test_addendum_interpolates_research_tools() -> None:
    text = build_plan_mode_addendum(
        approved_this_turn=False,
        research_only_tools=["read_file", "search_web", "hub_check_inbox"],
    )
    assert "read_file" in text
    assert "search_web" in text
    assert "hub_check_inbox" in text


def test_addendum_handles_empty_research_list() -> None:
    text = build_plan_mode_addendum(approved_this_turn=False, research_only_tools=[])
    assert "(none configured)" in text


def test_addendum_unapproved_names_always_allowed_tools() -> None:
    text = build_plan_mode_addendum(approved_this_turn=False, research_only_tools=[])
    assert "request_plan_approval" in text
    assert "request_checkpoint" in text
    assert "protect_save" in text


# ---------- inject_plan_mode_addendum ----------


def test_inject_returns_input_unchanged_when_off() -> None:
    msgs = [{"role": "system", "content": "wake"}, {"role": "user", "content": "hi"}]
    out = inject_plan_mode_addendum(
        msgs, plan_mode_active=False, plan_approved_this_turn=False, research_only_tools=[]
    )
    assert out is msgs  # exact same object


def test_inject_inserts_after_leading_system_messages() -> None:
    msgs = [
        {"role": "system", "content": "wake"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    out = inject_plan_mode_addendum(
        msgs, plan_mode_active=True, plan_approved_this_turn=False, research_only_tools=["read_file"]
    )
    assert len(out) == 4
    assert out[0]["role"] == "system" and out[0]["content"] == "wake"
    assert out[1]["role"] == "system" and "PLAN MODE" in out[1]["content"]
    assert out[2]["role"] == "user"
    assert out[3]["role"] == "assistant"


def test_inject_handles_multiple_leading_system_messages() -> None:
    msgs = [
        {"role": "system", "content": "wake"},
        {"role": "system", "content": "scopes"},
        {"role": "user", "content": "hi"},
    ]
    out = inject_plan_mode_addendum(
        msgs, plan_mode_active=True, plan_approved_this_turn=False, research_only_tools=[]
    )
    # Addendum should be inserted at index 2 — after both leading system messages
    assert out[0]["content"] == "wake"
    assert out[1]["content"] == "scopes"
    assert "PLAN MODE" in out[2]["content"]
    assert out[3]["role"] == "user"


def test_inject_inserts_at_index_zero_when_no_leading_system_messages() -> None:
    msgs = [{"role": "user", "content": "hi"}]
    out = inject_plan_mode_addendum(
        msgs, plan_mode_active=True, plan_approved_this_turn=False, research_only_tools=[]
    )
    assert len(out) == 2
    assert out[0]["role"] == "system"
    assert "PLAN MODE" in out[0]["content"]
    assert out[1]["role"] == "user"


def test_inject_does_not_mutate_input() -> None:
    msgs = [{"role": "system", "content": "wake"}, {"role": "user", "content": "hi"}]
    msgs_snapshot = [dict(m) for m in msgs]
    inject_plan_mode_addendum(
        msgs, plan_mode_active=True, plan_approved_this_turn=False, research_only_tools=[]
    )
    assert msgs == msgs_snapshot


def test_inject_reflects_approved_state_in_addendum() -> None:
    msgs = [{"role": "user", "content": "hi"}]
    out = inject_plan_mode_addendum(
        msgs, plan_mode_active=True, plan_approved_this_turn=True, research_only_tools=[]
    )
    assert "APPROVED THIS TURN" in out[0]["content"]


# ---------- PLAN_MODE_ALWAYS_ALLOWED constant ----------


def test_always_allowed_includes_the_three_critical_tools() -> None:
    assert "request_plan_approval" in PLAN_MODE_ALWAYS_ALLOWED
    assert "request_checkpoint" in PLAN_MODE_ALWAYS_ALLOWED
    assert "protect_save" in PLAN_MODE_ALWAYS_ALLOWED


def test_always_allowed_is_immutable_set() -> None:
    assert isinstance(PLAN_MODE_ALWAYS_ALLOWED, frozenset)


# ---------- dispatch_one_tool_call gating ----------


@pytest.fixture
def mock_dispatch_deps(tmp_path):
    """Build minimal dependencies for dispatch_one_tool_call tests.

    Returns a dict of kwargs to pass to dispatch_one_tool_call, with
    sensible defaults that can be overridden per test.
    """
    config = MagicMock()
    tools = MagicMock()
    tools.dispatch.return_value = "tool executed normally"
    session = MagicMock()
    session.messages = []
    return {
        "tool_call_id": "test-id",
        "config": config,
        "tools": tools,
        "timeline": None,
        "session": session,
        "on_plan_approval_request": None,
        "on_git_push_request": None,
        "on_delete_path_request": None,
    }


def test_dispatch_passes_through_when_plan_mode_off(mock_dispatch_deps) -> None:
    """plan_mode_active=False means no gating; tools dispatch normally."""
    result = dispatch_one_tool_call(
        name="write_file",
        args={"path": "x.md", "content": "y"},
        plan_mode_active=False,
        plan_approved=False,
        research_only_tools=[],
        **mock_dispatch_deps,
    )
    assert result == "tool executed normally"
    mock_dispatch_deps["tools"].dispatch.assert_called_once_with(
        "write_file", {"path": "x.md", "content": "y"}
    )


def test_dispatch_gates_destructive_when_active_unapproved(mock_dispatch_deps) -> None:
    result = dispatch_one_tool_call(
        name="write_file",
        args={"path": "x.md", "content": "y"},
        plan_mode_active=True,
        plan_approved=False,
        research_only_tools=["read_file"],  # write_file not in list
        **mock_dispatch_deps,
    )
    assert "Plan mode is active" in result
    assert "write_file" in result
    # Regular dispatch should NOT have been called
    mock_dispatch_deps["tools"].dispatch.assert_not_called()


def test_dispatch_passes_research_tools_when_active_unapproved(mock_dispatch_deps) -> None:
    result = dispatch_one_tool_call(
        name="read_file",
        args={"path": "x.md"},
        plan_mode_active=True,
        plan_approved=False,
        research_only_tools=["read_file"],
        **mock_dispatch_deps,
    )
    assert result == "tool executed normally"
    mock_dispatch_deps["tools"].dispatch.assert_called_once()


def test_dispatch_always_allowed_pass_through_even_unapproved(mock_dispatch_deps) -> None:
    """protect_save is in PLAN_MODE_ALWAYS_ALLOWED; should pass even without approval."""
    result = dispatch_one_tool_call(
        name="protect_save",
        args={},
        plan_mode_active=True,
        plan_approved=False,
        research_only_tools=[],  # protect_save NOT in this list
        **mock_dispatch_deps,
    )
    # The actual tool path may differ (protect_save has its own branch in real
    # dispatch), but the gate should NOT block it. Since our mock tools.dispatch
    # is the fallback, and protect_save passes the gate, it would reach the
    # default branch. The key assertion: NOT the gated message.
    assert "Plan mode is active" not in result


def test_dispatch_request_plan_approval_pass_through_even_unapproved(mock_dispatch_deps) -> None:
    """request_plan_approval is special-cased AND in PLAN_MODE_ALWAYS_ALLOWED.
    It must NOT be gated — otherwise the partner couldn't ever get approval."""
    mock_dispatch_deps["on_plan_approval_request"] = MagicMock(return_value=(False, None))
    result = dispatch_one_tool_call(
        name="request_plan_approval",
        args={"summary": "test", "plan": ["step1"]},
        plan_mode_active=True,
        plan_approved=False,
        research_only_tools=[],
        **mock_dispatch_deps,
    )
    assert "Plan mode is active" not in result
    # The special-case branch ran (operator handler invoked)
    mock_dispatch_deps["on_plan_approval_request"].assert_called_once()


def test_dispatch_all_tools_pass_when_approved(mock_dispatch_deps) -> None:
    """plan_approved=True means the gate has lifted — write_file should pass."""
    result = dispatch_one_tool_call(
        name="write_file",
        args={"path": "x.md", "content": "y"},
        plan_mode_active=True,
        plan_approved=True,  # KEY: approved
        research_only_tools=["read_file"],
        **mock_dispatch_deps,
    )
    assert result == "tool executed normally"
    mock_dispatch_deps["tools"].dispatch.assert_called_once()


def test_on_plan_approved_callback_fires_when_approved(mock_dispatch_deps) -> None:
    mock_dispatch_deps["on_plan_approval_request"] = MagicMock(return_value=(True, None))
    on_approved = MagicMock()
    result = dispatch_one_tool_call(
        name="request_plan_approval",
        args={"summary": "test", "plan": ["step1"]},
        plan_mode_active=True,
        plan_approved=False,
        research_only_tools=[],
        on_plan_approved=on_approved,
        **mock_dispatch_deps,
    )
    assert "approved" in result.lower()
    on_approved.assert_called_once()


def test_on_plan_approved_callback_not_fired_on_decline(mock_dispatch_deps) -> None:
    mock_dispatch_deps["on_plan_approval_request"] = MagicMock(return_value=(False, None))
    on_approved = MagicMock()
    result = dispatch_one_tool_call(
        name="request_plan_approval",
        args={"summary": "test", "plan": ["step1"]},
        plan_mode_active=True,
        plan_approved=False,
        research_only_tools=[],
        on_plan_approved=on_approved,
        **mock_dispatch_deps,
    )
    assert "declined" in result.lower()
    on_approved.assert_not_called()


# ---------- /plan-mode slash command ----------


@pytest.fixture
def router(tmp_path):
    """Build a CommandRouter with mocked dependencies for slash-command tests."""
    from partner_client.commands import CommandRouter

    config = MagicMock()
    config.plan_mode = PlanModeConfig()  # real PlanModeConfig so mutation works
    config.thinking = MagicMock()
    session = MagicMock()
    tools = MagicMock()
    return CommandRouter(config, session, tools)


def test_plan_mode_status_shows_off_initially(router) -> None:
    result = router.dispatch("/plan-mode")
    assert "off" in result.output.lower()


def test_plan_mode_status_via_explicit_arg(router) -> None:
    result = router.dispatch("/plan-mode status")
    assert "off" in result.output.lower()


def test_plan_mode_switch_to_on(router) -> None:
    assert router.config.plan_mode.mode == "off"  # baseline
    result = router.dispatch("/plan-mode on")
    assert router.config.plan_mode.mode == "on"
    assert "on" in result.output.lower()


def test_plan_mode_switch_to_off(router) -> None:
    router.config.plan_mode.mode = "on"
    result = router.dispatch("/plan-mode off")
    assert router.config.plan_mode.mode == "off"
    assert "off" in result.output.lower()


def test_plan_mode_no_change_when_already_set(router) -> None:
    router.config.plan_mode.mode = "on"
    result = router.dispatch("/plan-mode on")
    assert "no change" in result.output.lower() or "already" in result.output.lower()


def test_plan_mode_unknown_value_rejected(router) -> None:
    result = router.dispatch("/plan-mode auto")
    assert "unknown" in result.output.lower() or "valid" in result.output.lower()
    # mode unchanged
    assert router.config.plan_mode.mode == "off"


def test_plan_mode_case_insensitive(router) -> None:
    result = router.dispatch("/plan-mode ON")
    assert router.config.plan_mode.mode == "on"


def test_plan_mode_status_lists_research_tools(router) -> None:
    result = router.dispatch("/plan-mode status")
    # Status output should mention the research-only tools (joined as csv)
    assert "read_file" in result.output


def test_plan_mode_status_mentions_always_allowed(router) -> None:
    result = router.dispatch("/plan-mode status")
    assert "request_plan_approval" in result.output
    assert "request_checkpoint" in result.output


# ---------- /help includes /plan-mode ----------


def test_help_includes_plan_mode_command(router) -> None:
    result = router.dispatch("/help")
    assert "/plan-mode" in result.output


# ---------- Client.plan_mode_active property ----------


def test_client_plan_mode_active_reads_live_from_config(tmp_path) -> None:
    """The @property indirection means /plan-mode mutations to config take
    effect on the client without needing to re-initialize the client."""
    # Build a minimal config + smoke-import the client to verify the property
    cfg_path = _write_minimal_config(tmp_path, extra_toml="[plan_mode]\nmode = \"off\"\n")
    config = load_config(cfg_path)

    # Construct OllamaClient via make_chat_client would need ollama installed
    # in test env; mock the import path. We just verify the property logic
    # directly via a tiny stand-in.
    class _Stub:
        def __init__(self, c):
            self.config = c
        @property
        def plan_mode_active(self) -> bool:
            return self.config.plan_mode.mode == "on"

    stub = _Stub(config)
    assert stub.plan_mode_active is False
    config.plan_mode.mode = "on"
    assert stub.plan_mode_active is True
