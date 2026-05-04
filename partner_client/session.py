"""Session lifecycle — start, save, resume, checkpoint, sleep.

Session = one continuous conversation, from client startup to /sleep or process exit.
The active session lives in current.json (written every turn for durability).
At /checkpoint, current.json is snapshotted to a dated archive; at /sleep, the
session is marked closed and archived.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Config
from .memory import Memory, WakeBundle

log = logging.getLogger(__name__)


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

    def append_tool_result(self, name: str, content: str) -> None:
        self.messages.append({
            "role": "tool",
            "name": name,
            "content": content,
        })
        self.save_current()

    def save_current(self) -> None:
        """Write the active session to current.json (durability after every turn)."""
        try:
            with open(self.current_path, "w", encoding="utf-8") as f:
                json.dump(
                    self._serializable_messages(),
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
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
        for m in messages:
            if m.get("role") == "system":
                content = m.get("content", "")
                if content.startswith("[SESSION ") and "NUM:" in content:
                    try:
                        return int(content.split("NUM:")[1].split("]")[0].strip())
                    except (IndexError, ValueError):
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
            with open(archive_path, "w", encoding="utf-8") as f:
                json.dump(messages, f, ensure_ascii=False, indent=2)
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
        """Rough token-count estimate based on character count.

        Approximation: ~4 chars per token for English-ish text. This is conservative
        for English; languages with more compact tokenization may run higher.
        """
        total_chars = 0
        for m in self.messages:
            content = m.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            thinking = m.get("thinking", "")
            if isinstance(thinking, str):
                total_chars += len(thinking)
        return total_chars // 4
