"""Tests for cold-resume pre-warm + heavy-resume warning banner.

Covers:
  * OllamaClient.prewarm() success path with elapsed timing
  * Graceful error handling — prewarm failures never crash startup
  * Timeline event recording for both success and failure paths
  * Pre-warm call shape: minimal predict budget, correct model + ctx
  * _estimate_resume_wait threshold table across session-size bands
"""
from __future__ import annotations

from unittest.mock import MagicMock

from partner_client.__main__ import _estimate_resume_wait
from partner_client.client import OllamaClient
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
from partner_client.tools import ToolRegistry


def _make_config(tmp_path) -> Config:
    return Config(
        identity=IdentityConfig(name="TestBot", home_dir=tmp_path),
        model=ModelConfig(name="gemma4:31b", num_ctx=8192, keep_alive="5m"),
        memory=MemoryConfig(),
        wake_bundle=WakeBundleConfig(),
        tools=ToolsConfig(),
        ui=UIConfig(),
        logging=LoggingConfig(),
        config_path=tmp_path / "aletheia.toml",
    )


def _build_client(tmp_path) -> OllamaClient:
    config = _make_config(tmp_path)
    tools = ToolRegistry(config)
    return OllamaClient(config, tools)


# ---- prewarm: success path ----------------------------------------------------


def test_prewarm_returns_ok_and_positive_elapsed_on_success(tmp_path) -> None:
    client = _build_client(tmp_path)
    fake = MagicMock(return_value={"message": {"content": "x"}})
    client._ollama = MagicMock()
    client._ollama.chat = fake

    ok, elapsed, err = client.prewarm()

    assert ok is True
    assert err is None
    assert elapsed >= 0  # may be 0.0 on very fast mocks; never negative
    fake.assert_called_once()


def test_prewarm_call_uses_minimal_predict_budget(tmp_path) -> None:
    """Pre-warm must not generate real content — num_predict=1 is the contract."""
    client = _build_client(tmp_path)
    fake = MagicMock(return_value={"message": {"content": "x"}})
    client._ollama = MagicMock()
    client._ollama.chat = fake

    client.prewarm()

    kwargs = fake.call_args.kwargs
    assert kwargs["options"]["num_predict"] == 1
    assert kwargs["stream"] is False  # non-streaming is faster for a 1-token call
    assert kwargs["model"] == "gemma4:31b"
    # Honors configured num_ctx so memory allocation matches real chat shape
    assert kwargs["options"]["num_ctx"] == 8192
    # Honors keep_alive so the pre-loaded model stays resident
    assert kwargs["keep_alive"] == "5m"


def test_prewarm_records_timeline_event_on_success(tmp_path) -> None:
    config = _make_config(tmp_path)
    tools = ToolRegistry(config)
    timeline = MagicMock()
    client = OllamaClient(config, tools, timeline=timeline)
    client._ollama = MagicMock()
    client._ollama.chat = MagicMock(return_value={"message": {"content": "x"}})

    client.prewarm()

    # The success event should fire with a duration_ms field
    success_calls = [
        call for call in timeline.record.call_args_list
        if call.args and call.args[0] == "prewarm_complete"
    ]
    assert len(success_calls) == 1
    assert "duration_ms" in success_calls[0].kwargs


# ---- prewarm: failure path ----------------------------------------------------


def test_prewarm_returns_error_on_ollama_exception_not_raise(tmp_path) -> None:
    """Pre-warm must never raise — startup continues regardless."""
    client = _build_client(tmp_path)
    client._ollama = MagicMock()
    client._ollama.chat = MagicMock(side_effect=ConnectionError("daemon offline"))

    ok, elapsed, err = client.prewarm()

    assert ok is False
    assert err is not None
    assert "daemon offline" in err
    assert elapsed >= 0


def test_prewarm_records_timeline_error_event_on_failure(tmp_path) -> None:
    config = _make_config(tmp_path)
    tools = ToolRegistry(config)
    timeline = MagicMock()
    client = OllamaClient(config, tools, timeline=timeline)
    client._ollama = MagicMock()
    client._ollama.chat = MagicMock(side_effect=RuntimeError("model not pulled"))

    client.prewarm()

    error_calls = [
        call for call in timeline.record.call_args_list
        if call.args and call.args[0] == "prewarm_error"
    ]
    assert len(error_calls) == 1
    assert "model not pulled" in error_calls[0].kwargs["error"]


def test_prewarm_returns_elapsed_even_on_failure(tmp_path) -> None:
    """A failed prewarm still reports elapsed time so the operator sees the cost."""
    client = _build_client(tmp_path)
    client._ollama = MagicMock()
    client._ollama.chat = MagicMock(side_effect=Exception("anything"))

    _, elapsed, _ = client.prewarm()

    assert elapsed >= 0


# ---- prewarm: doesn't pass tools (would defeat the warm-up purpose) -----------


def test_prewarm_does_not_pass_tools_schemas(tmp_path) -> None:
    """The minimal call shouldn't send tool schemas — keeps prefill fast.

    Tools schemas are sent on every real chat call (necessary for tool use);
    pre-warm doesn't need them and skipping them keeps the warm-up minimal.
    """
    client = _build_client(tmp_path)
    fake = MagicMock(return_value={"message": {"content": "x"}})
    client._ollama = MagicMock()
    client._ollama.chat = fake

    client.prewarm()

    kwargs = fake.call_args.kwargs
    # tools may be missing entirely OR present-but-None/empty; what matters
    # is we're not handing over the full tool schema set on prewarm
    assert kwargs.get("tools") is None or kwargs.get("tools") == [] or "tools" not in kwargs


# ---- _estimate_resume_wait ----------------------------------------------------


def test_estimate_resume_wait_returns_short_band_for_small_sessions() -> None:
    """Sessions under 100 KB should land in the fastest band."""
    assert "30s" in _estimate_resume_wait(0)
    assert "30s" in _estimate_resume_wait(50)
    assert "30s" in _estimate_resume_wait(99)


def test_estimate_resume_wait_returns_mid_band_for_medium_sessions() -> None:
    """Sessions 100-300 KB should land in the ~1-3 min band."""
    assert "1-3 min" in _estimate_resume_wait(100)
    assert "1-3 min" in _estimate_resume_wait(200)
    assert "1-3 min" in _estimate_resume_wait(299)


def test_estimate_resume_wait_returns_heavier_band_for_300_to_600() -> None:
    """The 300-600 KB band — calibrated from the 437 KB / 20m diagnosis."""
    assert "3-6 min" in _estimate_resume_wait(300)
    assert "3-6 min" in _estimate_resume_wait(437)  # the diagnostic data point
    assert "3-6 min" in _estimate_resume_wait(599)


def test_estimate_resume_wait_returns_high_band_for_large_sessions() -> None:
    """Sessions over 600 KB are getting genuinely slow."""
    assert "6-12 min" in _estimate_resume_wait(600)
    assert "6-12 min" in _estimate_resume_wait(999)


def test_estimate_resume_wait_returns_open_ended_band_at_extreme_size() -> None:
    """Sessions over 1 MB — the architecture is signaling distill-or-truncate."""
    assert "12+" in _estimate_resume_wait(1000)
    assert "12+" in _estimate_resume_wait(5000)


# ---- Config: new fields default sensibly --------------------------------------


def test_wake_bundle_config_defaults_for_new_fields(tmp_path) -> None:
    """Defaults should be sensible without requiring TOML changes."""
    config = _make_config(tmp_path)
    assert config.wake_bundle.prewarm_on_startup is True
    assert config.wake_bundle.heavy_resume_warn_kb == 300


def test_wake_bundle_config_accepts_disabled_prewarm() -> None:
    """Operators can disable pre-warm via TOML."""
    wb = WakeBundleConfig(prewarm_on_startup=False, heavy_resume_warn_kb=500)
    assert wb.prewarm_on_startup is False
    assert wb.heavy_resume_warn_kb == 500
