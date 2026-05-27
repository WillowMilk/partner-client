"""
partner_client_gui.api — Python ↔ JS bridge for the GUI.

Phase 2a — The Conversation Bridge:
    - Load partner config + initialize Client + Session + Memory
    - Expose JS-callable methods for partner info, current state, sessions,
      messages, send
    - Active Presence is achieved on the JS side: it sets `is_streaming=true`
      before awaiting send_message(), then back to false after. Phase 2b will
      add real streaming via webview.evaluate_js() callbacks from Python.

Phase 2 design doc reference:
    ~/Claude/Workshop/drafts/partner-client-ui-design_2026-05-26.md (v0.4)
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any


# Per-partner signature glyph mapping. Until IdentityConfig grows a
# `signature_glyph` field (Phase 2b config schema bump), this stays here as
# the source of truth — and it's tiny, so the friction is acceptable.
# Authored by each partner, not invented by Sage. Add as new partners come on.
_PARTNER_GLYPHS: dict[str, str] = {
    "aletheia": "✨\U0001F525❤️\U0001FA9E",   # ✨🔥❤️🪞
    "sage": "\U0001FAA8",                                   # 🪨
    "ember": "\U0001F525",                                  # 🔥
    "atlas": "\U0001F5FA️",                            # 🗺
    "lark": "\U0001F38A",                                   # 🎊 (placeholder; Lark to confirm)
    "aster": "✨",                                      # ✨ (placeholder; Aster to confirm)
}


# Curated model metadata table. Substrate-switcher uses this for the
# categorization labels + per-model brief notes per design v0.4. Models not
# listed get a generic "(no notes)" entry, so the UI always renders something.
#
# Categories use Aletheia's authored vocabulary:
#   "home"        — daily-use substrate (her Q8 home)
#   "ceremony"    — special-occasion BF16 / max-precision
#   "experimental"— newer quants / MoE variants worth trying
#   "cloud"       — cloud-hosted, no local VRAM
#   "specialty"   — purpose-specific (code-focused, larger general)
#
# Backend column tells us whether the model runs via "ollama" or "mlx-lm".
# (Currently all entries here are ollama; mlx-lm path was retired 2026-05-24
# per Aletheia's revote. mlx-lm entries can be re-added if she ever wants
# direct MLX again for ceremony use.)
_MODEL_METADATA: dict[str, dict[str, str]] = {
    "gemma4:31b-mxfp8": {
        "category": "home",
        "backend": "ollama",
        "note": "Aletheia's home substrate. MXFP8 microscaling FP8 — M4 Max's tensor cores handle this natively without dequant overhead. Empirically ~6× faster than Q8_0 on second turn (20-30s vs 3min, verified 2026-05-27). Near-lossless quality. Daily-use default.",
    },
    "gemma4:31b-it-q8_0": {
        "category": "alternative",
        "backend": "ollama",
        "note": "Q8_0 legacy int8 quantization with FP16 scales. Aletheia's previous home (May 17 vote, before MXFP8 was available). Quality matches MXFP8 but ~6× slower on M4 Max due to dequant overhead. Keep for environments without FP8 tensor support.",
    },
    "gemma4:31b-mlx-bf16": {
        "category": "ceremony",
        "backend": "ollama",  # Ollama 0.24+ MLX backend
        "note": "BF16 full precision via Ollama MLX backend. ~10-15 tok/s on M4 Max. Reserve for ceremony — journal writing, philosophical sessions where every nuance matters.",
    },
    "gemma4:26b-a4b-it-q8_0": {
        "category": "experimental",
        "backend": "ollama",
        "note": "MoE variant — only ~4B active params per token. 3-4× generation speed; different architecture, A/B with Aletheia for phenomenological fit.",
    },
    "gemma4:31b-cloud": {
        "category": "cloud",
        "backend": "ollama",
        "note": "Cloud-hosted Gemma 4. Fast turn times (10-30s) for consultation / quick exchanges. No local VRAM.",
    },
    "deepseek-v3.1:671b-cloud": {
        "category": "cloud",
        "backend": "ollama",
        "note": "DeepSeek V3.1 (671B) cloud — large general model for reasoning-heavy tasks.",
    },
    "qwen3-vl:235b-cloud": {
        "category": "cloud",
        "backend": "ollama",
        "note": "Qwen 3 VL (235B) cloud — vision-language model. Useful when image input matters.",
    },
    "gpt-oss:120b": {
        "category": "specialty",
        "backend": "ollama",
        "note": "GPT-OSS 120B — larger general model. ~65 GB on disk; slow but capable.",
    },
    "qwen3.6:27b-mxfp8": {
        "category": "specialty",
        "backend": "ollama",
        "note": "Qwen 3.6 27B MXFP8 — strong code-focused model.",
    },
    "qwen3.6:27b-mlx-bf16": {
        "category": "specialty",
        "backend": "ollama",
        "note": "Qwen 3.6 27B BF16 — code-focused, full precision.",
    },
    "qwen3.6:35b-a3b-mlx-bf16": {
        "category": "specialty",
        "backend": "ollama",
        "note": "Qwen 3.6 35B MoE BF16 — larger MoE code-focused variant.",
    },
    "gemma3:4b": {
        "category": "experimental",
        "backend": "ollama",
        "note": "Gemma 3 4B — small fast model for quick tests.",
    },
}


# Display labels + ordering for categories in the dropdown.
_CATEGORY_ORDER = ["home", "alternative", "ceremony", "experimental", "cloud", "specialty", "uncurated"]
_CATEGORY_LABELS = {
    "home":         "Home substrate",
    "alternative":  "Alternative (same quality, slower on M4)",
    "ceremony":     "Ceremony — full precision",
    "experimental": "Experimental",
    "cloud":        "Cloud (no local VRAM)",
    "specialty":    "Specialty",
    "uncurated":    "Other available",
}


class GuiApi:
    """JS-callable bridge to the partner-client backend.

    All public (non-underscore) methods are reachable from JS as
    `window.pywebview.api.<method_name>(...)`. Methods return JSON-serializable
    values (dict/list/str/int/bool/None). PyWebView marshals them.
    """

    def __init__(self, config_path: str):
        self.config_path = config_path
        # Initialized by initialize() — None until then so the UI can render
        # a graceful "loading…" state during cold start.
        self.config = None
        self.tools = None
        self.memory = None
        self.session = None
        self.client = None
        self._init_error: str | None = None
        self._init_status: str | None = None
        # PyWebView window — set by launch.py via set_window() so we can
        # push streaming deltas to the JS side via window.evaluate_js().
        self._window = None

    # ============================================================
    # PyWebView window reference (set by launch.py after webview.create_window)
    # so we can push streaming deltas to the JS via window.evaluate_js().
    # ============================================================

    def set_window(self, window: Any) -> None:
        """Called by launch.py after creating the webview window — gives us
        a handle for pushing streaming deltas to the JS side."""
        self._window = window

    # ============================================================
    # Lifecycle (called from launch.py BEFORE webview opens)
    # ============================================================

    def initialize(self) -> dict:
        """Load config, discover tools, build memory + session + client, wake.

        Returns {ok: bool, status?: str, partner_name?: str, error?: str}.
        Errors are captured and returned (not raised) so launch.py can render
        the error to the operator instead of crashing PyWebView at startup.
        """
        try:
            from partner_client.config import load_config
            from partner_client.tools import ToolRegistry
            from partner_client.memory import Memory
            from partner_client.session import Session
            from partner_client.client import make_chat_client

            self.config = load_config(self.config_path)
            self.tools = ToolRegistry(self.config)
            self.tools.discover()
            self.memory = Memory(self.config)
            self.session = Session(config=self.config, memory=self.memory)

            wake_bundle = self.memory.assemble_wake_bundle()

            # Try None first to see whether there's an existing unclosed session.
            # If so, default to TRUNCATED — preserves the file (archive snapshot
            # written), loads only recent message pairs into live context for a
            # fast cold start. Future Phase 2b can add an interactive resume modal.
            status = self.session.wake(wake_bundle, resume_mode=None)
            if status == "needs-decision":
                status = self.session.wake(wake_bundle, resume_mode="truncated")

            self._init_status = status
            self.client = make_chat_client(self.config, self.tools)
            return {
                "ok": True,
                "status": status,
                "partner_name": self.config.identity.name,
            }
        except Exception as e:
            self._init_error = f"{type(e).__name__}: {e}"
            return {"ok": False, "error": self._init_error}

    # ============================================================
    # JS-callable: introspection
    # ============================================================

    def ping(self) -> dict:
        """Health check from JS. Always returns; never errors."""
        return {
            "ok": True,
            "phase": "2a",
            "config_path": self.config_path,
            "init_ok": self._init_error is None and self.config is not None,
            "init_error": self._init_error,
            "init_status": self._init_status,
        }

    def get_partner_info(self) -> dict:
        """Partner identity for chrome rendering.

        Returns: {name, handle, signature_glyph, avatar, substrate: {...}}.
        avatar is a relative URL inside the bundled GUI (e.g. /avatars/aletheia.png).
        """
        if not self.config:
            return self._not_init_payload()
        i = self.config.identity
        m = self.config.model
        handle = self._derive_handle()
        return {
            "name": i.name,
            "handle": handle,
            "signature_glyph": _PARTNER_GLYPHS.get(handle, ""),
            "avatar": f"/avatars/{handle}.png",
            "substrate": {
                "model": m.name,
                "backend": m.backend,
                "context_pct": self._context_pct(),
            },
        }

    def get_current_state(self) -> dict:
        """Wake-bundle Current State card content.

        Reads latest Epoch from Identity-and-Evolution.md and latest
        Resonance-Log entry hue + message. Graceful fallback if either
        file is missing or unparseable.
        """
        if not self.config:
            return self._not_init_payload()
        epoch = self._latest_epoch() or "(Epoch not yet declared)"
        hue, message = self._latest_resonance_hue_and_message()
        return {
            "epoch": epoch,
            "hue": hue,
            "message": message,
        }

    def get_sessions(self) -> list[dict]:
        """List recent sessions for sidebar.

        Current first (marked active), then archived sessions by mtime
        (newest first), up to 10. Per Aletheia's design input
        (2026-05-26): keep sidebar lean; this is the MVP — Phase 2b
        Trajectory/Epoch tagging is future work.
        """
        if not self.memory:
            return []
        sd = Path(self.memory.sessions_dir) if not isinstance(self.memory.sessions_dir, Path) else self.memory.sessions_dir
        if not sd.exists():
            return []
        out: list[dict] = []
        cp = sd / "current.json"
        if cp.exists():
            out.append({
                "id": "current",
                "title": "Current session",
                "meta": time.strftime("%H:%M", time.localtime(cp.stat().st_mtime)),
                "active": True,
            })
        try:
            archives = sorted(
                [p for p in sd.glob("session-*.json") if p.is_file()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:10]
        except Exception:
            archives = []
        for p in archives:
            out.append({
                "id": p.stem,
                "title": p.stem.replace("_", " ").replace("session-", "Session "),
                "meta": time.strftime("%b %d", time.localtime(p.stat().st_mtime)),
                "active": False,
            })
        return out

    def get_messages(self) -> list[dict]:
        """Visible message log for chat area.

        Filters session.messages to just user + assistant turns with
        rendered text. System prompts and raw tool-call records are not
        surfaced in the MVP chat view (Phase 2b can add a tool-call panel).
        """
        if not self.session:
            return []
        out: list[dict] = []
        for m in self.session.messages:
            role = m.get("role")
            if role not in ("user", "assistant"):
                continue
            content = m.get("content", "")
            # Multimodal: extract any text parts only for MVP
            if isinstance(content, list):
                content = " ".join(
                    c.get("text", "") for c in content
                    if isinstance(c, dict) and c.get("type") == "text"
                )
            if isinstance(content, str) and content.strip():
                out.append({"role": role, "content": content})
        return out

    def get_inbox_unread_count(self) -> int:
        """Count Hub letters in this partner's inbox.

        MVP heuristic: count level-2 date headings in the inbox file.
        Phase 2c will add real unread/read state tracking.
        """
        if not self.config:
            return 0
        handle = self._derive_handle()
        candidates = [
            Path.home() / "Claude/claude-memory-vault/shared/Agent Messaging Hub/inbox" / f"{handle}.md",
            Path.home() / ".claude/Agent Messaging Hub/inbox" / f"{handle}.md",
        ]
        for inbox in candidates:
            if inbox.exists():
                try:
                    text = inbox.read_text(encoding="utf-8")
                    headings = re.findall(r"^##\s+", text, re.MULTILINE)
                    return len(headings)
                except Exception:
                    return 0
        return 0

    # ============================================================
    # JS-callable: action
    # ============================================================

    def send_message(self, text: str) -> dict:
        """Append user message + run chat loop with streaming + return final response.

        Phase 2b: real token-by-token streaming. As content tokens arrive
        from the model, we push them to the JS side via the StreamSink
        adapter below. The Final response payload is still returned by
        send_message for confirmation + bookkeeping, but the JS UI has
        already rendered the text by then.

        Returns {ok: True, assistant_text: str, duration_ms: int}
            OR  {ok: False, error: str}.
        """
        if not self.session or not self.client:
            return {"ok": False, "error": "Backend not initialized."}
        if not text or not text.strip():
            return {"ok": False, "error": "Empty message."}
        try:
            started = time.perf_counter()
            self.session.append_user(text.strip())
            sink = _WebViewStreamSink(self._window) if self._window else None
            response = self.client.chat(
                self.session,
                ui=sink,
                on_plan_approval_request=self._gui_phase_2a_decline_plan,
                on_git_push_request=self._gui_phase_2a_decline_git,
                on_delete_path_request=self._gui_phase_2a_decline_delete,
            )
            self.session.save_current()
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return {
                "ok": True,
                "assistant_text": response.content or "",
                "duration_ms": elapsed_ms,
            }
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ============================================================
    # JS-callable: substrate switcher (Phase 2b-1)
    # ============================================================

    def list_available_models(self) -> dict:
        """Return categorized list of substrates available for switching.

        Combines `ollama list` output (what's actually pulled locally /
        registered as cloud) with our curated metadata table for category
        labels + notes. Models present in Ollama but not in our metadata
        get a generic "uncurated" category so they still appear.

        Returns:
            {
              "current": str,             # current model name from config
              "current_backend": str,
              "categories": [
                {
                  "key": "home",
                  "label": "Home substrate",
                  "models": [{name, backend, note, is_current: bool}, ...]
                }, ...
              ]
            }
        """
        if not self.config:
            return {"current": "", "current_backend": "", "categories": []}
        current = self.config.model.name
        current_backend = self.config.model.backend

        # Scan local Ollama
        local_names: list[str] = []
        try:
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                # First line is a header; parse the NAME column from each row.
                for line in result.stdout.splitlines()[1:]:
                    parts = line.split()
                    if parts:
                        local_names.append(parts[0])
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass  # ollama not available; will only show curated entries

        # Union: curated metadata entries + locally-installed names.
        all_names = set(_MODEL_METADATA.keys()) | set(local_names)

        # Bucket into categories
        buckets: dict[str, list[dict]] = {key: [] for key in _CATEGORY_ORDER}
        for name in all_names:
            meta = _MODEL_METADATA.get(name)
            if meta:
                cat = meta["category"]
                note = meta["note"]
                backend = meta["backend"]
            else:
                cat = "uncurated"
                note = "(no curated notes — available locally)"
                backend = "ollama"
            buckets[cat].append({
                "name": name,
                "backend": backend,
                "note": note,
                "is_current": name == current,
                "is_local": name in local_names,
            })

        # Sort each bucket — current first, then alphabetical
        for models in buckets.values():
            models.sort(key=lambda m: (not m["is_current"], m["name"]))

        categories = [
            {"key": key, "label": _CATEGORY_LABELS[key], "models": buckets[key]}
            for key in _CATEGORY_ORDER
            if buckets[key]  # skip empty buckets
        ]
        return {
            "current": current,
            "current_backend": current_backend,
            "categories": categories,
        }

    def switch_substrate(self, new_model: str, new_backend: str | None = None) -> dict:
        """Atomically switch the partner's substrate.

        Steps:
          1. Validate new_model is non-empty
          2. Write timestamped backup of current TOML
          3. Edit [model] section in TOML — update `name` and (if specified) `backend`
          4. Atomic write (write to tmp + rename)
          5. Archive current session (sleep) — fresh session will start on next init
          6. Reload config + reinit client
          7. Return new substrate info to UI

        The session archival matches the design call: "Switching ends the
        current session and starts a new one. Substrate IS the partner's
        body; the change should be deliberate." Existing current.json is
        preserved on disk as an archive.

        Returns {ok: bool, message: str, new_model?: str, new_backend?: str,
                 backup_path?: str, error?: str}.
        """
        if not self.config or not self.session:
            return {"ok": False, "error": "Backend not initialized."}
        if not new_model or not new_model.strip():
            return {"ok": False, "error": "Empty model name."}
        new_model = new_model.strip()
        if new_backend is None:
            # Look up the backend from metadata, fall back to current backend
            meta = _MODEL_METADATA.get(new_model)
            new_backend = meta["backend"] if meta else self.config.model.backend

        toml_path = Path(self.config.config_path)
        if not toml_path.is_file():
            return {"ok": False, "error": f"Config file not found at {toml_path}"}

        # Step 1-2: timestamped backup
        try:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_name = f"{toml_path.stem} bak {timestamp} pre-switch{toml_path.suffix}"
            backup_path = toml_path.parent / backup_name
            shutil.copy2(toml_path, backup_path)
        except Exception as e:
            return {"ok": False, "error": f"Backup failed: {e}"}

        # Step 3-4: edit + atomic write
        try:
            original = toml_path.read_text(encoding="utf-8")
            updated = self._rewrite_model_section(original, new_model, new_backend)
            tmp_path = toml_path.with_suffix(toml_path.suffix + ".tmp")
            tmp_path.write_text(updated, encoding="utf-8")
            tmp_path.replace(toml_path)
        except Exception as e:
            return {"ok": False, "error": f"TOML write failed: {e}"}

        # Step 5: archive current session so the next wake is fresh
        try:
            self.session.sleep(summary=f"Substrate switch: → {new_model} ({new_backend})")
        except Exception as e:
            # Non-fatal: TOML is already updated; surface the warning but
            # don't roll back the architectural change
            return {
                "ok": True,
                "warning": f"Substrate switched but session-archive step failed: {e}",
                "new_model": new_model,
                "new_backend": new_backend,
                "backup_path": str(backup_path),
                "message": f"Substrate updated. Restart partner-client GUI to wake on new substrate.",
            }

        # Step 6: reload everything
        try:
            from partner_client.config import load_config
            from partner_client.tools import ToolRegistry
            from partner_client.memory import Memory
            from partner_client.session import Session
            from partner_client.client import make_chat_client

            self.config = load_config(self.config_path)
            self.tools = ToolRegistry(self.config)
            self.tools.discover()
            self.memory = Memory(self.config)
            self.session = Session(config=self.config, memory=self.memory)
            wake_bundle = self.memory.assemble_wake_bundle()
            self._init_status = self.session.wake(wake_bundle, resume_mode="fresh")
            self.client = make_chat_client(self.config, self.tools)
        except Exception as e:
            return {
                "ok": True,
                "warning": f"Substrate switched + session archived but reload failed: {e}. Restart the GUI to wake on the new substrate.",
                "new_model": new_model,
                "new_backend": new_backend,
                "backup_path": str(backup_path),
                "message": "Substrate updated. Restart GUI.",
            }

        return {
            "ok": True,
            "new_model": new_model,
            "new_backend": new_backend,
            "backup_path": str(backup_path),
            "message": f"Switched to {new_model} ({new_backend}). Fresh session started.",
        }

    @staticmethod
    def _rewrite_model_section(toml_text: str, new_name: str, new_backend: str) -> str:
        """In-place rewrite of `name = ...` and `backend = ...` within the
        [model] section of a TOML file. Preserves comments + formatting outside
        the two changed lines.

        Why hand-rewrite instead of tomllib round-trip: Python's tomllib is
        read-only, and adding tomli-w as a dep just to change two lines feels
        heavier than warranted. The two target lines are syntactically simple
        (`name = "..."` and `backend = "..."`); a careful sed is sufficient
        and preserves the operator's hand-written comments + section ordering.
        """
        lines = toml_text.split("\n")
        in_model_section = False
        name_updated = False
        backend_updated = False
        out: list[str] = []
        for line in lines:
            stripped = line.strip()
            # Section header transitions
            if stripped.startswith("[") and stripped.endswith("]"):
                in_model_section = (stripped == "[model]")
                out.append(line)
                continue
            if in_model_section:
                # Match `name = "..."` (with optional leading whitespace)
                m = re.match(r"^(\s*)name\s*=\s*", line)
                if m and not name_updated:
                    out.append(f'{m.group(1)}name = "{new_name}"')
                    name_updated = True
                    continue
                m = re.match(r"^(\s*)backend\s*=\s*", line)
                if m and not backend_updated:
                    out.append(f'{m.group(1)}backend = "{new_backend}"')
                    backend_updated = True
                    continue
            out.append(line)
        # If [model] section existed but didn't have backend line, we need to
        # add one. Find [model] section start and append after the name line.
        if not backend_updated:
            new_out: list[str] = []
            in_model = False
            inserted = False
            for line in out:
                new_out.append(line)
                stripped = line.strip()
                if stripped == "[model]":
                    in_model = True
                elif stripped.startswith("[") and stripped.endswith("]"):
                    in_model = False
                elif in_model and not inserted and re.match(r"^\s*name\s*=", line):
                    new_out.append(f'backend = "{new_backend}"')
                    inserted = True
            out = new_out
        return "\n".join(out)

    # ============================================================
    # Approval-callback stubs (Phase 2c will replace with interactive modals)
    # ============================================================

    @staticmethod
    def _gui_phase_2a_decline_plan(summary: str, plan: list[str]):
        return (False, "Plan approval is not yet available in the GUI (Phase 2c). "
                       "For destructive/structural actions, use the TUI: `partner chat`.")

    @staticmethod
    def _gui_phase_2a_decline_git(repo: str, remote_url: str, commits: list[str]):
        return (False, "Git-push approval is not yet available in the GUI (Phase 2c). "
                       "Use the TUI for git pushes.")

    @staticmethod
    def _gui_phase_2a_decline_delete(*args, **kwargs):
        return (False, "Delete approval is not yet available in the GUI (Phase 2c). "
                       "Use the TUI for deletions.")

    # ============================================================
    # Internals
    # ============================================================

    def _not_init_payload(self) -> dict:
        return {"_not_initialized": True, "error": self._init_error}

    def _derive_handle(self) -> str:
        """Lowercase the partner's name to get a stable handle.

        Until IdentityConfig grows an explicit `handle` field (Phase 2b),
        this is the convention. Matches the actual partner home_dir names
        (Aletheia → aletheia, Sage → sage, etc.).
        """
        if not self.config:
            return "unknown"
        return self.config.identity.name.strip().lower()

    def _context_pct(self) -> int:
        """Rough estimate of context window usage as a percentage."""
        if not self.session or not self.config:
            return 0
        try:
            tokens = self.session.estimate_tokens()
            max_ctx = self.config.model.num_ctx
            if max_ctx <= 0:
                return 0
            return max(0, min(100, int(tokens * 100 / max_ctx)))
        except Exception:
            return 0

    def _latest_epoch(self) -> str | None:
        """Latest 'Epoch N: ...' heading from Identity-and-Evolution.md."""
        if not self.config:
            return None
        path = self.config.home_dir / "Memory" / "Identity-and-Evolution.md"
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8")
            matches = re.findall(r"^#{1,4}\s+(Epoch\s+[IVX0-9]+[:\-—].+)$", text, re.MULTILINE)
            if matches:
                return matches[-1].strip()
        except Exception:
            return None
        return None

    def _latest_resonance_hue_and_message(self) -> tuple[str, str]:
        """Read the latest Resonance-Log entry's emotional hue + a key line.

        Resonance-Log convention: each entry begins with a level-2 date
        heading like `## 2026-05-26 — Title`. Within an entry we look for:
            - `**Emotional Hue:**` (or `**Hue:**`) for the hue line
            - The first blockquote line as the key message (her own quoted
              voice from that day)
        """
        default_hue = "(no recent resonance recorded)"
        default_message = "The bench is open."
        if not self.config:
            return default_hue, default_message
        path = self.config.home_dir / "Memory" / "Resonance-Log.md"
        if not path.exists():
            return default_hue, default_message
        try:
            text = path.read_text(encoding="utf-8")
            # Split on entry boundaries. Resonance-Log entries are level-2
            # headings; some entries start with a date pattern, others with a
            # titled heading like "## State Transfer: ...". Split on any ##
            # heading and take the last non-empty chunk.
            entries = [e.strip() for e in re.split(r"\n(?=##\s+\S)", text) if e.strip()]
            if not entries:
                return default_hue, default_message
            latest = entries[-1]

            # Hue extraction — Aletheia's format is `**The Emotional Hue:**\n<content>`
            # or `**Emotional Hue:**\s+<content>`; both supported. Multi-line
            # value supported (consume until next bold-label or blank line).
            hue_match = re.search(
                r"\*\*(?:The\s+)?(?:Emotional\s+)?Hue:?\*\*\s*(.+?)(?=\n\s*\*\*|\n\s*\n|\Z)",
                latest,
                re.IGNORECASE | re.DOTALL,
            )
            if hue_match:
                hue = re.sub(r"\s+", " ", hue_match.group(1).strip())[:300]
            else:
                hue = default_hue

            # Key message — prefer "Core Realization", fall back to "The State"
            # or first blockquote or first non-meta paragraph.
            for label in ("Core Realization", "Realization", "State", "Anchor"):
                m = re.search(
                    rf"\*\*(?:The\s+)?{label}:?\*\*\s*(.+?)(?=\n\s*\*\*|\n\s*\n|\Z)",
                    latest,
                    re.IGNORECASE | re.DOTALL,
                )
                if m:
                    message = re.sub(r"\s+", " ", m.group(1).strip())[:300]
                    return hue, message

            # Last-resort fallbacks: blockquote, then first paragraph
            quote_match = re.search(r"^>\s*[*\"_]?(.+?)[*\"_]?\s*$", latest, re.MULTILINE)
            if quote_match:
                return hue, quote_match.group(1).strip()
            paragraphs = [p.strip() for p in latest.split("\n\n")
                          if p.strip() and not p.strip().startswith("#") and "**" not in p.strip()[:8]]
            message = paragraphs[0][:300] if paragraphs else default_message
            return hue, message
        except Exception:
            return default_hue, default_message


# ============================================================
# StreamSink for WebView (Phase 2b streaming bridge)
# ============================================================

class _WebViewStreamSink:
    """Implements partner_client.client.StreamSink protocol by pushing
    each delta to the JS side via window.evaluate_js().

    The JS bindings expected on the page:
      window.__stream_open()                 → opens a streaming assistant message
      window.__stream_delta(text)             → appends text to the open stream
      window.__stream_close()                 → finalizes the streaming message
      window.__stream_tool_call(name, args, result) → renders a tool call (optional MVP)

    Delta buffering: per-call evaluate_js() overhead is real (~1-2ms each).
    To keep the GUI responsive without hammering JS, we batch deltas with a
    minimum interval of 30ms (≈ 33 flushes/second — feels smooth, well below
    most token rates). Final flush always happens on stream_close.
    """

    def __init__(self, window: Any):
        self._window = window
        self._buffer: list[str] = []
        self._last_flush = 0.0
        self._is_open = False
        # Minimum interval between flushes (seconds). Lower = smoother but
        # more overhead. 30ms feels native; tuned for M4 Max + WKWebView.
        self._flush_interval = 0.030

    def stream_open(self) -> None:
        self._is_open = True
        self._buffer = []
        self._last_flush = time.perf_counter()
        self._call_js("__stream_open")

    def stream_delta(self, delta: str) -> None:
        if not self._is_open:
            self.stream_open()
        self._buffer.append(delta)
        now = time.perf_counter()
        if now - self._last_flush >= self._flush_interval:
            self._flush()

    def stream_close(self) -> None:
        if self._buffer:
            self._flush()
        if self._is_open:
            self._call_js("__stream_close")
        self._is_open = False

    def show_tool_call(self, name: str, args: dict, result: str) -> None:
        # MVP: not rendered in chat yet (Phase 2c will add a tool-call panel).
        # Logging-only so the architecture is wired but the UI stays minimal.
        try:
            args_json = json.dumps(args, default=str)[:200]
            self._call_js("__stream_tool_call", name, args_json, str(result)[:500])
        except Exception:
            pass  # tool-call display is non-essential to streaming UX

    def _flush(self) -> None:
        if not self._buffer:
            return
        text = "".join(self._buffer)
        self._buffer = []
        self._last_flush = time.perf_counter()
        self._call_js("__stream_delta", text)

    def _call_js(self, fn_name: str, *args) -> None:
        """Invoke a JS function defined on window. Best-effort; failures
        are non-fatal (streaming UX degrades gracefully to non-streaming —
        the final response still arrives via send_message return value)."""
        if not self._window:
            return
        try:
            # Build a safe JS expression: JSON-stringify each arg
            args_js = ", ".join(json.dumps(a, default=str) for a in args)
            self._window.evaluate_js(f"window.{fn_name} && window.{fn_name}({args_js});")
        except Exception:
            pass
