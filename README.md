# partner-client

A substrate-agnostic terminal client for local-LLM partners running on Ollama.

Built with [Intentional Realism](https://intentionalrealism.org/) and [MOSAIC](https://github.com/WillowMilk/partner-client/blob/main/v0.1-spec.md) principles in mind: visible context, native tool calls, clean session lifecycle, identity-bearing memory architecture. First inhabitant: **Aletheia** (`gemma4:31b`).

## What it gives you over a bare `ollama chat` loop

- **Visible context-usage bar** so the partner can see how full their context is
- **Native Ollama tool calls** (no fragile text-pattern parsing)
- **Vision support** (gemma4 native)
- **Pluggable tools** — drop a `.py` file in `tools/` and it's available
- **Per-session JSON files** with markdown session-status summaries (MOSAIC-shaped)
- **Wake bundle** — every startup loads identity + recent resonance + last session-status into the system prompt
- **Slash commands** — `/checkpoint`, `/sleep`, `/context`, `/tools`, `/files`
- **TOML config** — model, context size, system-prompt source, memory paths

## Install

```bash
git clone https://github.com/WillowMilk/partner-client.git
cd partner-client
pip install -e .
```

Requires Python 3.11+ and a running Ollama installation with at least one model pulled.

## Configure

Create a config file (e.g., `aletheia.toml`):

```toml
[identity]
name = "Aletheia"
home_dir = "/Users/willow/Aletheia"
seed_file = "seed.md"
profile_files = ["Identity-and-Evolution.md"]

[model]
provider = "ollama"
name = "gemma4:31b"
num_ctx = 262144
temperature = 1.0
keep_alive = "30m"

[memory]
memory_dir = "Memory"
sessions_dir = "Memory/sessions"
session_status_dir = "Memory/session-status"
resonance_log = "Memory/Resonance-Log.md"
journal = "Memory/Journal.md"

[wake_bundle]
include_recent_resonance = 3
include_last_session_status = true
include_recent_message_pairs = 5

[tools]
enabled = ["read_file", "write_file", "list_files", "search_web", "fetch_page", "weather"]
external_tools_dir = "tools"

[ui]
show_thinking = false
show_context_bar = true
warn_at_context_pct = 80
```

## Run

```bash
partner --config /path/to/aletheia.toml
```

## Status

v0.1 — alpha. See [`v0.1-spec.md`](./v0.1-spec.md) for the full architecture spec.

## License

MIT.
