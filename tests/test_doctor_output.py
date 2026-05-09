from __future__ import annotations

import io

from partner_client import doctor
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


class Cp1252Stream(io.StringIO):
    @property
    def encoding(self) -> str:
        return "cp1252"


def dummy_config(tmp_path) -> Config:
    return Config(
        identity=IdentityConfig(name="TestBot", home_dir=tmp_path),
        model=ModelConfig(name="gemma4:31b", num_ctx=8192),
        memory=MemoryConfig(),
        wake_bundle=WakeBundleConfig(),
        tools=ToolsConfig(),
        ui=UIConfig(),
        logging=LoggingConfig(),
        config_path=tmp_path / "aletheia.toml",
    )


def test_doctor_uses_ascii_status_on_cp1252_stream(tmp_path, monkeypatch) -> None:
    def ok_check(_config):
        return doctor.CheckResult("Config parses", doctor.OK)

    def warn_check(_config):
        return doctor.CheckResult("Model available", doctor.WARN, "not pulled")

    monkeypatch.setattr(doctor, "_ALL_CHECKS", [ok_check, warn_check])
    stream = Cp1252Stream()

    exit_code = doctor.run_doctor(dummy_config(tmp_path), stream=stream)

    output = stream.getvalue()
    assert exit_code == 0
    assert "OK Config parses" in output
    assert "WARN Model available" in output
    assert doctor.OK not in output
    assert doctor.WARN not in output


def test_doctor_returns_nonzero_on_failures(tmp_path, monkeypatch) -> None:
    def fail_check(_config):
        return doctor.CheckResult("Memory directory exists", doctor.FAIL, "missing", "create it")

    monkeypatch.setattr(doctor, "_ALL_CHECKS", [fail_check])
    stream = io.StringIO()

    exit_code = doctor.run_doctor(dummy_config(tmp_path), stream=stream)

    assert exit_code == 1
    assert "One or more checks failed" in stream.getvalue()


# --- _check_model_available shape compatibility ---------------------------

def _install_fake_ollama_listing(monkeypatch, models) -> None:
    """Replace ollama.Client with a stub that returns the given listing.

    `models` is whatever shape we want to simulate — Pydantic-like objects,
    plain dicts, or a mix. The stub Client's list() returns an object with
    a `.models` attribute containing them, mirroring the SDK's ListResponse.
    """
    import ollama

    class _FakeListing:
        def __init__(self, items):
            self.models = items

    class _FakeClient:
        def list(self):
            return _FakeListing(list(models))

    monkeypatch.setattr(ollama, "Client", _FakeClient)


class _PydanticLikeModel:
    """Mimic ollama.ListResponse.Model — exposes `model`, no `name`."""

    def __init__(self, model: str) -> None:
        self.model = model


def test_check_model_reads_model_field_on_modern_sdk(tmp_path, monkeypatch) -> None:
    """Regression for the doctor WARN-on-installed-model bug (2026-05-08).

    ollama-python >= 0.4 exposes each entry as a Pydantic Model with `model`
    instead of `name`. The check must read `model` so an installed model
    isn't reported as missing on a substrate where it's plainly available.
    """
    config = dummy_config(tmp_path)
    config.model.name = "gemma4:31b"
    _install_fake_ollama_listing(
        monkeypatch,
        [
            _PydanticLikeModel("gemma4:31b"),
            _PydanticLikeModel("llama3.2:latest"),
        ],
    )

    result = doctor._check_model_available(config)

    assert result.status == doctor.OK
    assert "gemma4:31b" in result.name


def test_check_model_falls_back_to_name_on_legacy_dict_shape(
    tmp_path, monkeypatch
) -> None:
    """Backward-compat: legacy ollama-python <0.4 returned dicts with `name`."""
    config = dummy_config(tmp_path)
    config.model.name = "gemma4:31b"
    _install_fake_ollama_listing(
        monkeypatch,
        [
            {"name": "gemma4:31b"},
            {"name": "llama3.2:latest"},
        ],
    )

    result = doctor._check_model_available(config)

    assert result.status == doctor.OK


def test_check_model_warns_when_genuinely_missing(tmp_path, monkeypatch) -> None:
    """Sanity: the WARN path still fires for actually-not-installed models."""
    config = dummy_config(tmp_path)
    config.model.name = "gemma4:31b"
    _install_fake_ollama_listing(
        monkeypatch,
        [_PydanticLikeModel("llama3.2:latest")],
    )

    result = doctor._check_model_available(config)

    assert result.status == doctor.WARN
    assert "not in local registry" in result.message
