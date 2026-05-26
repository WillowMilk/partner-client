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
import time
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
        """Append user message + run chat loop + return assistant response.

        Phase 2a is non-streaming: JS awaits the full response. The Active
        Presence pulse on the avatar is JS-side, driven by the JS `is_streaming`
        flag (set true before await, false after). Phase 2b will add real
        token-by-token streaming via webview.evaluate_js() callbacks.

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
            response = self.client.chat(
                self.session,
                ui=None,
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
