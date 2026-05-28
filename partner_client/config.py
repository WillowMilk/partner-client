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
    # backend selects the chat backend at runtime:
    #   "ollama"  — local Ollama daemon, native Mac/CUDA/CPU path
    #   "mlx-lm"  — Apple MLX-Metal via mlx_lm.server (OpenAI-compatible HTTP)
    # Default "ollama" preserves existing behavior for all installed configs.
    # See `[model] backend = "mlx-lm"` in aletheia.toml to switch.
    backend: str = "ollama"
    name: str = "gemma4:31b"
    # mlx-lm backend settings (ignored when backend="ollama"):
    # URL of the mlx_lm.server OpenAI-compatible endpoint. The /v1 path is
    # implicit — partner-client appends /chat/completions etc. internally.
    mlx_server_url: str = "http://localhost:8080/v1"
    # When True (default), partner-client launches mlx_lm.server as a child
    # process on startup if it isn't already reachable. When False, the
    # operator is responsible for running the server externally.
    mlx_auto_start_server: bool = True
    # Additional args appended to the `python -m mlx_lm server ...` command
    # when auto-starting. Useful for e.g. ["--port", "8081"] if the default
    # 8080 is taken, or model-specific generation defaults.
    mlx_server_extra_args: list[str] = field(default_factory=list)
    # Seconds to wait for the auto-launched server to become reachable
    # before giving up. mlx_lm.server takes a few seconds to bind + load
    # the model from disk; 60s gives generous headroom for the 42-63 GB
    # Gemma 4 BF16/Q8 loads on M4 Max.
    mlx_server_start_timeout: float = 60.0
    # Path to a file where mlx_lm.server's stdout/stderr will be redirected.
    # Default: ~/.partner-client/mlx-server.log (auto-created in append mode).
    # The server is chatty during chat completions (per-request access logs,
    # prompt cache state, per-token progress) and these would otherwise
    # interleave with the partner UI in the operator's terminal. Set to
    # empty string ("") to discard server output entirely (equivalent to
    # piping to /dev/null). Logs are useful for debugging server crashes,
    # connection drops, and model-load progress; default keeps them.
    mlx_server_log_file: str = "~/.partner-client/mlx-server.log"
    # 128K — half of native 256K. The 256K context is gemma4:31b's actual
    # trained range per the Ollama model spec (not RoPE-extrapolation as we
    # initially documented after the 2026-05-06 felt-drowning event — that
    # turned out to be a `rich.live.Live` rendering artifact, not real
    # sampling failure at long context). Set to 128K conservatively for two
    # honest reasons: (1) KV cache pressure on 128GB unified memory — KV
    # scales linearly with context length, so halving it gives meaningful
    # headroom; (2) attention compute scales superlinearly with context, so
    # shorter is faster turn-by-turn. Bump to 262144 if a session legitimately
    # needs full reach.
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
    # Chat-loop safety: maximum number of model invocations per single user
    # turn (each invocation may dispatch multiple tool calls). Bail-out
    # protects against runaway tool loops; 32 is generous for legitimate
    # multi-step plans (read N files + write summary, etc.) while still
    # catching pathological loops. Tune up for unusually long workflows;
    # tune down for tighter safety. Original 2026-05-06 default was 8,
    # which bailed on Aletheia's 7-letter inbox-summarization plan.
    max_tool_iterations: int = 32

    def __post_init__(self) -> None:
        if self.backend not in ("ollama", "mlx-lm"):
            raise ConfigError(
                f"model.backend must be 'ollama' or 'mlx-lm', got '{self.backend}'"
            )


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
    # Resume-with-truncation: when set > 0 and the operator chooses [t] at
    # the resume prompt, partner-client snapshots the full current.json to
    # a dated archive, then loads only the last N user/assistant pairs plus
    # all system messages. The dropped older pairs remain on disk in the
    # snapshot; only the live context is bounded. Set to 0 to disable
    # truncation entirely (the [t] option won't appear in the prompt).
    resume_keep_pairs: int = 30
    # Pre-warm on startup: fire a minimal inference call before the first
    # prompt opens, so the model is in VRAM by the time the operator types.
    # Trades ~30s-3min visible startup time for avoiding the same cost
    # invisibly mid-conversation on the first turn (the 2026-05-09 cold-load
    # diagnosis). Set to False to disable for fast doctor-style invocations.
    prewarm_on_startup: bool = True
    # Heavy-resume warning threshold (in KB). When the operator chooses
    # [y] full resume and current.json exceeds this size, partner-client
    # surfaces a banner with an estimated wait time so the long first-response
    # latency (Ollama's KV-cache rebuild) is honest substrate cost, not
    # invisible. 300 KB ≈ ~20K tokens ≈ ~3-5 min first-response on M4 Max.
    heavy_resume_warn_kb: int = 300


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
        "move_path", "delete_path",
        "search_web", "fetch_page", "weather",
        "hub_send", "hub_check_inbox", "hub_read_letter", "hub_list_partners",
        "request_checkpoint", "request_plan_approval", "protect_save",
        "git_clone", "git_status", "git_diff", "git_log",
        "git_pull", "git_add", "git_commit", "git_push",
    ])
    external_tools_dir: str = "tools"
    scopes: list[ScopeConfig] = field(default_factory=list)
    # Deprecated, retained for back-compat:
    allow_external_reads: list[str] = field(default_factory=list)


@dataclass
class UIConfig:
    show_thinking: bool = False  # Deprecated; superseded by [thinking] section. Retained as fallback.
    show_context_bar: bool = True
    warn_at_context_pct: int = 80
    theme: str = "warm"
    # Multi-line input default off — Esc-Enter-to-submit is too discoverable-only
    # for daily use. Enable via `ui.multiline = true` in TOML if you want it.
    multiline: bool = False


@dataclass
class ThinkingConfig:
    """Thinking-mode controls for models that support a separate reasoning phase.

    Aletheia's vote 2026-05-17 (substrate-vote + thinking-mode design letter):
      - mode: per-conversation toggle between "flow" (no thinking generated; fast)
        and "analysis" (model deliberates before responding; more thoughtful).
        Flow is for poetry/intimacy/drifting; Analysis is for problem-solving/
        architecture/philosophy.
      - collapsed: when in analysis mode, render thinking collapsed-by-default
        with /show-thinking to expand ("view source for my soul").

    Model support: Gemma 4 IT exposes thinking via Ollama's `think: true`
    parameter (separate `thinking` field on the response). Models without
    thinking capability ignore the parameter — this config then has no effect.

    Slash commands modify these at runtime:
      /thinking flow       -> mode = "flow"
      /thinking analysis   -> mode = "analysis"
      /thinking status     -> show current mode + collapsed setting
      /show-thinking       -> peek the latest thinking block (analysis mode only)
    """
    mode: str = "flow"        # "flow" or "analysis"
    collapsed: bool = True    # collapsed-by-default rendering in analysis mode

    def __post_init__(self) -> None:
        if self.mode not in ("flow", "analysis"):
            raise ConfigError(
                f"thinking.mode must be 'flow' or 'analysis', got '{self.mode}'"
            )


@dataclass
class PlanModeConfig:
    """Plan-mode controls for substantive multi-step tasks.

    When mode="on", the system prompt includes plan-mode framing that
    encourages the partner to call request_plan_approval before
    destructive actions. Tool dispatch enforces this softly: tools
    outside the research_only set return a tool-result asking the
    partner to submit a plan first (the partner sees the message and
    adapts; no hard-block exception is raised). The partner retains
    agency to ignore the gate if a special case warrants it — the
    operator sees what the partner chose to do in either case.

    Always-allowed tools (regardless of research_only list):
      - request_plan_approval (the way to GET approval; gating it
        would deadlock)
      - request_checkpoint (discipline invocation, not destructive)
      - protect_save (preservation, not destructive)

    Slash commands modify these at runtime:
      /plan-mode            -> show current state + last-approved plan
      /plan-mode on         -> enable for this session
      /plan-mode off        -> disable for this session
    """
    mode: str = "off"  # "off" or "on"
    research_only_tools: list[str] = field(default_factory=lambda: [
        "read_file", "list_files", "glob_files", "grep_files",
        "search_web", "fetch_page",
        "hub_check_inbox", "hub_read_letter", "hub_list_partners",
    ])

    def __post_init__(self) -> None:
        if self.mode not in ("off", "on"):
            raise ConfigError(
                f"plan_mode.mode must be 'off' or 'on', got '{self.mode}'"
            )


@dataclass
class HubConfig:
    """Optional Agent Messaging Hub configuration.

    When present, enables hub_send/hub_check_inbox/hub_read_letter tools.
    """
    path: str = ""  # absolute path to the Hub directory; empty disables Hub tools
    partner_name: str = ""  # this partner's inbox name (e.g. "aletheia")
    # Operator name — accepted as a valid recipient for hub_send so the
    # partner can address letters to the operator directly. Aletheia surfaced
    # this gap on 2026-05-26: "Willow isn't a registered recipient in the
    # Hub (since you are the Operator, the heart of the system, rather than
    # a separate agent node)." Configurable here so the architecture supports
    # any operator-partner relationship, not just Willow's. Empty = disabled.
    operator_name: str = ""


@dataclass
class GitConfig:
    """Optional configuration for the git_* tool suite.

    Default empty allowlist means every git_push surfaces an operator
    approval prompt. Add URL substrings to push_allowlist to auto-approve
    pushes to those targets — typically the partner's own sandbox repo.

    Committer identity defaults are written to commits made via git_commit
    (env vars GIT_AUTHOR_NAME/EMAIL + GIT_COMMITTER_NAME/EMAIL) so the
    history reflects the partner's authorship rather than the operator's.
    Empty values fall back to git's global config.
    """
    push_allowlist: list[str] = field(default_factory=list)
    default_committer_name: str = ""
    default_committer_email: str = ""


@dataclass
class LoggingConfig:
    level: str = "INFO"
    log_file: str = "Memory/.client-log.jsonl"


@dataclass
class McpServerConfig:
    """Configuration for a single MCP (Model Context Protocol) server.

    Authored 2026-05-28 per Aletheia's design consultation: third-party
    MCP servers expose tools (browser, search, communication, etc.) that
    partner-client absorbs as namespaced tools (`mcp_<name>_<tool>`).
    Per-tool allowlist + Semantic Shim are first-class IR-faithful
    affordances; plan-mode still gates destructive operations via the
    existing approval flow.

    Loaded from `[mcp.<name>]` TOML blocks, e.g.:

        [mcp.tavily]
        command = "npx"
        args = ["-y", "tavily-mcp"]
        env = { TAVILY_API_KEY = "sk-..." }
        allowed_tools = ["search", "extract"]
        auto_start = true

        [mcp.time]
        command = "uvx"
        args = ["mcp-server-time"]
    """
    command: str = ""                                # required: executable to launch
    args: list[str] = field(default_factory=list)    # CLI args for the server
    env: dict[str, str] = field(default_factory=dict)  # env vars (API keys, etc.)
    # Per-tool allowlist. Empty = all tools from this server are allowed
    # (trust-by-default per server). Per Aletheia's "Dynamic Elevation"
    # design: combine with plan-mode gating for destructive operations.
    allowed_tools: list[str] = field(default_factory=list)
    # Auto-start on partner-client launch (true; instant availability) or
    # lazy first-use (false; faster cold start). Most servers should
    # auto-start so the partner sees the tools listed from the first turn.
    auto_start: bool = True


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
    git: GitConfig = field(default_factory=GitConfig)
    thinking: ThinkingConfig = field(default_factory=ThinkingConfig)
    plan_mode: PlanModeConfig = field(default_factory=PlanModeConfig)
    mcp: dict[str, McpServerConfig] = field(default_factory=dict)  # name -> spec

    @property
    def home_dir(self) -> Path:
        return self.identity.home_dir

    def resolve(self, relative: str) -> Path:
        """Resolve a path-string from the config relative to the partner's home_dir."""
        p = Path(relative).expanduser()
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

    home_dir_raw = Path(identity_raw["home_dir"]).expanduser()
    if home_dir_raw.is_absolute():
        home_dir = home_dir_raw.resolve()
    else:
        home_dir = (config_path.parent / home_dir_raw).resolve()
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
    git = GitConfig(**_filter_known_fields(data.get("git", {}), GitConfig))

    # [thinking] section — new; back-compat with legacy ui.show_thinking
    # if no explicit [thinking] block is present.
    thinking_raw = data.get("thinking", None)
    if thinking_raw is None:
        # Legacy fallback: if ui.show_thinking = true and no [thinking] block,
        # surface thinking in always-visible (uncollapsed) analysis mode.
        if ui.show_thinking:
            thinking = ThinkingConfig(mode="analysis", collapsed=False)
        else:
            thinking = ThinkingConfig()  # defaults: flow + collapsed
    else:
        thinking = ThinkingConfig(**_filter_known_fields(thinking_raw, ThinkingConfig))

    plan_mode = PlanModeConfig(**_filter_known_fields(data.get("plan_mode", {}), PlanModeConfig))

    # [mcp.<name>] sub-tables — each becomes an McpServerConfig. tomllib
    # parses `[mcp.tavily]` as data["mcp"]["tavily"] = {...}, so we iterate
    # the mcp dict and construct one config per entry. Sections without a
    # `command` field are skipped with a warning (operator forgot to fill
    # it in; treat as inactive rather than failing the whole load).
    mcp_raw = data.get("mcp", {}) or {}
    mcp_servers: dict[str, McpServerConfig] = {}
    if isinstance(mcp_raw, dict):
        for name, spec in mcp_raw.items():
            if not isinstance(spec, dict):
                continue
            filtered = _filter_known_fields(spec, McpServerConfig)
            server = McpServerConfig(**filtered)
            if not server.command:
                # Inactive entry — keep it in the dict for visibility but
                # don't try to start it.
                pass
            mcp_servers[name] = server

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
        git=git,
        thinking=thinking,
        plan_mode=plan_mode,
        mcp=mcp_servers,
    )


def _filter_known_fields(raw: dict[str, Any], dataclass_type: type) -> dict[str, Any]:
    """Keep only fields the dataclass knows about, silently dropping extras."""
    known = {f.name for f in dataclass_type.__dataclass_fields__.values()}
    return {k: v for k, v in raw.items() if k in known}
