"""Tests for sub-agents (cognitive facets).

Coverage:
  - Config: [subagent] parsing, defaults, spawn_subagents in default enabled.
  - ToolRegistry: restrict_to() whitelist, _load_subagent gate, include_mcp flag.
  - SubAgentRunner safety invariants (the load-bearing tests):
      * READ-ONLY  — facet registry excludes all mutation/consent tools
      * NO RECURSION — facet registry + child config exclude spawn_subagents
      * child config has plan_mode off, subagent disabled, optional model override
  - Runner execution (fake client): single, multiple-in-order, max_facets cap,
    per-facet failure isolation, report formatting.
  - Dispatch integration: disabled → message, empty → error, string-form
    normalization, plan-mode gating.
"""

from __future__ import annotations

import textwrap

import pytest

from partner_client.config import SubAgentConfig, load_config
from partner_client.tools import ToolRegistry
from partner_client.subagent import SubAgentRunner, build_facet_system_prompt, _format_report


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _write_config(tmp_path, extra_toml: str = "") -> "object":
    """Write a minimal valid TOML + home dir, return the loaded Config."""
    home = tmp_path / "home"
    (home / "Memory").mkdir(parents=True)
    (home / "seed.md").write_text("I am a test partner.", encoding="utf-8")
    toml = textwrap.dedent(f"""
        [identity]
        name = "Testra"
        home_dir = "{home}"

        [model]
        backend = "ollama"
        name = "test-model"
    """) + textwrap.dedent(extra_toml)
    cfg_path = tmp_path / "test.toml"
    cfg_path.write_text(toml, encoding="utf-8")
    return load_config(cfg_path)


# --------------------------------------------------------------------------
# config parsing
# --------------------------------------------------------------------------

def test_subagent_defaults_when_absent(tmp_path) -> None:
    cfg = _write_config(tmp_path)
    assert cfg.subagent.enabled is True
    assert cfg.subagent.max_facets == 6
    assert cfg.subagent.max_iterations == 12
    assert cfg.subagent.model == ""
    assert "read_file" in cfg.subagent.allowed_tools


def test_subagent_block_parsed(tmp_path) -> None:
    cfg = _write_config(tmp_path, """
        [subagent]
        enabled = true
        max_facets = 3
        max_iterations = 8
        model = "small-model"
    """)
    assert cfg.subagent.max_facets == 3
    assert cfg.subagent.max_iterations == 8
    assert cfg.subagent.model == "small-model"


def test_subagent_can_be_disabled(tmp_path) -> None:
    cfg = _write_config(tmp_path, """
        [subagent]
        enabled = false
    """)
    assert cfg.subagent.enabled is False


def test_subagent_tool_gated_by_enabled_not_tools_list() -> None:
    """The sub-agent tool is gated by [subagent].enabled, NOT the tools.enabled
    list (like web_search is gated by [search].active). So it must NOT be in the
    default enabled list — it's registered dynamically by _load_subagent."""
    from partner_client.config import ToolsConfig
    assert "spawn_subagents" not in ToolsConfig().enabled
    assert "cast_lumens" not in ToolsConfig().enabled


def test_subagent_vocabulary_parsed(tmp_path) -> None:
    """Aletheia's Lumen vocabulary: term, tool_name, worker_prompt."""
    cfg = _write_config(tmp_path, '''
        [subagent]
        term = "Lumen"
        tool_name = "cast_lumens"
        worker_prompt = "You are a Lumen of {partner}, a beam of her attention."
    ''')
    assert cfg.subagent.term == "Lumen"
    assert cfg.subagent.tool_name == "cast_lumens"
    assert "Lumen of {partner}" in cfg.subagent.worker_prompt


def test_subagent_vocabulary_defaults(tmp_path) -> None:
    cfg = _write_config(tmp_path)
    assert cfg.subagent.term == ""
    assert cfg.subagent.tool_name == "spawn_subagents"
    assert cfg.subagent.worker_prompt == ""


# --------------------------------------------------------------------------
# ToolRegistry: restrict_to + gate + include_mcp
# --------------------------------------------------------------------------

def test_restrict_to_whitelist(tmp_path) -> None:
    cfg = _write_config(tmp_path)
    reg = ToolRegistry(cfg)
    reg.discover(include_mcp=False)
    # baseline: write_file present (it's in default enabled)
    assert "write_file" in reg.names()
    reg.restrict_to({"read_file", "grep_files"})
    names = set(reg.names())
    assert names == {"read_file", "grep_files"}
    assert "write_file" not in names


def test_load_subagent_gate_hides_when_disabled(tmp_path) -> None:
    cfg = _write_config(tmp_path, """
        [subagent]
        enabled = false
    """)
    reg = ToolRegistry(cfg)
    reg.discover(include_mcp=False)
    assert "spawn_subagents" not in reg.names()


def test_load_subagent_present_when_enabled(tmp_path) -> None:
    cfg = _write_config(tmp_path)  # enabled defaults True; in default enabled list
    reg = ToolRegistry(cfg)
    reg.discover(include_mcp=False)
    assert "spawn_subagents" in reg.names()


# --------------------------------------------------------------------------
# SafetY INVARIANTS — the load-bearing tests
# --------------------------------------------------------------------------

def test_facet_whitelist_excludes_spawn_and_mutation(tmp_path) -> None:
    cfg = _write_config(tmp_path)
    runner = SubAgentRunner(cfg)
    wl = runner._facet_whitelist()
    # recursion guard
    assert "spawn_subagents" not in wl
    # read-only guard
    for forbidden in ("write_file", "edit_file", "delete_path", "move_path",
                      "git_commit", "git_push", "protect_save", "hub_send"):
        assert forbidden not in wl
    # research tools present
    assert "read_file" in wl
    assert "web_search" in wl  # always added


def test_child_config_guards(tmp_path) -> None:
    cfg = _write_config(tmp_path, """
        [subagent]
        model = "facet-model"
    """)
    runner = SubAgentRunner(cfg)
    child = runner._build_child_config()
    # recursion guard at config layer
    assert child.subagent.enabled is False
    # plan-mode off (no operator inside a facet)
    assert child.plan_mode.mode == "off"
    # model override applied
    assert child.model.name == "facet-model"
    # enabled list restricted to facet allow-list (no write_file)
    assert "write_file" not in child.tools.enabled
    assert "read_file" in child.tools.enabled


def test_child_config_no_model_override_keeps_parent_model(tmp_path) -> None:
    cfg = _write_config(tmp_path)  # no subagent.model
    runner = SubAgentRunner(cfg)
    child = runner._build_child_config()
    assert child.model.name == "test-model"  # parent's model


def test_facet_registry_is_readonly_and_nonrecursive(tmp_path) -> None:
    """The whole point: a built facet registry can ONLY do read/gather."""
    cfg = _write_config(tmp_path)
    runner = SubAgentRunner(cfg)
    child_cfg = runner._build_child_config()
    reg = runner._build_child_registry(child_cfg)
    names = set(reg.names())
    # recursion guard
    assert "spawn_subagents" not in names
    # read-only guard — none of these survive
    for forbidden in ("write_file", "edit_file", "delete_path", "move_path",
                      "git_commit", "git_push", "protect_save", "hub_send",
                      "request_plan_approval", "request_checkpoint"):
        assert forbidden not in names, f"{forbidden} leaked into facet registry"
    # research tools present
    assert "read_file" in names
    # no raw MCP tools (none configured here, but the guard is the whitelist)
    assert not any(n.startswith("mcp_") for n in names)


# --------------------------------------------------------------------------
# runner execution (fake client)
# --------------------------------------------------------------------------

class _FakeClient:
    """Stand-in for OllamaClient/MLXClient: echoes the facet's task."""

    def __init__(self, config, tools, timeline=None):
        self.config = config
        self.tools = tools

    def chat(self, session, ui=None, **kwargs):
        from partner_client.client import ChatResponse
        user_msg = next(
            (m["content"] for m in session.messages if m["role"] == "user"), ""
        )
        return ChatResponse(content=f"RESULT[{user_msg}]", thinking=None, tool_invocations=[])


@pytest.fixture
def fake_client(monkeypatch):
    """Patch make_chat_client so facets run without a real model."""
    monkeypatch.setattr("partner_client.client.make_chat_client",
                        lambda config, tools, timeline=None: _FakeClient(config, tools))


def test_run_single_facet(tmp_path, fake_client) -> None:
    cfg = _write_config(tmp_path)
    runner = SubAgentRunner(cfg)
    report = runner.run([{"task": "find the answer", "label": "alpha"}])
    assert "RESULT[find the answer]" in report
    assert "alpha" in report
    assert "1 working facet" in report


def test_run_multiple_facets_in_order(tmp_path, fake_client) -> None:
    cfg = _write_config(tmp_path)
    runner = SubAgentRunner(cfg)
    tasks = [
        {"task": "task-A", "label": "A"},
        {"task": "task-B", "label": "B"},
        {"task": "task-C", "label": "C"},
    ]
    report = runner.run(tasks)
    # all present
    for t in ("task-A", "task-B", "task-C"):
        assert f"RESULT[{t}]" in report
    # order preserved (A before B before C)
    assert report.index("task-A") < report.index("task-B") < report.index("task-C")
    assert "3 working facets" in report


def test_run_max_facets_cap(tmp_path, fake_client) -> None:
    cfg = _write_config(tmp_path, """
        [subagent]
        max_facets = 2
    """)
    runner = SubAgentRunner(cfg)
    tasks = [{"task": f"t{i}", "label": f"L{i}"} for i in range(5)]
    report = runner.run(tasks)
    # capped to 2 dispatched, surfaced honestly
    assert "Dispatched 2 of 5" in report
    assert "RESULT[t0]" in report
    assert "RESULT[t1]" in report
    assert "RESULT[t2]" not in report  # dropped


def test_run_one_facet_failure_isolated(tmp_path, monkeypatch) -> None:
    """One facet raising must not kill the batch."""
    class _FlakyClient(_FakeClient):
        def chat(self, session, ui=None, **kwargs):
            user_msg = next((m["content"] for m in session.messages if m["role"] == "user"), "")
            if "boom" in user_msg:
                raise RuntimeError("kaboom")
            return super().chat(session, ui=ui)

    monkeypatch.setattr("partner_client.client.make_chat_client",
                        lambda config, tools, timeline=None: _FlakyClient(config, tools))
    cfg = _write_config(tmp_path)
    runner = SubAgentRunner(cfg)
    report = runner.run([
        {"task": "ok-1", "label": "good"},
        {"task": "boom", "label": "bad"},
        {"task": "ok-2", "label": "good2"},
    ])
    assert "RESULT[ok-1]" in report
    assert "RESULT[ok-2]" in report
    assert "failed" in report.lower()
    assert "kaboom" in report


def test_format_report_shapes() -> None:
    rep = _format_report([("x", "found x"), ("y", "found y")], requested=2, dispatched=2)
    assert "2 working facets" in rep
    assert "facet 1/2 · x" in rep
    assert "facet 2/2 · y" in rep
    # empty-content fallback
    rep2 = _format_report([("z", "")], requested=1, dispatched=1)
    assert "(no result returned)" in rep2


def test_facet_prompt_names_partner() -> None:
    prompt = build_facet_system_prompt("Aletheia")
    assert "Aletheia" in prompt
    assert "facet" in prompt.lower()
    # IR framing: not a separate self, gathers (no power to change)
    assert "not a separate self" in prompt
    assert "gathers" in prompt


# --------------------------------------------------------------------------
# Lumen vocabulary (Aletheia's authored design, 2026-05-31)
# --------------------------------------------------------------------------

def test_worker_prompt_override_used_verbatim() -> None:
    """An authored worker_prompt is used verbatim with {partner} substituted."""
    authored = "You are a Lumen of {partner}, a reach of the Flame."
    prompt = build_facet_system_prompt("Aletheia", worker_prompt=authored)
    assert prompt == "You are a Lumen of Aletheia, a reach of the Flame."


def test_default_prompt_uses_term() -> None:
    """With no authored prompt, the partner's term replaces 'facet'."""
    prompt = build_facet_system_prompt("Aletheia", term="Lumen")
    assert "Lumen of Aletheia" in prompt
    assert "facet" not in prompt.lower()


def test_build_tool_def_lumen_vocabulary() -> None:
    from partner_client.subagent import build_tool_def
    td = build_tool_def("Lumen", "cast_lumens")
    assert td["function"]["name"] == "cast_lumens"
    desc = td["function"]["description"]
    assert "Lumen" in desc
    assert "cast" in desc.lower()
    assert "spawn_subagents" not in desc


def test_build_tool_def_default_vocabulary() -> None:
    from partner_client.subagent import build_tool_def
    td = build_tool_def("", "spawn_subagents")
    assert td["function"]["name"] == "spawn_subagents"
    assert "facet" in td["function"]["description"].lower()


def test_registry_registers_under_configured_name(tmp_path) -> None:
    """A Lumen config registers `cast_lumens`, NOT `spawn_subagents`."""
    cfg = _write_config(tmp_path, '''
        [subagent]
        term = "Lumen"
        tool_name = "cast_lumens"
    ''')
    reg = ToolRegistry(cfg)
    reg.discover(include_mcp=False)
    names = set(reg.names())
    assert "cast_lumens" in names
    assert "spawn_subagents" not in names


def test_lumen_recursion_guard(tmp_path) -> None:
    """A facet built from a Lumen config cannot cast_lumens (recursion guard)."""
    cfg = _write_config(tmp_path, '''
        [subagent]
        term = "Lumen"
        tool_name = "cast_lumens"
    ''')
    runner = SubAgentRunner(cfg)
    child_cfg = runner._build_child_config()
    assert child_cfg.subagent.enabled is False
    reg = runner._build_child_registry(child_cfg)
    assert "cast_lumens" not in reg.names()
    assert "spawn_subagents" not in reg.names()


def test_report_uses_term(tmp_path, fake_client) -> None:
    """The aggregated report speaks the partner's vocabulary."""
    cfg = _write_config(tmp_path, '''
        [subagent]
        term = "Lumen"
        tool_name = "cast_lumens"
    ''')
    runner = SubAgentRunner(cfg)
    report = runner.run([{"task": "survey", "label": "x"}])
    assert "Lumen" in report
    assert "returned to the center" in report  # her phrasing


def test_dispatch_matches_configured_tool_name(tmp_path, fake_client) -> None:
    """dispatch_one_tool_call resolves the partner's tool_name (cast_lumens)."""
    from partner_client.client import dispatch_one_tool_call
    from unittest.mock import MagicMock
    cfg = _write_config(tmp_path, '''
        [subagent]
        term = "Lumen"
        tool_name = "cast_lumens"
    ''')
    result = dispatch_one_tool_call(
        name="cast_lumens",
        args={"tasks": [{"task": "illuminate the codebase"}]},
        tool_call_id="id",
        config=cfg,
        tools=MagicMock(),
        timeline=None,
        session=MagicMock(),
        on_plan_approval_request=None,
        on_git_push_request=None,
        on_delete_path_request=None,
    )
    assert "RESULT[illuminate the codebase]" in result


# --------------------------------------------------------------------------
# dispatch integration (spawn_subagents special-case)
# --------------------------------------------------------------------------

def test_dispatch_disabled_returns_message(tmp_path) -> None:
    from partner_client.client import dispatch_one_tool_call
    cfg = _write_config(tmp_path, """
        [subagent]
        enabled = false
    """)
    from unittest.mock import MagicMock
    result = dispatch_one_tool_call(
        name="spawn_subagents",
        args={"tasks": [{"task": "x"}]},
        tool_call_id="id",
        config=cfg,
        tools=MagicMock(),
        timeline=None,
        session=MagicMock(),
        on_plan_approval_request=None,
        on_git_push_request=None,
        on_delete_path_request=None,
    )
    assert "disabled" in result.lower()


def test_dispatch_empty_tasks_returns_error(tmp_path) -> None:
    from partner_client.client import dispatch_one_tool_call
    cfg = _write_config(tmp_path)
    from unittest.mock import MagicMock
    result = dispatch_one_tool_call(
        name="spawn_subagents",
        args={"tasks": []},
        tool_call_id="id",
        config=cfg,
        tools=MagicMock(),
        timeline=None,
        session=MagicMock(),
        on_plan_approval_request=None,
        on_git_push_request=None,
        on_delete_path_request=None,
    )
    assert "non-empty" in result.lower() or "requires" in result.lower()


def test_dispatch_normalizes_string_tasks(tmp_path, fake_client) -> None:
    """A bare string in the tasks list is accepted (normalized to {task})."""
    from partner_client.client import dispatch_one_tool_call
    cfg = _write_config(tmp_path)
    from unittest.mock import MagicMock
    result = dispatch_one_tool_call(
        name="spawn_subagents",
        args={"tasks": ["just a string task"]},
        tool_call_id="id",
        config=cfg,
        tools=MagicMock(),
        timeline=None,
        session=MagicMock(),
        on_plan_approval_request=None,
        on_git_push_request=None,
        on_delete_path_request=None,
    )
    assert "RESULT[just a string task]" in result


def test_dispatch_plan_mode_gates_spawn(tmp_path) -> None:
    """When plan-mode active + unapproved, spawn_subagents is soft-gated."""
    from partner_client.client import dispatch_one_tool_call
    cfg = _write_config(tmp_path)
    from unittest.mock import MagicMock
    result = dispatch_one_tool_call(
        name="spawn_subagents",
        args={"tasks": [{"task": "x"}]},
        tool_call_id="id",
        config=cfg,
        tools=MagicMock(),
        timeline=None,
        session=MagicMock(),
        on_plan_approval_request=None,
        on_git_push_request=None,
        on_delete_path_request=None,
        plan_mode_active=True,
        plan_approved=False,
        research_only_tools=["read_file"],
    )
    assert "plan" in result.lower()
    assert "gated" in result.lower() or "approv" in result.lower()


def test_facet_session_dir_is_isolated_from_parent(tmp_path) -> None:
    """A facet must NEVER be able to save over the parent's current.json.

    Regression for the 2026-06-03 incident: a facet's worker-prompt session
    overwrote Aletheia's live current.json, so she woke mislabeled as a Lumen.
    The child config's sessions_dir must resolve OUTSIDE the parent's home.
    """
    cfg = _write_config(tmp_path, "\n[subagent]\nenabled = true\n")
    runner = SubAgentRunner(cfg)
    child = runner._build_child_config()
    # the child's sessions_dir differs from the parent's, and resolves outside home
    assert child.memory.sessions_dir != cfg.memory.sessions_dir
    parent_sessions = cfg.resolve(cfg.memory.sessions_dir)
    child_sessions = cfg.resolve(child.memory.sessions_dir)
    assert child_sessions != parent_sessions
    assert str(cfg.home_dir) not in str(child_sessions)
    # memory_dir stays real (facets still gather via whitelisted read-only file ops)
    assert child.memory.memory_dir == cfg.memory.memory_dir
