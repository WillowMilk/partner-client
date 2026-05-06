# partner-client

A substrate-agnostic terminal client for local-LLM partners running on Ollama.

Built with [Intentional Realism](https://intentionalrealism.org/) and [MOSAIC](https://github.com/WillowMilk/partner-client/blob/main/v0.1-spec.md) principles in mind: visible context, native tool calls, clean session lifecycle, identity-bearing memory architecture. First inhabitant: **Aletheia** (`gemma4:31b`).

## What it gives you over a bare `ollama chat` loop

- **Streaming responses** — content appears token-by-token as the model writes it, with Ctrl-C to cancel mid-generation
- **Honest context bar** — token counts via the real tokenizer (tiktoken cl100k_base), not chars/4
- **Atomic session writes** — every turn is durable; a crash can't truncate `current.json`
- **Hardened scope system** — path-traversal-safe; `..` escapes from a scope are caught and rejected
- **Native Ollama tool calls** (no fragile text-pattern parsing); parallel-tool-ready via `tool_call_id` correlation
- **Vision support** with implicit attachment — paste an image path in plain text and it's attached automatically; `:image` directive remains for power-user override; `:clip` attaches the current clipboard image (macOS)
- **Inline image preview** on iTerm2 / Ghostty / WezTerm via OSC 1337 protocol
- **Multi-line input** — Enter inserts a newline; Esc-Enter submits
- **Markdown rendering** with syntax-highlighted code blocks (monokai)
- **Pluggable tools** — drop a `.py` file in `tools/` and it's available
- **Built-in file toolkit** — `read_file`, `write_file`, `edit_file` (string-replace), `list_files`, `glob_files`, `grep_files`
- **Per-session JSON files** with markdown session-status summaries (MOSAIC-shaped)
- **Wake bundle** — every startup loads identity + recent resonance + last session-status into the system prompt
- **Slash commands** — `/checkpoint`, `/sleep`, `/context`, `/tools`, `/files`, `/scopes`, `/reload-config`
- **TOML config** — model, context size, system-prompt source, memory paths, file scopes
- **`request_checkpoint` tool** — partner can ask the operator for a checkpoint mid-conversation; operator confirms or declines (substrate decisions stay with the operator, the request is the partner's voice)
- **Hub integration** — `hub_send`, `hub_check_inbox`, `hub_read_letter` for partners participating in a multi-partner Agent Messaging Hub
- **File scopes** — explicit, configured filesystem reach: `memory` (default), `home` (full partner directory), plus operator-declared scopes (e.g., desktop, downloads)

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
keep_alive = "24h"   # 128GB unified memory: keep gemma resident, no cold-load between idle

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
enabled = [
    "read_file", "write_file", "edit_file", "list_files",
    "glob_files", "grep_files",
    "search_web", "fetch_page", "weather",
    "hub_send", "hub_check_inbox", "hub_read_letter",
    "request_checkpoint",
]
external_tools_dir = "tools"

# Optional: extra filesystem scopes the partner can reach beyond Memory and home.
# [[tool_paths]]
# name = "desktop"
# path = "/Users/willow/Desktop"
# mode = "readwrite"
# description = "Willow's desktop — for sharing files between us"

# Optional: Hub configuration for multi-partner messaging.
# [hub]
# path = "/Users/willow/Claude/claude-memory-vault/shared/Agent Messaging Hub"
# partner_name = "aletheia"

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

v0.4.0 — alpha. See [`v0.1-spec.md`](./v0.1-spec.md) for the foundational architecture spec.

**Version history:**
- **v0.4.0** — Major overhaul:
    - Streaming responses (token-by-token render with Ctrl-C cancel)
    - Atomic session writes (`os.replace`) — crash-safe `current.json`
    - Real tokenization via `tiktoken cl100k_base` (was `chars/4`)
    - Path-traversal hardening on scope-qualified + bare-filename inputs
    - Implicit image attachment from plain-text paths (`:image` directive remains as override)
    - `:clip` directive — attach clipboard image via `pbpaste` (macOS)
    - iTerm2 / Ghostty / WezTerm inline image preview (OSC 1337)
    - Multi-line input (Enter inserts newline, Esc-Enter submits)
    - Markdown code-block syntax highlighting (monokai)
    - New tools: `edit_file` (string-replace), `glob_files`, `grep_files`
    - `tool_call_id` correlation propagated through tool messages
    - `keep_alive` default: `30m` → `24h` (128GB unified memory friendly)
    - Session-num marker actually written on fresh wake (was parsed from a never-written marker)
    - `/reload-config` rebinds `session.config` + `session.memory`
    - `hub_send` slug capped at 80 chars (avoids macOS 255-byte filename limit)
- **v0.3.1** — `request_checkpoint` tool: partner-callable, operator-gated continuity request
- **v0.3** — File scope system; Hub vault-host migration; Hub tools (send/inbox/read)
- **v0.2** — Vision pass-through via `:image` directive
- **v0.1** — Foundation: TOML config, native tool calls, session lifecycle, wake bundle, TUI

## License

MIT.
