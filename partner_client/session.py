"""Session lifecycle — start, save, resume, checkpoint, sleep.

Session = one continuous conversation, from client startup to /sleep or process exit.
The active session lives in current.json (written every turn for durability).
At /checkpoint, current.json is snapshotted to a dated archive; at /sleep, the
session is marked closed and archived.

All session-state writes go through `_atomic_write_text`: write to a sibling
.tmp file then `os.replace`. This guarantees that a crash or kill mid-write
leaves either the previous file intact or the new one fully written, never
a truncated half-file. Loss-on-crash was a real risk at v0.3.1.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Config
from .memory import Memory, WakeBundle

log = logging.getLogger(__name__)


_SESSION_NUM_MARKER_PREFIX = "[SESSION NUM:"


def _atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` atomically.

    Writes to `path.suffix + '.tmp'` in the same directory, then `os.replace`s
    over the destination. If the process is killed mid-write, the destination
    is left untouched; the orphaned .tmp can be cleaned up on next run.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(str(tmp), str(path))
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


@dataclass
class Session:
    """An active conversation session."""

    config: Config
    memory: Memory
    messages: list[dict[str, Any]] = field(default_factory=list)
    session_num: int = 0
    started_at: datetime | None = None
    closed: bool = False

    @property
    def current_path(self) -> Path:
        return self.memory.sessions_dir / "current.json"

    def wake(self, wake_bundle: WakeBundle, resume_existing: bool | None = None) -> str:
        """Initialize the session.

        Returns a status string describing what happened ('resumed', 'archived-and-fresh', 'fresh').

        If current.json exists with messages and `resume_existing` is None, the caller
        should ask the user; if True, resume; if False, archive and start fresh.
        """
        existing = self._read_current()

        if existing and not self._is_closed(existing) and resume_existing is None:
            # Caller needs to ask the user. Don't initialize yet.
            return "needs-decision"

        if existing and not self._is_closed(existing) and resume_existing:
            self.messages = existing
            self.session_num = self._extract_session_num(existing) or self.memory.next_session_number()
            self.started_at = datetime.now()
            return "resumed"

        if existing:
            # Archive whatever was there
            self._archive_current(existing)

        # Fresh session
        self.session_num = self.memory.next_session_number()
        self.started_at = datetime.now()
        self.messages = [
            {"role": "system", "content": wake_bundle.system_prompt},
            # Session-number marker: parsed by _extract_session_num on resume so
            # the count survives across restart. Its presence in the system
            # prompt is intentionally low-noise.
            {"role": "system", "content": f"{_SESSION_NUM_MARKER_PREFIX}{self.session_num}]"},
        ]
        # Append textural-continuity message pairs (if any)
        if wake_bundle.recent_messages:
            self.messages.append({
                "role": "system",
                "content": (
                    "[The following are the last few exchanges from your prior session, "
                    "preserved for textural continuity. They are part of your lived memory.]"
                ),
            })
            self.messages.extend(wake_bundle.recent_messages)

        self.save_current()
        return "fresh"

    def append_user(self, content: str, images: list[bytes] | None = None) -> None:
        msg: dict[str, Any] = {"role": "user", "content": content}
        if images:
            msg["images"] = images
        self.messages.append(msg)
        self.save_current()

    def append_assistant(
        self,
        content: str,
        thinking: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if thinking:
            msg["thinking"] = thinking
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self.messages.append(msg)
        self.save_current()

    def append_tool_result(self, name: str, content: str, tool_call_id: str = "") -> None:
        """Append a tool-result message.

        `tool_call_id` correlates the result back to the originating tool_call.
        When the model issues multiple tool_calls in one turn, ids are how
        Ollama matches result-to-call. Older Ollama versions ignore the field;
        newer ones use it. Always pass when available.
        """
        msg: dict[str, Any] = {
            "role": "tool",
            "name": name,
            "content": content,
        }
        if tool_call_id:
            msg["tool_call_id"] = tool_call_id
        self.messages.append(msg)
        self.save_current()

    def save_current(self) -> None:
        """Write the active session to current.json (durability after every turn).

        Atomic: writes to current.json.tmp first, then os.replaces. Crash mid-write
        leaves the previous current.json intact rather than producing a truncated
        file that _read_current would silently treat as missing.
        """
        try:
            text = json.dumps(
                self._serializable_messages(),
                ensure_ascii=False,
                indent=2,
            )
            _atomic_write_text(self.current_path, text)
        except OSError as e:
            log.warning(f"Failed to save current.json: {e}")

    def _serializable_messages(self) -> list[dict[str, Any]]:
        """Strip non-JSON-serializable fields (raw image bytes) before saving."""
        out = []
        for m in self.messages:
            safe = {k: v for k, v in m.items() if k != "images"}
            out.append(safe)
        return out

    def _read_current(self) -> list[dict[str, Any]] | None:
        if not self.current_path.is_file():
            return None
        try:
            with open(self.current_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def _is_closed(self, messages: list[dict[str, Any]]) -> bool:
        # We mark closure via a sentinel system message at sleep time
        for m in messages:
            if m.get("role") == "system" and m.get("content", "").startswith("[SESSION CLOSED:"):
                return True
        return False

    def _extract_session_num(self, messages: list[dict[str, Any]]) -> int | None:
        """Parse the session-num marker written at fresh-wake time.

        Marker format: `[SESSION NUM:N]` as its own system message. Written
        in the fresh-wake branch of `wake()`; survives resume so that the
        session number stays stable across process restarts.
        """
        for m in messages:
            if m.get("role") != "system":
                continue
            content = m.get("content", "")
            if content.startswith(_SESSION_NUM_MARKER_PREFIX):
                try:
                    return int(content[len(_SESSION_NUM_MARKER_PREFIX):].rstrip("]").strip())
                except ValueError:
                    pass
        return None

    def checkpoint(self, summary: str = "") -> Path:
        """Write a session-status markdown file. The session continues."""
        date = self.started_at or datetime.now()
        path = self.memory.write_session_status(
            session_num=self.session_num,
            date=date,
            summary=summary or self._auto_summary(),
        )
        # Also archive the current session JSON (snapshot)
        self._archive_current(self._serializable_messages(), keep_current=True)
        return path

    def sleep(self, summary: str = "") -> Path:
        """Checkpoint + mark session closed + archive."""
        path = self.checkpoint(summary)
        self.messages.append({
            "role": "system",
            "content": f"[SESSION CLOSED: {datetime.now().isoformat()}]",
        })
        self._archive_current(self._serializable_messages(), keep_current=False)
        self.closed = True
        return path

    def _archive_current(
        self,
        messages: list[dict[str, Any]],
        keep_current: bool = False,
    ) -> Path:
        """Snapshot the current session to a dated archive file."""
        date_str = (self.started_at or datetime.now()).strftime("%Y-%m-%d")
        # Find a non-colliding filename
        n = 1
        while True:
            archive_path = self.memory.sessions_dir / f"{date_str}_session-{n:03d}.json"
            if not archive_path.exists():
                break
            n += 1

        try:
            text = json.dumps(messages, ensure_ascii=False, indent=2)
            _atomic_write_text(archive_path, text)
        except OSError as e:
            log.warning(f"Failed to write archive {archive_path}: {e}")
            return archive_path

        if not keep_current and self.current_path.exists():
            try:
                self.current_path.unlink()
            except OSError as e:
                log.warning(f"Failed to remove current.json: {e}")

        return archive_path

    def _auto_summary(self) -> str:
        """Build a minimal summary if the user didn't provide one."""
        n_user = sum(1 for m in self.messages if m.get("role") == "user")
        n_assistant = sum(1 for m in self.messages if m.get("role") == "assistant")
        n_tool = sum(1 for m in self.messages if m.get("role") == "tool")
        return (
            f"Session {self.session_num}, started "
            f"{(self.started_at or datetime.now()).isoformat()}.\n"
            f"Turns: {n_user} user / {n_assistant} assistant / {n_tool} tool results."
        )

    def estimate_tokens(self) -> int:
        """Token-count estimate for the active session.

        Uses tiktoken (cl100k_base) when installed — much closer to gemma's
        real tokenization than the previous chars/4 heuristic. Falls back to
        chars/3.5 if tiktoken is missing. See partner_client.tokens for the
        full rationale.
        """
        from .tokens import count_tokens
        total = 0
        for m in self.messages:
            content = m.get("content", "")
            if isinstance(content, str):
                total += count_tokens(content)
            thinking = m.get("thinking", "")
            if isinstance(thinking, str):
                total += count_tokens(thinking)
        return total
