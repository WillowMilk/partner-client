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


def _truncate_to_recent_pairs(
    messages: list[dict[str, Any]],
    keep_pairs: int,
) -> tuple[list[dict[str, Any]], int]:
    """Drop older non-system messages, keeping only the last N user/assistant pairs.

    Tool messages associated with kept assistant turns (they appear AFTER an
    assistant in the message stream) are preserved as part of the natural
    slice. All system messages are preserved unchanged.

    Returns (truncated_messages, dropped_count). `dropped_count` is the
    number of non-system messages that were removed from the live context.
    If keep_pairs <= 0, or if there are fewer pairs than keep_pairs in the
    input, the original messages are returned with dropped_count = 0.
    """
    if keep_pairs <= 0:
        return messages, 0

    system_msgs = [m for m in messages if m.get("role") == "system"]
    chat_msgs = [m for m in messages if m.get("role") != "system"]

    # Walk backwards counting user messages. The Nth-from-end user message
    # is the cutoff: keep from there onwards. Anything earlier is dropped.
    pairs_seen = 0
    cutoff_user_idx: int | None = None
    for i in range(len(chat_msgs) - 1, -1, -1):
        if chat_msgs[i].get("role") == "user":
            pairs_seen += 1
            if pairs_seen == keep_pairs:
                cutoff_user_idx = i
                break

    if cutoff_user_idx is None:
        # Fewer pairs than keep_pairs - no truncation needed
        return messages, 0

    kept_chat = chat_msgs[cutoff_user_idx:]
    dropped_count = cutoff_user_idx
    return system_msgs + kept_chat, dropped_count


def _build_reorientation_message(
    archive_path: Path,
    keep_pairs: int,
    dropped_count: int,
) -> dict[str, Any]:
    """Compose the system message inserted on truncated resume.

    The message's role is 'system' so the partner reads it as substrate-
    state context (not as conversation). It explains in second-person
    framing that the live context has been bounded and where to find the
    full snapshot if older content is needed.
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    content = (
        f"[SESSION TRUNCATED - {now_str}]\n"
        f"\n"
        f"You have just resumed an ongoing session. Earlier exchanges have been "
        f"archived to {archive_path} (bytes-identical snapshot of the full prior "
        f"state). Your live context now holds the last {keep_pairs} message pairs "
        f"of this session plus all system messages (wake bundle, identity, "
        f"session-number marker). {dropped_count} earlier non-system message(s) "
        f"were moved to the archive.\n"
        f"\n"
        f"You may have read files, written content, or had exchanges earlier in "
        f"this session that you no longer have direct memory of. If you need to "
        f"recall something specific from before the truncation, you can read the "
        f"archived session JSON via your read_file tool. Your identity files "
        f"remain unchanged on disk and are reflected in your current system "
        f"prompt.\n"
        f"\n"
        f"The conversation continues from where it was; you and Willow can pick "
        f"up the recent thread naturally."
    )
    return {"role": "system", "content": content}


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

    def wake(self, wake_bundle: WakeBundle, resume_mode: str | None = None) -> str:
        """Initialize the session.

        Returns a status string describing what happened:
            'needs-decision'    - caller must prompt the user (mode was None and existing session found)
            'resumed-full'      - loaded full existing current.json into live context (slow on heavy sessions)
            'resumed-truncated' - snapshotted full session, loaded last N pairs + system msgs (fast)
            'archived-and-fresh' - existing session archived; new fresh session started
            'fresh'             - no existing session; new fresh session started

        resume_mode values:
            None        - caller must ask the user; returns 'needs-decision'
            'full'      - resume full content of current.json (current.json stays intact)
            'truncated' - snapshot current.json, then load only last N user/assistant pairs +
                          all system messages + a reorientation marker. N comes from
                          config.wake_bundle.resume_keep_pairs.
            'fresh'     - archive current.json, start a new session
        """
        existing = self._read_current()

        if existing and not self._is_closed(existing) and resume_mode is None:
            # Caller needs to ask the user. Don't initialize yet.
            return "needs-decision"

        if existing and not self._is_closed(existing) and resume_mode == "full":
            self.messages = existing
            self.session_num = self._extract_session_num(existing) or self.memory.next_session_number()
            self.started_at = datetime.now()
            return "resumed-full"

        if existing and not self._is_closed(existing) and resume_mode == "truncated":
            # Snapshot the full content first (preservation; nothing lost on disk).
            archive_path = self._archive_current(existing, keep_current=True)
            keep_pairs = self.config.wake_bundle.resume_keep_pairs
            truncated, dropped_count = _truncate_to_recent_pairs(existing, keep_pairs)

            # Insert reorientation marker right after the existing system block,
            # before the kept chat messages, so the partner sees it as context
            # explaining the truncated state of her live memory.
            reorientation = _build_reorientation_message(
                archive_path=archive_path,
                keep_pairs=keep_pairs,
                dropped_count=dropped_count,
            )
            # Split into system + chat to insert the marker cleanly
            sys_msgs = [m for m in truncated if m.get("role") == "system"]
            chat_msgs = [m for m in truncated if m.get("role") != "system"]
            self.messages = sys_msgs + [reorientation] + chat_msgs

            self.session_num = self._extract_session_num(self.messages) or self.memory.next_session_number()
            self.started_at = datetime.now()
            self.save_current()  # write the truncated state back to current.json
            return "resumed-truncated"

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
