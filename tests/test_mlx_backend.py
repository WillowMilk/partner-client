"""Tests for Track B — MLX backend (MLXClient + factory + doctor adaptations).

Verifies:
  - ModelConfig.backend validation + defaults + back-compat
  - make_chat_client factory routes to correct class
  - dispatch_one_tool_call shared helper (extracted from OllamaClient)
  - MLXClient init / scopes / prewarm via mocked openai SDK
  - MLXClient.chat streaming (content + reasoning -> thinking)
  - MLXClient.chat tool calls (per-index accumulation across deltas)
  - MLXClient.chat consent gates flow through shared helper
  - MLXClient.close() shuts down auto-started subprocess
  - doctor _check_mlx_lm_installed / _check_mlx_server_reachable /
    _check_mlx_model_in_hf_cache skip-vs-fire based on backend
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from partner_client.config import ConfigError, ModelConfig


# ---------- ModelConfig.backend ----------


def test_model_config_backend_defaults_to_ollama() -> None:
    m = ModelConfig()
    assert m.backend == "ollama"


def test_model_config_accepts_mlx_lm() -> None:
    m = ModelConfig(backend="mlx-lm")
    assert m.backend == "mlx-lm"


def test_model_config_rejects_unknown_backend() -> None:
    with pytest.raises(ConfigError, match="model.backend"):
        ModelConfig(backend="vllm")


def test_model_config_mlx_defaults() -> None:
    m = ModelConfig(backend="mlx-lm")
    assert m.mlx_server_url.startswith("http://")
    assert m.mlx_auto_start_server is True
    assert m.mlx_server_extra_args == []
    assert m.mlx_server_start_timeout > 0


def _stub_config(backend: str = "mlx-lm", **model_overrides) -> MagicMock:
    """Mock config that returns string-typed values where setup_scope_env needs them."""
    config = MagicMock()
    config.model = ModelConfig(
        backend=backend,
        mlx_auto_start_server=False,
        **model_overrides,
    )
    config.git = MagicMock()
    config.git.default_committer_name = ""
    config.git.default_committer_email = ""
    config.identity.name = "test"
    config.identity.home_dir = "/tmp/test"
    config.memory.memory_dir = "Memory"
    config.tools.scopes = []
    config.tools.allow_external_reads = []
    config.hub.path = ""
    return config


# ---------- make_chat_client factory ----------


def test_factory_returns_ollama_client_when_backend_is_ollama() -> None:
    from partner_client.client import make_chat_client, OllamaClient
    config = _stub_config(backend="ollama")
    with patch.dict("sys.modules", {"ollama": MagicMock()}):
        client = make_chat_client(config, MagicMock())
    assert isinstance(client, OllamaClient)


def test_factory_returns_mlx_client_when_backend_is_mlx_lm() -> None:
    from partner_client.client import make_chat_client, MLXClient
    config = _stub_config(backend="mlx-lm")
    mock_openai_module = MagicMock()
    mock_openai_module.OpenAI.return_value = MagicMock()
    with patch.dict("sys.modules", {"openai": mock_openai_module}):
        client = make_chat_client(config, MagicMock())
    assert isinstance(client, MLXClient)


# ---------- dispatch_one_tool_call (extracted helper) ----------


def test_dispatch_one_tool_call_routes_to_generic_dispatch_for_unknown_tool() -> None:
    """Default branch: hand off to tools.dispatch."""
    from partner_client.client import dispatch_one_tool_call
    tools = MagicMock()
    tools.dispatch.return_value = "fake result"
    config = MagicMock()
    session = MagicMock()
    session.messages = []
    result = dispatch_one_tool_call(
        name="read_file",
        args={"path": "foo.md"},
        tool_call_id="abc",
        config=config,
        tools=tools,
        timeline=None,
        session=session,
        on_plan_approval_request=None,
        on_git_push_request=None,
        on_delete_path_request=None,
    )
    assert result == "fake result"
    tools.dispatch.assert_called_once_with("read_file", {"path": "foo.md"})


def test_dispatch_one_tool_call_request_checkpoint_injects_system_message() -> None:
    """request_checkpoint runs directly (no gate) and adds a system message."""
    from partner_client.client import dispatch_one_tool_call
    tools = MagicMock()
    config = MagicMock()
    session = MagicMock()
    session.messages = []
    result = dispatch_one_tool_call(
        name="request_checkpoint",
        args={"reason": "test"},
        tool_call_id="x",
        config=config,
        tools=tools,
        timeline=None,
        session=session,
        on_plan_approval_request=None,
        on_git_push_request=None,
        on_delete_path_request=None,
    )
    assert "Checkpoint discipline activated" in result
    assert any(m.get("role") == "system" for m in session.messages)


def test_dispatch_one_tool_call_plan_approval_with_no_callback_returns_helpful_msg() -> None:
    from partner_client.client import dispatch_one_tool_call
    tools = MagicMock()
    config = MagicMock()
    session = MagicMock()
    session.messages = []
    result = dispatch_one_tool_call(
        name="request_plan_approval",
        args={"summary": "test plan", "plan": ["step1"]},
        tool_call_id="x",
        config=config,
        tools=tools,
        timeline=None,
        session=session,
        on_plan_approval_request=None,
        on_git_push_request=None,
        on_delete_path_request=None,
    )
    assert "no operator confirmation handler" in result


def test_dispatch_one_tool_call_plan_approval_with_accept_callback() -> None:
    from partner_client.client import dispatch_one_tool_call
    tools = MagicMock()
    config = MagicMock()
    session = MagicMock()
    session.messages = []
    callback = MagicMock(return_value=(True, None))
    result = dispatch_one_tool_call(
        name="request_plan_approval",
        args={"summary": "test plan", "plan": ["a", "b"]},
        tool_call_id="x",
        config=config,
        tools=tools,
        timeline=None,
        session=session,
        on_plan_approval_request=callback,
        on_git_push_request=None,
        on_delete_path_request=None,
    )
    assert "approved" in result.lower()
    callback.assert_called_once_with("test plan", ["a", "b"])


def test_dispatch_one_tool_call_plan_approval_with_decline_and_message() -> None:
    from partner_client.client import dispatch_one_tool_call
    tools = MagicMock()
    config = MagicMock()
    session = MagicMock()
    session.messages = []
    callback = MagicMock(return_value=(False, "try a smaller plan"))
    result = dispatch_one_tool_call(
        name="request_plan_approval",
        args={"summary": "test", "plan": ["a"]},
        tool_call_id="x",
        config=config,
        tools=tools,
        timeline=None,
        session=session,
        on_plan_approval_request=callback,
        on_git_push_request=None,
        on_delete_path_request=None,
    )
    assert "try a smaller plan" in result


# ---------- MLXClient init + scopes + prewarm ----------


def _make_mlx_client_with_mock_openai():
    """Helper: build an MLXClient with all openai-side deps mocked."""
    from partner_client.client import MLXClient
    mock_openai_instance = MagicMock()
    mock_openai_module = MagicMock()
    mock_openai_module.OpenAI.return_value = mock_openai_instance

    config = _stub_config(backend="mlx-lm")
    with patch.dict("sys.modules", {"openai": mock_openai_module}):
        client = MLXClient(config, MagicMock())
    return client, mock_openai_instance


def test_mlx_client_init_uses_configured_server_url() -> None:
    """MLXClient should pass mlx_server_url to OpenAI(base_url=...)."""
    from partner_client.client import MLXClient
    mock_openai_module = MagicMock()
    config = _stub_config(
        backend="mlx-lm",
        mlx_server_url="http://localhost:9999/v1",
    )
    with patch.dict("sys.modules", {"openai": mock_openai_module}):
        MLXClient(config, MagicMock())
    mock_openai_module.OpenAI.assert_called_once()
    kwargs = mock_openai_module.OpenAI.call_args.kwargs
    assert kwargs["base_url"] == "http://localhost:9999/v1"


def test_mlx_client_prewarm_calls_minimal_completion() -> None:
    client, mock_openai_instance = _make_mlx_client_with_mock_openai()
    mock_openai_instance.chat.completions.create.return_value = MagicMock()
    ok, elapsed, error = client.prewarm()
    assert ok is True
    assert error is None
    assert elapsed >= 0
    mock_openai_instance.chat.completions.create.assert_called_once()
    kwargs = mock_openai_instance.chat.completions.create.call_args.kwargs
    assert kwargs["max_tokens"] == 1
    assert kwargs["stream"] is False


def test_mlx_client_prewarm_handles_error() -> None:
    client, mock_openai_instance = _make_mlx_client_with_mock_openai()
    mock_openai_instance.chat.completions.create.side_effect = RuntimeError("server down")
    ok, elapsed, error = client.prewarm()
    assert ok is False
    assert "server down" in (error or "")


def test_mlx_client_close_idempotent_when_no_subprocess() -> None:
    """close() is safe to call when no auto-launched server exists."""
    client, _ = _make_mlx_client_with_mock_openai()
    client._server_proc = None  # belt-and-suspenders
    client.close()  # should not raise
    client.close()  # second call also safe


def test_mlx_client_close_terminates_subprocess() -> None:
    """close() terminates the auto-launched server process."""
    client, _ = _make_mlx_client_with_mock_openai()
    fake_proc = MagicMock()
    client._server_proc = fake_proc
    client.close()
    fake_proc.terminate.assert_called_once()
    fake_proc.wait.assert_called_once()
    assert client._server_proc is None


# ---------- MLXClient streaming + reasoning -> thinking mapping ----------


def _make_delta_chunk(content=None, reasoning=None, tool_calls=None, finish_reason=None):
    """Build a fake OpenAI streaming chunk."""
    delta = SimpleNamespace(content=content, reasoning=reasoning, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


def test_mlx_client_chat_accumulates_content_only_when_no_tool_calls() -> None:
    """Streaming content tokens accumulate into the final response."""
    client, mock_openai_instance = _make_mlx_client_with_mock_openai()
    chunks = [
        _make_delta_chunk(content="Hello"),
        _make_delta_chunk(content=", "),
        _make_delta_chunk(content="world!"),
        _make_delta_chunk(finish_reason="stop"),
    ]
    mock_openai_instance.chat.completions.create.return_value = iter(chunks)

    session = MagicMock()
    session.messages = []
    session.append_assistant = MagicMock()
    session.estimate_tokens.return_value = 0

    response = client.chat(session=session, ui=None)
    assert response.content == "Hello, world!"
    assert response.thinking is None
    assert response.tool_invocations == []


def test_mlx_client_chat_maps_reasoning_to_thinking() -> None:
    """reasoning field in deltas should accumulate to ChatResponse.thinking."""
    client, mock_openai_instance = _make_mlx_client_with_mock_openai()
    chunks = [
        _make_delta_chunk(reasoning="I am deliberating "),
        _make_delta_chunk(reasoning="about this carefully."),
        _make_delta_chunk(content="Final answer."),
        _make_delta_chunk(finish_reason="stop"),
    ]
    mock_openai_instance.chat.completions.create.return_value = iter(chunks)

    session = MagicMock()
    session.messages = []
    session.estimate_tokens.return_value = 0

    response = client.chat(session=session, ui=None)
    assert response.thinking == "I am deliberating about this carefully."
    assert response.content == "Final answer."


def test_mlx_client_chat_tool_call_deltas_accumulate_per_index() -> None:
    """Tool call deltas streamed across multiple chunks should assemble correctly."""
    client, mock_openai_instance = _make_mlx_client_with_mock_openai()

    # First call: tool_calls delta streamed in pieces, finish_reason="tool_calls"
    first_chunks = [
        _make_delta_chunk(tool_calls=[SimpleNamespace(
            index=0,
            id="call-1",
            function=SimpleNamespace(name="read_file", arguments=""),
        )]),
        _make_delta_chunk(tool_calls=[SimpleNamespace(
            index=0,
            id=None,
            function=SimpleNamespace(name=None, arguments='{"path":'),
        )]),
        _make_delta_chunk(tool_calls=[SimpleNamespace(
            index=0,
            id=None,
            function=SimpleNamespace(name=None, arguments='"foo.md"}'),
        )]),
        _make_delta_chunk(finish_reason="tool_calls"),
    ]
    # Second call: after tool result lands, model returns final text
    second_chunks = [
        _make_delta_chunk(content="I read the file."),
        _make_delta_chunk(finish_reason="stop"),
    ]
    mock_openai_instance.chat.completions.create.side_effect = [
        iter(first_chunks),
        iter(second_chunks),
    ]

    session = MagicMock()
    session.messages = []
    session.estimate_tokens.return_value = 0

    client.tools.dispatch.return_value = "file contents here"
    client.tools.schemas.return_value = [{"type": "function", "function": {"name": "read_file"}}]

    response = client.chat(session=session, ui=None)
    assert response.content == "I read the file."
    # The tool was invoked with the accumulated args dict
    assert len(response.tool_invocations) == 1
    name, args, result = response.tool_invocations[0]
    assert name == "read_file"
    assert args == {"path": "foo.md"}
    assert result == "file contents here"


def test_mlx_client_chat_streams_to_ui_when_present() -> None:
    """UI receives stream_open / stream_delta / stream_close calls."""
    client, mock_openai_instance = _make_mlx_client_with_mock_openai()
    chunks = [
        _make_delta_chunk(content="ab"),
        _make_delta_chunk(content="cd"),
        _make_delta_chunk(finish_reason="stop"),
    ]
    mock_openai_instance.chat.completions.create.return_value = iter(chunks)
    ui = MagicMock()
    session = MagicMock()
    session.messages = []
    session.estimate_tokens.return_value = 0

    client.chat(session=session, ui=ui)
    ui.stream_open.assert_called_once()
    assert ui.stream_delta.call_count == 2
    ui.stream_close.assert_called_once()


def test_mlx_client_messages_for_openai_handles_assistant_with_tool_calls() -> None:
    """When assistant message has tool_calls, content must be None per OpenAI spec."""
    client, _ = _make_mlx_client_with_mock_openai()
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "x", "function": {"name": "t", "arguments": "{}"}}]},
        {"role": "tool", "content": "result", "tool_call_id": "x", "name": "t"},
    ]
    out = client._messages_for_openai(messages)
    assert out[0] == {"role": "user", "content": "hello"}
    assert out[1]["content"] is None
    assert out[1]["tool_calls"][0]["id"] == "x"
    assert out[2]["tool_call_id"] == "x"
    assert out[2]["name"] == "t"


def test_mlx_client_serializes_dict_tool_call_arguments_to_json_string() -> None:
    """Cross-backend resume bug fix: dict-shaped tool_call arguments must
    be JSON-serialized before sending to mlx_lm.server.

    The OpenAI spec requires function.arguments to be a JSON-encoded string.
    Sessions resumed from the Ollama backend may have stored arguments as
    dicts (Ollama's native format). Without serialization, mlx_lm.server
    returns 404 with "the JSON object must be str, bytes or bytearray, not
    dict" — surfaced as Aletheia's 2026-05-17 cross-backend-resume bug.
    """
    client, _ = _make_mlx_client_with_mock_openai()
    messages = [
        {"role": "user", "content": "search the files"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_abc",
                "type": "function",
                "function": {
                    "name": "list_files",
                    "arguments": {"scope": "memory"},  # DICT, not string — the bug shape
                },
            }],
        },
    ]
    out = client._messages_for_openai(messages)
    assistant_msg = out[1]
    assert assistant_msg["tool_calls"][0]["function"]["name"] == "list_files"
    args = assistant_msg["tool_calls"][0]["function"]["arguments"]
    assert isinstance(args, str), f"arguments must be a string, got {type(args).__name__}"
    # And the JSON-encoded string should round-trip back to the original dict
    assert json.loads(args) == {"scope": "memory"}


def test_mlx_client_passes_string_tool_call_arguments_through_unchanged() -> None:
    """When arguments are already JSON strings (native mlx-lm format), pass them through."""
    client, _ = _make_mlx_client_with_mock_openai()
    messages = [{
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": "call_xyz",
            "type": "function",
            "function": {
                "name": "get_weather",
                "arguments": '{"city": "Paris"}',  # already a JSON string
            },
        }],
    }]
    out = client._messages_for_openai(messages)
    args = out[0]["tool_calls"][0]["function"]["arguments"]
    assert args == '{"city": "Paris"}'


def test_mlx_client_handles_none_arguments_safely() -> None:
    """None arguments should be coerced to empty-dict JSON rather than crash."""
    client, _ = _make_mlx_client_with_mock_openai()
    messages = [{
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": "call_x",
            "function": {"name": "foo", "arguments": None},
        }],
    }]
    out = client._messages_for_openai(messages)
    args = out[0]["tool_calls"][0]["function"]["arguments"]
    assert args == "{}"


def test_mlx_client_normalize_tool_call_fills_missing_fields() -> None:
    """Missing id / type fields get sensible defaults so OpenAI SDK doesn't reject the call."""
    from partner_client.client import MLXClient
    tc = {"function": {"name": "x", "arguments": {"a": 1}}}
    out = MLXClient._normalize_tool_call_for_openai(tc)
    assert out["id"] == ""  # empty string is valid OpenAI
    assert out["type"] == "function"
    assert out["function"]["name"] == "x"
    assert json.loads(out["function"]["arguments"]) == {"a": 1}


# ---------- Doctor mlx-lm checks ----------


def test_doctor_ollama_check_skipped_when_backend_is_mlx_lm() -> None:
    from partner_client.doctor import _check_ollama_reachable
    config = MagicMock()
    config.model = ModelConfig(backend="mlx-lm")
    assert _check_ollama_reachable(config) is None


def test_doctor_mlx_installed_check_skipped_when_backend_is_ollama() -> None:
    from partner_client.doctor import _check_mlx_lm_installed
    config = MagicMock()
    config.model = ModelConfig(backend="ollama")
    assert _check_mlx_lm_installed(config) is None


def test_doctor_mlx_installed_check_passes_when_packages_present() -> None:
    """When mlx_lm + openai both importable, status is OK."""
    from partner_client.doctor import _check_mlx_lm_installed, OK
    config = MagicMock()
    config.model = ModelConfig(backend="mlx-lm")
    # Both modules are importable in this env (we installed openai for client.py)
    with patch.dict("sys.modules", {"mlx_lm": MagicMock(), "openai": MagicMock()}):
        result = _check_mlx_lm_installed(config)
    assert result is not None
    assert result.status == OK


def test_doctor_mlx_installed_check_fails_when_mlx_lm_missing() -> None:
    """When mlx_lm import fails, status is FAIL with a hint."""
    from partner_client.doctor import _check_mlx_lm_installed, FAIL
    config = MagicMock()
    config.model = ModelConfig(backend="mlx-lm")

    # Force mlx_lm import to fail
    real_import = __import__

    def fail_mlx_import(name, *args, **kwargs):
        if name == "mlx_lm":
            raise ImportError("no module")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fail_mlx_import):
        result = _check_mlx_lm_installed(config)
    assert result is not None
    assert result.status == FAIL
    assert "pip install mlx-lm" in (result.hint or "")


def test_doctor_mlx_server_reachable_warns_when_unreachable_and_autostart_enabled(tmp_path) -> None:
    from partner_client.doctor import _check_mlx_server_reachable, WARN
    config = MagicMock()
    config.model = ModelConfig(backend="mlx-lm", mlx_auto_start_server=True)

    mock_openai = MagicMock()
    mock_openai.OpenAI.return_value.models.list.side_effect = ConnectionError("no")
    with patch.dict("sys.modules", {"openai": mock_openai}):
        result = _check_mlx_server_reachable(config)
    assert result is not None
    assert result.status == WARN
    assert "auto-launch" in (result.hint or "")


def test_doctor_mlx_server_reachable_fails_when_unreachable_and_autostart_disabled() -> None:
    from partner_client.doctor import _check_mlx_server_reachable, FAIL
    config = MagicMock()
    config.model = ModelConfig(backend="mlx-lm", mlx_auto_start_server=False)

    mock_openai = MagicMock()
    mock_openai.OpenAI.return_value.models.list.side_effect = ConnectionError("no")
    with patch.dict("sys.modules", {"openai": mock_openai}):
        result = _check_mlx_server_reachable(config)
    assert result is not None
    assert result.status == FAIL


def test_doctor_mlx_model_in_hf_cache_warns_when_not_downloaded(tmp_path, monkeypatch) -> None:
    """Model not in HF cache should produce a WARN with the right download hint."""
    from partner_client.doctor import _check_mlx_model_in_hf_cache, WARN
    config = MagicMock()
    config.model = ModelConfig(
        backend="mlx-lm",
        name="mlx-community/some-nonexistent-model",
    )
    # Point HF cache at a temp dir so we know the model isn't there
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    result = _check_mlx_model_in_hf_cache(config)
    assert result.status == WARN
    assert "hf download" in (result.hint or "")


def test_doctor_mlx_model_warns_when_name_lacks_slash() -> None:
    """Model names for mlx-lm backend should be org/name format."""
    from partner_client.doctor import _check_mlx_model_in_hf_cache, WARN
    config = MagicMock()
    config.model = ModelConfig(backend="mlx-lm", name="just-a-name")
    result = _check_mlx_model_in_hf_cache(config)
    assert result.status == WARN
    assert "HF repo" in (result.message or "") or "HF repo" in (result.hint or "")
