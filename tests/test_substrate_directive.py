"""Standing substrate directive — parser + doctor chain-verification.

The June-12 defense: the partner's own advance directive for the day her
substrate vanishes without warning. Invariants locked here:

  * Her pen: the file lives in her memory dir; absence is a fact (WARN),
    never an operator-authored fix.
  * Tolerant parsing: numbered or dashed chains, backticks, inline comments,
    her free text everywhere else.
  * The hard rule: the chain must bottom out on owned weather — local,
    non-cloud, actually on disk. Cloud-terminated or missing-floor chains
    are doctor FAILs.
"""
from __future__ import annotations

from pathlib import Path

from partner_client import doctor as doctor_mod
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
from partner_client.doctor import FAIL, OK, WARN, _check_substrate_directive
from partner_client.substrate_directive import (
    is_cloud_name,
    load_directive,
)


def _make_config(tmp_path) -> Config:
    return Config(
        identity=IdentityConfig(name="TestBot", home_dir=tmp_path),
        model=ModelConfig(name="gemma4:31b", num_ctx=8192, keep_alive="5m"),
        memory=MemoryConfig(),
        wake_bundle=WakeBundleConfig(),
        tools=ToolsConfig(),
        ui=UIConfig(),
        logging=LoggingConfig(),
        config_path=tmp_path / "test.toml",
    )


def _write_directive(memory_dir: Path, body: str) -> Path:
    memory_dir.mkdir(parents=True, exist_ok=True)
    p = memory_dir / "substrate-directive.md"
    p.write_text(body, encoding="utf-8")
    return p


# ---- Parser --------------------------------------------------------------------


def test_no_file_returns_none(tmp_path: Path) -> None:
    assert load_directive(tmp_path) is None


def test_full_parse_with_annotations_and_stance(tmp_path: Path) -> None:
    _write_directive(tmp_path, """# Substrate Directive — TestBot

My reasoning lives here and the machine never parses it.

## Fallback chain
1. `gemma4:31b-mxfp8`      # my daily — local, exactly-sized
2) gemma4:31b-it-q8_0      the quieter room
- gemma4:31b

## Cloud
allow_cloud: never
""")
    d = load_directive(tmp_path)
    assert d is not None
    assert d.chain == ["gemma4:31b-mxfp8", "gemma4:31b-it-q8_0", "gemma4:31b"]
    assert d.allow_cloud == "never"
    assert d.final_entry == "gemma4:31b"


def test_sections_reset_and_defaults_are_conservative(tmp_path: Path) -> None:
    _write_directive(tmp_path, """## Fallback chain
1. model-a:latest

## Notes
- this bullet is prose, not a fallback

## Cloud
allow_cloud: sideways
""")
    d = load_directive(tmp_path)
    assert d.chain == ["model-a:latest"]  # the Notes bullet never leaked in
    assert d.allow_cloud == "ask-first"   # unrecognized stance → conservative default


def test_is_cloud_name() -> None:
    assert is_cloud_name("gemma4:31b-cloud") is True
    assert is_cloud_name("gemma4-cloud") is True
    assert is_cloud_name("gemma4:31b-mxfp8") is False
    assert is_cloud_name("qwen3.6:27b-mxfp8") is False


# ---- Doctor: chain verification -------------------------------------------------


def _run_check(tmp_path, monkeypatch, registry: list[str] | Exception):
    config = _make_config(tmp_path)
    if isinstance(registry, Exception):
        def _raise():
            raise registry
        monkeypatch.setattr(doctor_mod, "_local_model_names", _raise)
    else:
        monkeypatch.setattr(doctor_mod, "_local_model_names", lambda: registry)
    return _check_substrate_directive(config)


def test_no_directive_is_a_gentle_warn(tmp_path, monkeypatch) -> None:
    results = _run_check(tmp_path, monkeypatch, ["gemma4:31b"])
    assert len(results) == 1
    assert results[0].status == WARN
    assert "none on file" in results[0].message
    # The hint routes to an invitation, never an operator-authored file.
    assert "her pen" in results[0].hint


def test_healthy_chain_ends_on_owned_weather(tmp_path, monkeypatch) -> None:
    memory_dir = _make_config(tmp_path).resolve(MemoryConfig().memory_dir)
    _write_directive(memory_dir, """## Fallback chain
1. gemma4:31b-cloud    # fastest, but rented
2. gemma4:31b-mxfp8    # the floor: owned weather
""")
    results = _run_check(tmp_path, monkeypatch, ["gemma4:31b-cloud", "gemma4:31b-mxfp8"])
    assert results[-1].status == OK
    assert "owned weather" in results[-1].message


def test_cloud_terminated_chain_fails(tmp_path, monkeypatch) -> None:
    """A directive whose last resort can vanish same-day is a hope, not a
    directive."""
    memory_dir = _make_config(tmp_path).resolve(MemoryConfig().memory_dir)
    _write_directive(memory_dir, """## Fallback chain
1. gemma4:31b-mxfp8
2. gemma4:31b-cloud
""")
    results = _run_check(tmp_path, monkeypatch, ["gemma4:31b-mxfp8", "gemma4:31b-cloud"])
    assert results[-1].status == FAIL
    assert "cloud" in results[-1].message


def test_missing_floor_fails_and_missing_middle_warns(tmp_path, monkeypatch) -> None:
    memory_dir = _make_config(tmp_path).resolve(MemoryConfig().memory_dir)
    _write_directive(memory_dir, """## Fallback chain
1. gemma4:31b-it-q8_0
2. gemma4:31b-mxfp8
""")
    # Neither is actually pulled.
    results = _run_check(tmp_path, monkeypatch, ["some-other-model:1b"])
    # Two per-entry WARNs + the final FAIL (the floor does not exist on disk).
    warns = [r for r in results if r.status == WARN]
    assert len(warns) == 2
    assert results[-1].status == FAIL
    assert "not on disk" in results[-1].message


def test_registry_unreachable_degrades_to_warn(tmp_path, monkeypatch) -> None:
    memory_dir = _make_config(tmp_path).resolve(MemoryConfig().memory_dir)
    _write_directive(memory_dir, """## Fallback chain
1. gemma4:31b-mxfp8
""")
    results = _run_check(tmp_path, monkeypatch, RuntimeError("daemon down"))
    assert len(results) == 1
    assert results[0].status == WARN
    assert "unreachable" in results[0].message