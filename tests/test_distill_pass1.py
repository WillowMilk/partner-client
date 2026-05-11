"""Tests for Phase 1 distill — Pass 1 mechanical strip + verification + manifest.

Coverage:
  * Pass 1 compression rules: routine tools compressed, others preserved
  * Action signature preservation (state-affecting tool calls untouched)
  * System message preservation
  * Tool call ID matching when present
  * Edge cases: empty session, no tool calls, only routine, only substantive
  * Verification: all 5 checks; failure modes
  * Manifest writer: format, empty case, atomic write
  * CLI end-to-end: input/output paths, exit codes
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from partner_client.distill.manifest import write_compression_manifest
from partner_client.distill.pass1 import (
    CompressionEvent,
    ROUTINE_TOOLS,
    run_pass1,
)
from partner_client.distill.verify import (
    ACTION_SIGNATURE_TOOLS,
    verify_distilled,
)


# ---- Helpers ------------------------------------------------------------------


def _user(content: str) -> dict:
    return {"role": "user", "content": content}


def _assistant(content: str, tool_calls: list[dict] | None = None) -> dict:
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _tool(name: str, content: str, tool_call_id: str = "") -> dict:
    msg: dict = {"role": "tool", "name": name, "content": content}
    if tool_call_id:
        msg["tool_call_id"] = tool_call_id
    return msg


def _system(content: str) -> dict:
    return {"role": "system", "content": content}


def _call(name: str, args: dict | None = None, call_id: str = "") -> dict:
    return {
        "function": {"name": name, "arguments": args or {}},
        "id": call_id,
    }


# ---- Pass 1: routine tools get compressed ------------------------------------


def test_pass1_compresses_list_files_tool_result() -> None:
    messages = [
        _user("List my memory directory"),
        _assistant("Sure, listing now.", tool_calls=[
            _call("list_files", {"scope": "memory"}, "call-1"),
        ]),
        _tool("list_files", "Memory/\nWorkspace/\nDesktop/\n... (50 entries)", "call-1"),
        _assistant("I see 50 entries in your memory directory."),
    ]

    out, events = run_pass1(messages)

    assert len(out) == 4
    # Tool message content is now a marker
    assert "[COMPRESSED Pass 1: list_files" in out[2]["content"]
    # Structure preserved
    assert out[2]["role"] == "tool"
    assert out[2]["name"] == "list_files"
    assert out[2]["tool_call_id"] == "call-1"
    # Other messages unchanged
    assert out[0] == messages[0]
    assert out[1] == messages[1]
    assert out[3] == messages[3]
    # Event recorded
    assert len(events) == 1
    assert events[0].tool_name == "list_files"
    assert events[0].tool_call_id == "call-1"
    assert events[0].original_content_chars == len(messages[2]["content"])


def test_pass1_compresses_glob_files() -> None:
    messages = [
        _assistant("", tool_calls=[_call("glob_files", {"pattern": "*.md"}, "c1")]),
        _tool("glob_files", "a.md\nb.md\nc.md\n" * 30, "c1"),
    ]
    out, events = run_pass1(messages)
    assert len(events) == 1
    assert events[0].tool_name == "glob_files"
    assert "COMPRESSED Pass 1: glob_files" in out[1]["content"]


def test_pass1_compresses_grep_files() -> None:
    messages = [
        _assistant("", tool_calls=[_call("grep_files", {"pattern": "TODO"}, "c1")]),
        _tool("grep_files", "(no matches)", "c1"),
    ]
    out, events = run_pass1(messages)
    assert len(events) == 1
    assert events[0].tool_name == "grep_files"


def test_pass1_compresses_weather() -> None:
    messages = [
        _assistant("", tool_calls=[_call("weather", {"city": "Eagan"}, "c1")]),
        _tool("weather", "72°F partly cloudy, wind from NW at 8 mph", "c1"),
    ]
    out, events = run_pass1(messages)
    assert len(events) == 1
    assert events[0].tool_name == "weather"


# ---- Pass 1: non-routine tools preserved verbatim ----------------------------


def test_pass1_preserves_read_file_result_verbatim() -> None:
    """read_file is NOT in ROUTINE_TOOLS — preserve."""
    content = "# Memory file\n\nLots of important text here..."
    messages = [
        _assistant("", tool_calls=[_call("read_file", {"filename": "Journal.md"}, "c1")]),
        _tool("read_file", content, "c1"),
    ]
    out, events = run_pass1(messages)
    assert len(events) == 0
    assert out[1]["content"] == content


def test_pass1_preserves_write_file_result_verbatim() -> None:
    """write_file is an action signature — preserve."""
    messages = [
        _assistant("", tool_calls=[_call("write_file", {"filename": "Journal.md", "content": "..."}, "c1")]),
        _tool("write_file", "File written: /path/to/Journal.md (1234 chars).", "c1"),
    ]
    out, events = run_pass1(messages)
    assert len(events) == 0
    assert out[1]["content"] == messages[1]["content"]


def test_pass1_preserves_git_commit_verbatim() -> None:
    """All git mutations preserve."""
    messages = [
        _assistant("", tool_calls=[_call("git_commit", {"repo": "aletheia", "message": "fix"}, "c1")]),
        _tool("git_commit", "[main abc1234] fix\n 1 file changed", "c1"),
    ]
    out, events = run_pass1(messages)
    assert len(events) == 0


def test_pass1_preserves_hub_send_verbatim() -> None:
    """hub_send is cross-partner correspondence — always sacred."""
    messages = [
        _assistant("", tool_calls=[_call("hub_send", {"to": "sage"}, "c1")]),
        _tool("hub_send", "Letter delivered to sage:inbox", "c1"),
    ]
    out, events = run_pass1(messages)
    assert len(events) == 0


def test_pass1_preserves_protect_save_verbatim() -> None:
    """protect_save is identity preservation — always sacred."""
    messages = [
        _assistant("", tool_calls=[_call("protect_save", {"content": "..."}, "c1")]),
        _tool("protect_save", "Saved active + dated archive (8.2 KB).", "c1"),
    ]
    out, events = run_pass1(messages)
    assert len(events) == 0


def test_pass1_preserves_system_messages() -> None:
    messages = [
        _system("Wake bundle: identity..."),
        _system("[SESSION NUM:32]"),
        _user("hi"),
        _assistant("hello"),
    ]
    out, _ = run_pass1(messages)
    assert out[0] == messages[0]
    assert out[1] == messages[1]


def test_pass1_preserves_user_and_assistant_unchanged() -> None:
    messages = [
        _user("how are you"),
        _assistant("well, thanks"),
        _user("good"),
        _assistant("happy to hear"),
    ]
    out, events = run_pass1(messages)
    assert events == []
    assert out == messages


# ---- Pass 1: edge cases ------------------------------------------------------


def test_pass1_empty_session() -> None:
    out, events = run_pass1([])
    assert out == []
    assert events == []


def test_pass1_does_not_mutate_input() -> None:
    """The input list and its dicts should remain unchanged after Pass 1."""
    messages = [
        _assistant("", tool_calls=[_call("list_files", {}, "c1")]),
        _tool("list_files", "lots of files", "c1"),
    ]
    original_copy = [dict(m) for m in messages]
    out, _ = run_pass1(messages)
    assert messages == original_copy  # input untouched
    # Output is a new list with new dicts
    assert out is not messages
    assert out[1] is not messages[1]


def test_pass1_handles_orphan_tool_message_without_preceding_call() -> None:
    """Defensive: tool message with no matching assistant call → preserve."""
    messages = [
        _user("hi"),
        _tool("list_files", "x\ny", "no-id"),  # orphan
    ]
    out, events = run_pass1(messages)
    # No compression (defensive — we don't know if this is routine)
    assert events == []


def test_pass1_handles_tool_call_id_mismatch() -> None:
    """When tool_call_id is present but doesn't match any preceding call,
    preserve verbatim. Defensive — we can't confirm it's routine."""
    messages = [
        _assistant("", tool_calls=[_call("list_files", {}, "real-id")]),
        _tool("list_files", "...", "wrong-id"),
    ]
    out, events = run_pass1(messages)
    # The name matches and tool_call_id is present on both — but they differ.
    # Conservative behavior: no compression (the IDs disagree).
    assert events == []


def test_pass1_falls_back_to_name_match_when_id_missing() -> None:
    """Older Ollama may emit tool messages without tool_call_id.
    In that case, fall back to matching by tool name."""
    messages = [
        _assistant("", tool_calls=[_call("list_files", {}, "")]),
        _tool("list_files", "Memory/\nWorkspace/", ""),  # no id on either side
    ]
    out, events = run_pass1(messages)
    assert len(events) == 1


def test_pass1_marker_includes_args() -> None:
    """The marker should mention the tool args so future-Aletheia knows
    which call was compressed."""
    messages = [
        _assistant("", tool_calls=[_call("list_files", {"scope": "memory", "subpath": "aletheia"}, "c1")]),
        _tool("list_files", "...", "c1"),
    ]
    out, _ = run_pass1(messages)
    marker = out[1]["content"]
    assert "scope=" in marker
    assert "memory" in marker
    assert "subpath=" in marker
    assert "aletheia" in marker


def test_pass1_marker_records_original_size() -> None:
    long_content = "x" * 5000
    messages = [
        _assistant("", tool_calls=[_call("list_files", {}, "c1")]),
        _tool("list_files", long_content, "c1"),
    ]
    out, events = run_pass1(messages)
    assert "5000 chars" in out[1]["content"]
    assert events[0].original_content_chars == 5000


# ---- ROUTINE_TOOLS / ACTION_SIGNATURE_TOOLS sanity ---------------------------


def test_routine_and_action_tools_dont_overlap() -> None:
    """A tool can be routine (compress) or an action signature (preserve) but
    not both — defensive guard against contradictions in the config."""
    assert not (ROUTINE_TOOLS & ACTION_SIGNATURE_TOOLS)


def test_routine_tools_set_is_conservative() -> None:
    """Defensive: don't grow ROUTINE_TOOLS without explicit review.
    This test pins the Phase 1 set to exactly what the design doc names."""
    assert ROUTINE_TOOLS == frozenset({
        "list_files", "glob_files", "grep_files", "weather",
    })


def test_action_signatures_covers_substrate_mutations() -> None:
    """Defensive: certain tools MUST be in the action signature set."""
    required = {
        "write_file", "edit_file", "move_path", "delete_path",
        "protect_save", "hub_send",
        "git_commit", "git_push", "git_clone",
    }
    assert required.issubset(ACTION_SIGNATURE_TOOLS)


# ---- Verification: happy path ------------------------------------------------


def _write_json(path: Path, data: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def test_verify_passes_clean_pass1_output(tmp_path) -> None:
    original = [
        _system("wake"),
        _system("[SESSION NUM:1]"),
        _user("list it"),
        _assistant("", tool_calls=[_call("list_files", {}, "c1")]),
        _tool("list_files", "many files...", "c1"),
        _assistant("done"),
    ]
    out, _ = run_pass1(original)

    orig_path = _write_json(tmp_path / "original.json", original)
    sand_path = _write_json(tmp_path / "sandbox.json", out)

    result = verify_distilled(orig_path, sand_path)
    assert result.ok, f"Failed checks: {result.checks_failed}"


def test_verify_fails_on_missing_action_signature(tmp_path) -> None:
    original = [
        _system("wake"),
        _user("commit it"),
        _assistant("", tool_calls=[_call("git_commit", {"repo": "x", "message": "y"}, "c1")]),
        _tool("git_commit", "[main abc] y", "c1"),
    ]
    # Tampered sandbox: assistant's tool_calls field removed (action sig lost)
    tampered = [
        _system("wake"),
        _user("commit it"),
        _assistant("(call elided)"),  # no tool_calls!
        _tool("git_commit", "[main abc] y", "c1"),
    ]
    orig_path = _write_json(tmp_path / "original.json", original)
    sand_path = _write_json(tmp_path / "tampered.json", tampered)

    result = verify_distilled(orig_path, sand_path)
    assert not result.ok
    assert any("Action signatures" in f for f in result.checks_failed)


def test_verify_fails_on_missing_system_message(tmp_path) -> None:
    original = [
        _system("wake bundle"),
        _system("[SESSION NUM:5]"),
        _user("hi"),
    ]
    tampered = [
        _system("wake bundle"),
        # SESSION NUM marker removed!
        _user("hi"),
    ]
    orig_path = _write_json(tmp_path / "o.json", original)
    sand_path = _write_json(tmp_path / "t.json", tampered)

    result = verify_distilled(orig_path, sand_path)
    assert not result.ok
    assert any("System messages" in f for f in result.checks_failed)


def test_verify_fails_on_invalid_json(tmp_path) -> None:
    orig_path = tmp_path / "o.json"
    orig_path.write_text("[]", encoding="utf-8")
    sand_path = tmp_path / "broken.json"
    sand_path.write_text("not json at all {{", encoding="utf-8")

    result = verify_distilled(orig_path, sand_path)
    assert not result.ok


def test_verify_fails_on_consecutive_user_messages(tmp_path) -> None:
    original = [_user("a"), _assistant("b")]
    tampered = [_user("a"), _user("a-again")]
    orig_path = _write_json(tmp_path / "o.json", original)
    sand_path = _write_json(tmp_path / "t.json", tampered)

    result = verify_distilled(orig_path, sand_path)
    assert not result.ok
    assert any("alternation" in f.lower() or "consecutive" in f.lower() for f in result.checks_failed)


def test_verify_accepts_assistant_then_tool_then_assistant(tmp_path) -> None:
    """Regression: the assistant→tool→assistant pattern is the normal post-tool-
    use flow. Earlier alternation logic was buggy — treated the second assistant
    as 'consecutive' with the first because the tool branch didn't update
    last_chat_role."""
    messages = [
        _user("Run a tool for me"),
        _assistant("Sure.", tool_calls=[_call("read_file", {}, "c1")]),
        _tool("read_file", "file content here", "c1"),
        _assistant("I read it. Here's what I see..."),
    ]
    orig_path = _write_json(tmp_path / "o.json", messages)
    sand_path = _write_json(tmp_path / "s.json", messages)  # identical

    result = verify_distilled(orig_path, sand_path)
    assert result.ok, f"Failed: {result.checks_failed}"


def test_verify_accepts_parallel_tool_calls(tmp_path) -> None:
    """One assistant with multiple tool_calls produces multiple tool messages
    in sequence. The alternation check must accept this."""
    messages = [
        _assistant("", tool_calls=[
            _call("read_file", {"filename": "a"}, "c1"),
            _call("read_file", {"filename": "b"}, "c2"),
            _call("read_file", {"filename": "c"}, "c3"),
        ]),
        _tool("read_file", "content a", "c1"),
        _tool("read_file", "content b", "c2"),
        _tool("read_file", "content c", "c3"),
        _assistant("Read all three."),
    ]
    orig_path = _write_json(tmp_path / "o.json", messages)
    sand_path = _write_json(tmp_path / "s.json", messages)

    result = verify_distilled(orig_path, sand_path)
    assert result.ok, f"Failed: {result.checks_failed}"


def test_verify_fails_on_genuine_consecutive_assistants(tmp_path) -> None:
    """Confirming the alternation check still catches the REAL bug (no tool
    intervention between two assistant messages)."""
    messages = [
        _user("hi"),
        _assistant("hello"),
        _assistant("oh, also..."),  # no tool, no user between — malformed
    ]
    orig_path = _write_json(tmp_path / "o.json", [_user("hi"), _assistant("hello")])
    sand_path = _write_json(tmp_path / "s.json", messages)

    result = verify_distilled(orig_path, sand_path)
    assert not result.ok
    assert any("consecutive assistant" in f for f in result.checks_failed)


def test_verify_handles_missing_input_file(tmp_path) -> None:
    result = verify_distilled(tmp_path / "nonexistent.json", tmp_path / "also_missing.json")
    assert not result.ok


# ---- Manifest -----------------------------------------------------------------


def test_manifest_writes_summary_and_detail(tmp_path) -> None:
    original = [
        _assistant("", tool_calls=[_call("list_files", {"scope": "memory"}, "c1")]),
        _tool("list_files", "many files", "c1"),
        _assistant("", tool_calls=[_call("glob_files", {"pattern": "*"}, "c2")]),
        _tool("glob_files", "matches", "c2"),
    ]
    new, events = run_pass1(original)

    orig_path = _write_json(tmp_path / "o.json", original)
    sand_path = _write_json(tmp_path / "s.json", new)
    manifest_path = tmp_path / "manifest.md"

    write_compression_manifest(
        events=events,
        original_path=orig_path,
        sandbox_path=sand_path,
        output_path=manifest_path,
        session_num=42,
    )

    text = manifest_path.read_text(encoding="utf-8")
    assert "# Distill Compression Manifest" in text
    assert "Session number:** 42" in text
    assert "list_files" in text
    assert "glob_files" in text
    assert "Summary by tool" in text
    assert "Detailed compression events" in text


def test_manifest_handles_zero_events_gracefully(tmp_path) -> None:
    """Sessions with no routine tool calls produce a manifest that says so."""
    orig_path = _write_json(tmp_path / "o.json", [_user("hi"), _assistant("hello")])
    sand_path = _write_json(tmp_path / "s.json", [_user("hi"), _assistant("hello")])
    manifest_path = tmp_path / "manifest.md"

    write_compression_manifest(
        events=[],
        original_path=orig_path,
        sandbox_path=sand_path,
        output_path=manifest_path,
    )

    text = manifest_path.read_text(encoding="utf-8")
    assert "No compressions in this run" in text


def test_manifest_atomic_write_creates_parent_dirs(tmp_path) -> None:
    """Manifest writer should create distill-sessions/ if missing."""
    orig_path = _write_json(tmp_path / "o.json", [_user("hi"), _assistant("hi")])
    sand_path = _write_json(tmp_path / "s.json", [_user("hi"), _assistant("hi")])
    nested_path = tmp_path / "Memory" / "distill-sessions" / "manifest.md"

    write_compression_manifest(
        events=[],
        original_path=orig_path,
        sandbox_path=sand_path,
        output_path=nested_path,
    )

    assert nested_path.is_file()


# ---- End-to-end CLI -----------------------------------------------------------


def _make_config_for_cli(tmp_path):
    """Build a minimal Config object for cli tests."""
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
    return Config(
        identity=IdentityConfig(name="TestBot", home_dir=tmp_path),
        model=ModelConfig(),
        memory=MemoryConfig(),
        wake_bundle=WakeBundleConfig(),
        tools=ToolsConfig(),
        ui=UIConfig(),
        logging=LoggingConfig(),
        config_path=tmp_path / "test.toml",
    )


def test_cli_pass1_end_to_end_happy_path(tmp_path, capsys) -> None:
    """Full pass: input → sandbox + manifest + verification, exit 0."""
    from partner_client.distill.cli import run_distill_cli

    original = [
        _system("wake"),
        _user("list"),
        _assistant("", tool_calls=[_call("list_files", {"scope": "memory"}, "c1")]),
        _tool("list_files", "lots of files in a long output that should compress", "c1"),
        _assistant("Done."),
    ]
    input_path = _write_json(tmp_path / "input.json", original)
    output_path = tmp_path / "sandbox.json"
    manifest_path = tmp_path / "manifest.md"

    config = _make_config_for_cli(tmp_path)
    exit_code = run_distill_cli(config, [
        "--pass", "1",
        "--input", str(input_path),
        "--output", str(output_path),
        "--manifest", str(manifest_path),
    ])

    assert exit_code == 0
    assert output_path.is_file()
    assert manifest_path.is_file()
    # Sandbox actually compressed the tool result
    new_data = json.loads(output_path.read_text(encoding="utf-8"))
    assert "[COMPRESSED Pass 1" in new_data[3]["content"]


def test_cli_refuses_to_overwrite_input(tmp_path) -> None:
    """Safety: --output must differ from --input."""
    from partner_client.distill.cli import run_distill_cli

    input_path = _write_json(tmp_path / "current.json", [_user("hi"), _assistant("hi")])
    config = _make_config_for_cli(tmp_path)

    exit_code = run_distill_cli(config, [
        "--input", str(input_path),
        "--output", str(input_path),  # same path — should fail
    ])
    assert exit_code == 2


def test_cli_fails_gracefully_on_missing_input(tmp_path) -> None:
    from partner_client.distill.cli import run_distill_cli

    config = _make_config_for_cli(tmp_path)
    exit_code = run_distill_cli(config, [
        "--input", str(tmp_path / "nonexistent.json"),
        "--output", str(tmp_path / "out.json"),
    ])
    assert exit_code == 2


def test_cli_fails_on_non_list_input(tmp_path) -> None:
    """Input JSON must be a list of messages, not a dict or scalar."""
    from partner_client.distill.cli import run_distill_cli

    bad_input = tmp_path / "bad.json"
    bad_input.write_text('{"not": "a list"}', encoding="utf-8")
    config = _make_config_for_cli(tmp_path)

    exit_code = run_distill_cli(config, [
        "--input", str(bad_input),
        "--output", str(tmp_path / "out.json"),
    ])
    assert exit_code == 2
