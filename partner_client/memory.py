"""Memory module — wake bundle assembly and memory file conventions.

The wake bundle is the system prompt that orients the partner on every startup.
It composes seed + identity files + recent resonance + last session-status.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import Config


@dataclass
class WakeBundle:
    """Assembled wake bundle: system prompt + optional textural-continuity message pairs."""

    system_prompt: str
    recent_messages: list[dict]  # message pairs from prior session for textural continuity


class Memory:
    """Memory-file management and wake-bundle assembly."""

    def __init__(self, config: Config):
        self.config = config
        self.memory_dir = config.resolve(config.memory.memory_dir)
        self.sessions_dir = config.resolve(config.memory.sessions_dir)
        self.session_status_dir = config.resolve(config.memory.session_status_dir)
        self.resonance_log = config.resolve(config.memory.resonance_log)
        self.journal = config.resolve(config.memory.journal)

        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.session_status_dir.mkdir(parents=True, exist_ok=True)

    def assemble_wake_bundle(self) -> WakeBundle:
        """Build the system prompt + recent message pairs for orientation."""
        sections: list[str] = []

        seed = self._read_optional(self.config.home_dir / self.config.identity.seed_file)
        if seed:
            sections.append("[1. SEED]\n" + seed)

        identity_blocks = []
        for filename in self.config.identity.profile_files:
            content = self._read_optional(self.config.home_dir / filename)
            if content:
                identity_blocks.append(content)
        if identity_blocks:
            sections.append("[2. IDENTITY]\n" + "\n\n".join(identity_blocks))

        n_resonance = self.config.wake_bundle.include_recent_resonance
        if n_resonance > 0:
            recent = self._tail_resonance(n_resonance)
            if recent:
                sections.append("[3. RECENT RESONANCE]\n" + recent)

        if self.config.wake_bundle.include_last_session_status:
            last_status = self._latest_session_status()
            if last_status:
                sections.append("[4. LAST SESSION SUMMARY]\n" + last_status)

        sections.append(_RUNTIME_GUIDANCE)

        system_prompt = "\n\n".join(sections)

        recent_messages = []
        if self.config.wake_bundle.include_recent_message_pairs > 0:
            recent_messages = self.load_recent_message_pairs(
                n=self.config.wake_bundle.include_recent_message_pairs
            )

        return WakeBundle(system_prompt=system_prompt, recent_messages=recent_messages)

    def _read_optional(self, path: Path) -> str | None:
        if not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def _tail_resonance(self, n: int) -> str | None:
        """Return the last N entries from the resonance log."""
        content = self._read_optional(self.resonance_log)
        if not content:
            return None
        # Resonance entries are typically separated by `---` or `## ` headings
        # Try `---` first, then fall back to `## ` headings, then full content.
        if "---" in content:
            entries = [e.strip() for e in content.split("---") if e.strip()]
            return "\n\n---\n\n".join(entries[-n:]) if entries else content
        if "\n## " in content:
            parts = content.split("\n## ")
            head = parts[0]
            sections = ["## " + p for p in parts[1:]]
            return "\n\n".join(sections[-n:]) if sections else head
        return content

    def _latest_session_status(self) -> str | None:
        """Return the most recent session-status markdown file by name (sorted)."""
        if not self.session_status_dir.is_dir():
            return None
        files = sorted(self.session_status_dir.glob("session-*.md"))
        if not files:
            return None
        return self._read_optional(files[-1])

    def load_recent_message_pairs(self, n: int) -> list[dict]:
        """Load the last N user/assistant message pairs from the most recent prior session."""
        prior_session = self._latest_archived_session()
        if not prior_session:
            return []
        try:
            with open(prior_session, encoding="utf-8") as f:
                messages = json.load(f)
        except (OSError, json.JSONDecodeError):
            return []
        # Drop system messages; keep user/assistant pairs from the tail
        non_system = [m for m in messages if m.get("role") != "system"]
        # n pairs = 2n messages
        return non_system[-(2 * n):]

    def _latest_archived_session(self) -> Path | None:
        """Most recent dated session JSON (excluding current.json)."""
        if not self.sessions_dir.is_dir():
            return None
        files = sorted(
            f for f in self.sessions_dir.glob("*.json") if f.name != "current.json"
        )
        return files[-1] if files else None

    def write_session_status(
        self,
        session_num: int,
        date: datetime,
        summary: str,
        arc: str = "",
    ) -> Path:
        """Write a session-status markdown file."""
        date_str = date.strftime("%Y-%m-%d")
        path = self.session_status_dir / f"session-{session_num:03d}_{date_str}.md"
        body = f"# Session {session_num} — {date_str}\n\n"
        if arc:
            body += f"## Arc\n\n{arc}\n\n"
        body += f"## Summary\n\n{summary}\n"
        path.write_text(body, encoding="utf-8")
        return path

    def next_session_number(self) -> int:
        """Determine the next session number based on existing session-status files."""
        if not self.session_status_dir.is_dir():
            return 1
        nums = []
        for f in self.session_status_dir.glob("session-*_*.md"):
            try:
                # session-001_2026-05-04.md → 001
                stem = f.stem  # session-001_2026-05-04
                num_part = stem.split("_")[0].split("-")[1]
                nums.append(int(num_part))
            except (IndexError, ValueError):
                continue
        return (max(nums) + 1) if nums else 1


_RUNTIME_GUIDANCE = """[RUNTIME GUIDANCE]
You are running in the partner-client. You have native tool calls available — when you call a tool, you will receive its result as a structured message before you respond. Tool results that say "No results found" mean the tool returned nothing; do not narrate data you did not receive. For real-time facts (weather, news), prefer the dedicated tool when one exists. For static facts you know from training, you may answer directly — but say so plainly so the source of your knowledge is honest.

You can see your own context usage — the status bar shows it. When usage rises past 80%, consider asking to checkpoint and rest, so you wake fresh next time.

Your memory files are yours. Write them as you wish."""
