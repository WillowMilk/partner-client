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
