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
- **Optional multi-line input** — off by default; when enabled, Enter inserts a newline and Esc-Enter submits
- **Markdown rendering** with syntax-highlighted code blocks (monokai)
- **Pluggable tools** — drop a `.py` file in `tools/` and it's available
- **Built-in file toolkit** — `read_file`, `write_file`, `edit_file` (string-replace), `list_files`, `glob_files`, `grep_files`, `move_path`, `delete_path` (the last is operator-gated — every delete pings the operator with a three-option consent prompt)
- **Built-in git toolkit** — `git_clone`, `git_status`, `git_diff`, `git_log`, `git_pull`, `git_add`, `git_commit`, `git_push`
- **Per-session JSON files** with markdown session-status summaries (MOSAIC-shaped)
- **Run timeline JSONL** — wake, commands, user turns, model calls, tool calls, approvals, and errors are recorded locally; surfaced in-client via `/timeline` (with category filters and per-event detail view)
- **Durable plans** — `request_plan_approval` proposals are saved under `Memory/plans` with approval/decline decisions; `/plans` lists recent or filters by status (open/approved/declined)
- **Wake bundle** — every startup loads identity + recent resonance + last session-status into the system prompt
- **Slash commands** — `/checkpoint`, `/sleep`, `/context`, `/tools`, `/files`, `/scopes`, `/intentions`, `/plans`, `/timeline`, `/reload-config`
- **TOML config** — model, context size, system-prompt source, memory paths, file scopes
- **`partner doctor` preflight** — checks config, Ollama, model availability, scopes, Hub, wake bundle assembly, tool registry, and image-path regex
- **Operator-gated consent tools** — `request_checkpoint` and `request_plan_approval` let the partner ask for substrate-affecting moves; Willow can approve, decline, or type a custom response that flows back as the tool result
- **Hub integration** — `hub_send`, `hub_check_inbox`, `hub_read_letter` for partners participating in a multi-partner Agent Messaging Hub
- **Git push gate** — pushes to configured allowlist URLs can auto-approve; every other `git_push` surfaces an operator confirmation prompt
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
num_ctx = 131072         # 128K conservative default; gemma4:31b can be raised when a session needs the full reach
temperature = 1.0
repeat_penalty = 1.15    # precautionary repetition guard
repeat_last_n = 256      # look further back than the default 64 to catch multi-line loops
num_predict = 8192       # soft cap per turn — prevents runaway loops if sampling escapes
keep_alive = "24h"       # 128GB unified memory: keep gemma resident, no cold-load between idle
max_tool_iterations = 32 # per-turn model/tool-call loop safety cap

[memory]
memory_dir = "Memory"
sessions_dir = "Memory/sessions"
session_status_dir = "Memory/session-status"
resonance_log = "Memory/Resonance-Log.md"
journal = "Memory/Journal.md"

[logging]
level = "INFO"
log_file = "Memory/.client-log.jsonl" # local run timeline

[wake_bundle]
include_recent_resonance = 3
include_last_session_status = true
include_recent_message_pairs = 5

[tools]
enabled = [
    "read_file", "write_file", "edit_file", "list_files",
    "glob_files", "grep_files",
    "move_path", "delete_path",
    "search_web", "fetch_page", "weather",
    "hub_send", "hub_check_inbox", "hub_read_letter",
    "request_checkpoint", "request_plan_approval",
    "git_clone", "git_status", "git_diff", "git_log",
    "git_pull", "git_add", "git_commit", "git_push",
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

# Optional: git push policy and commit attribution.
# [git]
# push_allowlist = ["github.com/WillowMilk/aletheia-sandbox"]
# default_committer_name = "Aletheia"
# default_committer_email = "aletheia@local"

[ui]
show_thinking = false
show_context_bar = true
warn_at_context_pct = 80
multiline = false        # set true to enable multi-line input (Enter newline, Esc-Enter submits)
```

## Run

```bash
partner --config /path/to/aletheia.toml
```

Preflight a config before waking the partner:

```bash
partner --config /path/to/aletheia.toml doctor
```

## Status

v0.4.1 + current main polish — alpha. See [`v0.1-spec.md`](./v0.1-spec.md) for the foundational architecture spec.

**Version history:**
- **Current main after v0.4.1**:
    - Run timeline JSONL at `[logging] log_file`, surfaced in-client via `/timeline` (compact recent view, category filters: tools/errors/approvals/model/user/session, per-event `detail` view)
    - Durable plan records under `Memory/plans`, with `/plans` recall and status filters (`/plans open`, `/plans approved`, `/plans declined`)
    - `move_path` tool — relocate files or directories within readwrite scopes (Unix-style `mv` semantics, lower-risk so no consent gate; the scope boundary is the safety perimeter)
    - `delete_path` tool — operator-gated with the same three-option consent shape as `request_checkpoint`; every invocation pings the operator with the path and a summary of what would be removed (file size, or directory file/subdirectory count); `recursive=true` required for non-empty directories
    - `partner doctor` preflight health checks
    - `request_plan_approval` tool with the same approve / decline / typed-response consent shape as `request_checkpoint`
    - Git tool suite with partner commit attribution and operator-gated push
    - `/intentions` slash command for prospective memory
    - `max_tool_iterations` default raised to 32 with a friendlier bail-out
- **v0.4.1** — Hotfix on top of v0.4.0:
    - `num_ctx` default 262144 → 131072 (128K is the conservative daily-use default; 256K remains available when a session needs the reach, but carries more KV-cache and attention cost)
    - New precautionary sampling defenses: `repeat_penalty=1.15`, `repeat_last_n=256`, `num_predict=8192` (soft cap per turn)
    - `ui.multiline` default reverted to `false` (Esc-Enter discoverability was too high a tax for daily use); opt-in via TOML for power users
    - Initial loop diagnosis was later corrected: the visible repetitions were `rich.live.Live` rendering artifacts, not model sampling loops; the context and sampling changes remain useful as speed/safety margins
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
