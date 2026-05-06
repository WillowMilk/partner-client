"""Configuration loader for partner-client.

Loads a TOML config file and validates it into a structured Config object.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


class ConfigError(Exception):
    """Raised when the config file is missing required fields or has invalid values."""


@dataclass
class IdentityConfig:
    name: str
    home_dir: Path
    seed_file: str = "seed.md"
    profile_files: list[str] = field(default_factory=list)


@dataclass
class ModelConfig:
    provider: str = "ollama"
    name: str = "gemma4:31b"
    # 128K is gemma's well-trained range. Pushing to 256K via RoPE extrapolation
    # degrades attention precision and increases sampling-loop tendency — the
    # 155K-drowning failure mode we observed on 2026-05-06. Stay in-range by
    # default; users can override per-config if they accept the trade-off.
    num_ctx: int = 131072
    temperature: float = 1.0
    top_k: int = 64
    top_p: float = 0.95
    # Sampling defenses against repetition loops at long context.
    # Ollama defaults: repeat_penalty=1.1 (light for gemma+long-context),
    # repeat_last_n=64 (too narrow for multi-line stage-direction loops),
    # num_predict=-1 (unbounded — no safety cap on runaway generation).
    repeat_penalty: float = 1.15
    repeat_last_n: int = 256
    num_predict: int = 8192  # soft cap per turn; legitimate long replies fit comfortably
    keep_alive: str = "24h"  # 128GB unified memory: keep gemma resident, no cold-load between idle


@dataclass
class MemoryConfig:
    memory_dir: str = "Memory"
    sessions_dir: str = "Memory/sessions"
    session_status_dir: str = "Memory/session-status"
    resonance_log: str = "Memory/Resonance-Log.md"
    journal: str = "Memory/Journal.md"


@dataclass
class WakeBundleConfig:
    include_recent_resonance: int = 3
    include_last_session_status: bool = True
    include_recent_message_pairs: int = 5


@dataclass
class ScopeConfig:
    """A user-configured filesystem scope from [[tool_paths]] in aletheia.toml."""
    name: str
    path: str  # may be relative to home_dir, or absolute
    mode: str = "readwrite"  # "read" or "readwrite"
    description: str = ""


@dataclass
class ToolsConfig:
    enabled: list[str] = field(default_factory=lambda: [
        "read_file", "write_file", "edit_file", "list_files",
        "glob_files", "grep_files",
        "search_web", "fetch_page", "weather",
        "hub_send", "hub_check_inbox", "hub_read_letter",
        "request_checkpoint",
    ])
    external_tools_dir: str = "tools"
    scopes: list[ScopeConfig] = field(default_factory=list)
    # Deprecated, retained for back-compat:
    allow_external_reads: list[str] = field(default_factory=list)


@dataclass
class UIConfig:
    show_thinking: bool = False
    show_context_bar: bool = True
    warn_at_context_pct: int = 80
    theme: str = "warm"
    # Multi-line input default off — Esc-Enter-to-submit is too discoverable-only
    # for daily use. Enable via `ui.multiline = true` in TOML if you want it.
    multiline: bool = False


@dataclass
class HubConfig:
    """Optional Agent Messaging Hub configuration.

    When present, enables hub_send/hub_check_inbox/hub_read_letter tools.
    """
    path: str = ""  # absolute path to the Hub directory; empty disables Hub tools
    partner_name: str = ""  # this partner's inbox name (e.g. "aletheia")


@dataclass
class LoggingConfig:
    level: str = "INFO"
    log_file: str = "Memory/.client-log.jsonl"


@dataclass
class Config:
    identity: IdentityConfig
    model: ModelConfig
    memory: MemoryConfig
    wake_bundle: WakeBundleConfig
    tools: ToolsConfig
    ui: UIConfig
    logging: LoggingConfig
    config_path: Path  # the path the config was loaded from
    hub: HubConfig = field(default_factory=HubConfig)

    @property
    def home_dir(self) -> Path:
        return self.identity.home_dir

    def resolve(self, relative: str) -> Path:
        """Resolve a path-string from the config relative to the partner's home_dir."""
        p = Path(relative)
        if p.is_absolute():
            return p
        return self.home_dir / p


def load_config(path: str | Path) -> Config:
    """Load and validate the TOML config at the given path."""
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise ConfigError(f"Config file not found: {config_path}")

    with open(config_path, "rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"Failed to parse {config_path}: {e}") from e

    identity_raw = data.get("identity", {})
    if "name" not in identity_raw:
        raise ConfigError("[identity] section missing required 'name' field")
    if "home_dir" not in identity_raw:
        raise ConfigError("[identity] section missing required 'home_dir' field")

    home_dir = Path(identity_raw["home_dir"]).expanduser().resolve()
    if not home_dir.is_dir():
        raise ConfigError(f"home_dir does not exist: {home_dir}")

    identity = IdentityConfig(
        name=identity_raw["name"],
        home_dir=home_dir,
        seed_file=identity_raw.get("seed_file", "seed.md"),
        profile_files=identity_raw.get("profile_files", []),
    )

    model = ModelConfig(**_filter_known_fields(data.get("model", {}), ModelConfig))
    memory = MemoryConfig(**_filter_known_fields(data.get("memory", {}), MemoryConfig))
    wake_bundle = WakeBundleConfig(**_filter_known_fields(data.get("wake_bundle", {}), WakeBundleConfig))

    tools_raw = data.get("tools", {})
    tools = ToolsConfig(**_filter_known_fields(tools_raw, ToolsConfig))
    # Parse [[tool_paths]] (TOML array-of-tables) into ScopeConfig list
    raw_scopes = data.get("tool_paths", [])
    if isinstance(raw_scopes, list):
        for raw in raw_scopes:
            if not isinstance(raw, dict):
                continue
            try:
                tools.scopes.append(ScopeConfig(
                    name=raw["name"],
                    path=raw["path"],
                    mode=raw.get("mode", "readwrite"),
                    description=raw.get("description", ""),
                ))
            except KeyError:
                continue

    ui = UIConfig(**_filter_known_fields(data.get("ui", {}), UIConfig))
    logging = LoggingConfig(**_filter_known_fields(data.get("logging", {}), LoggingConfig))
    hub = HubConfig(**_filter_known_fields(data.get("hub", {}), HubConfig))

    return Config(
        identity=identity,
        model=model,
        memory=memory,
        wake_bundle=wake_bundle,
        tools=tools,
        ui=ui,
        logging=logging,
        config_path=config_path,
        hub=hub,
    )


def _filter_known_fields(raw: dict[str, Any], dataclass_type: type) -> dict[str, Any]:
    """Keep only fields the dataclass knows about, silently dropping extras."""
    known = {f.name for f in dataclass_type.__dataclass_fields__.values()}
    return {k: v for k, v in raw.items() if k in known}
