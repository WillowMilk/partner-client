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
