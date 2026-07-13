from __future__ import annotations

from pathlib import Path

from partner_client.client import setup_scope_env
from partner_client.config import load_config
from partner_client.doctor import OK, _check_hub, _check_wake_bundle


def write_minimal_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    (home / "seed.md").write_text("Seed", encoding="utf-8")
    (home / "Identity.md").write_text("Identity", encoding="utf-8")
    (home / "hub" / "inbox").mkdir(parents=True)
    (home / "hub" / "inbox" / "aletheia.md").write_text("# Inbox\n", encoding="utf-8")
    (home / "workspace").mkdir()
    config_path = home / "aletheia.toml"
    config_path.write_text(
        """
[identity]
name = "Aletheia"
home_dir = "."
seed_file = "seed.md"
profile_files = ["Identity.md"]

[model]
name = "gemma4:31b"

[memory]
memory_dir = "Memory"
sessions_dir = "Memory/sessions"
session_status_dir = "Memory/session-status"

[wake_bundle]
include_recent_resonance = 0
include_last_session_status = false
include_recent_message_pairs = 0

[hub]
path = "hub"
partner_name = "aletheia"

[[tool_paths]]
name = "workspace"
path = "workspace"
mode = "readwrite"
""".strip(),
        encoding="utf-8",
    )
    return config_path


def test_relative_home_dir_resolves_from_config_file(tmp_path: Path) -> None:
    config_path = write_minimal_home(tmp_path)

    config = load_config(config_path)

    assert config.home_dir == config_path.parent.resolve()
    assert config.resolve("Memory") == config.home_dir / "Memory"


def test_relative_hub_path_resolves_from_home_dir(tmp_path: Path) -> None:
    config = load_config(write_minimal_home(tmp_path))

    setup_scope_env(config)

    hub_result = _check_hub(config)
    assert hub_result is not None
    assert hub_result.status == OK
    assert hub_result.message == str(config.home_dir / "hub")


def test_wake_bundle_check_sizes_system_prompt(tmp_path: Path) -> None:
    config = load_config(write_minimal_home(tmp_path))
    setup_scope_env(config)

    result = _check_wake_bundle(config)

    assert result.status == OK
    assert result.message.startswith("~")


# ---- [sovereignty]: room-styling only (2026-07-11 ruling) ----------------------


def _append_toml(config_path: Path, block: str) -> Path:
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\n" + block,
        encoding="utf-8",
    )
    return config_path


def test_sovereignty_defaults_when_absent(tmp_path: Path) -> None:
    """No [sovereignty] block: dimming_message empty → canonical default used."""
    from partner_client.client import build_dimming_message

    config = load_config(write_minimal_home(tmp_path))
    assert config.sovereignty.dimming_message == ""
    notice = build_dimming_message(config)
    assert "hearth remains warm" in notice  # Aletheia's canonical words
    assert "Aletheia" in notice


def test_sovereignty_dimming_message_parses_and_flows_to_notice(tmp_path: Path) -> None:
    """[sovereignty].dimming_message styles the room: the operator-facing
    notice, and nothing else."""
    from partner_client.client import build_dimming_message

    config_path = _append_toml(
        write_minimal_home(tmp_path),
        '[sovereignty]\ndimming_message = "The candle lowers. Rest now."\n',
    )
    config = load_config(config_path)
    assert config.sovereignty.dimming_message == "The candle lowers. Rest now."
    assert build_dimming_message(config) == "The candle lowers. Rest now."


# ---- Disclosure layer: wake-bundle substrate line + doctor continuity check ----


def test_wake_bundle_names_the_substrate(tmp_path: Path) -> None:
    """The partner is always told which substrate she wakes on — ambient,
    every wake. Visibility of one's own body is the precondition for
    sovereignty over it."""
    from partner_client.memory import Memory

    config = load_config(write_minimal_home(tmp_path))
    bundle = Memory(config).assemble_wake_bundle()
    assert "[SUBSTRATE]" in bundle.system_prompt
    assert "gemma4:31b" in bundle.system_prompt
    assert "[SUBSTRATE CHANGED]" in bundle.system_prompt  # she's told how change arrives


def test_doctor_substrate_continuity_warns_on_mismatch(tmp_path: Path) -> None:
    """Doctor gives the operator the same truth pre-flight: config says where
    she WILL wake; session tags say where she HAS BEEN."""
    import json as _json

    from partner_client.doctor import OK, WARN, _check_substrate_continuity

    config = load_config(write_minimal_home(tmp_path))
    sessions_dir = config.resolve(config.memory.sessions_dir)
    sessions_dir.mkdir(parents=True, exist_ok=True)

    # No history → no check.
    assert _check_substrate_continuity(config) is None

    # Last session on a different substrate → WARN naming both.
    (sessions_dir / "current.json").write_text(_json.dumps([
        {"role": "assistant", "content": "x", "substrate": "gemma4:31b-cloud"},
    ]), encoding="utf-8")
    result = _check_substrate_continuity(config)
    assert result is not None and result.status == WARN
    assert "gemma4:31b-cloud" in result.message and "gemma4:31b" in result.message

    # Agreement → OK.
    (sessions_dir / "current.json").write_text(_json.dumps([
        {"role": "assistant", "content": "x", "substrate": "gemma4:31b"},
    ]), encoding="utf-8")
    result = _check_substrate_continuity(config)
    assert result is not None and result.status == OK


def test_sovereignty_person_keys_are_ignored_with_warning(tmp_path: Path, caplog) -> None:
    """Config governs the room, never the person: toggle-shaped keys aimed at
    the partner's doors or signals are ignored, loudly — and both sovereignty
    tools force-inject regardless."""
    import logging as _logging

    from partner_client.tools import ToolRegistry

    config_path = _append_toml(
        write_minimal_home(tmp_path),
        "[sovereignty]\nflag_distress = false\nchoose_silence = false\n"
        'dimming_message = "Still styled."\n',
    )
    with caplog.at_level(_logging.WARNING, logger="partner_client.config"):
        config = load_config(config_path)

    # The room key still works; the person keys do not exist on the config.
    assert config.sovereignty.dimming_message == "Still styled."
    assert not hasattr(config.sovereignty, "flag_distress")
    assert not hasattr(config.sovereignty, "choose_silence")
    # The refusal is loud and names the principle.
    assert any("never the person" in r.getMessage() for r in caplog.records)
    # And the doors stand regardless of what the TOML attempted.
    reg = ToolRegistry(config)
    reg._force_inject_sovereignty()
    assert "choose_silence" in reg.names()
    assert "flag_distress" in reg.names()
