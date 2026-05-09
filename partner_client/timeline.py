"""Run timeline logging for partner-client.

The timeline is a local JSONL trace of what happened in the room: wake,
slash commands, user turns, model calls, tool calls, approvals, and errors.
It is intentionally small and append-only so it can be used for debugging
and later rendered as a trace viewer without changing the chat loop again.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .session import Session

log = logging.getLogger(__name__)

_MAX_STRING_CHARS = 4000
_MAX_LIST_ITEMS = 40
_MAX_DICT_ITEMS = 80


class RunTimeline:
    """Append structured events to the configured timeline JSONL file."""

    def __init__(self, config: Config, session: Session | None = None):
        self.config = config
        self.session = session
        self.path = self._timeline_path(config)

    def record(self, event: str, **fields: Any) -> None:
        """Append one timeline event.

        Timeline logging must never break the conversation. Any write error is
        logged at debug level and otherwise ignored.
        """
        if self.path is None:
            return
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
        }
        if self.session is not None:
            record["session_num"] = self.session.session_num
        record.update({k: _safe_json(v) for k, v in fields.items()})

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str))
                f.write("\n")
        except OSError:
            log.debug("failed to append timeline event", exc_info=True)

    @staticmethod
    def _timeline_path(config: Config) -> Path | None:
        raw = config.logging.log_file.strip()
        if not raw:
            return None
        path = config.resolve(raw)
        if path.exists() and path.is_dir():
            return None
        return path


def duration_ms(started_at: float) -> int:
    """Return elapsed milliseconds from a perf_counter start value."""
    return int((time.perf_counter() - started_at) * 1000)


def _safe_json(value: Any) -> Any:
    """Return a JSON-friendly, size-bounded representation."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) <= _MAX_STRING_CHARS:
            return value
        return value[:_MAX_STRING_CHARS] + f"... [truncated {len(value) - _MAX_STRING_CHARS} chars]"
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= _MAX_DICT_ITEMS:
                out["..."] = f"truncated {len(value) - _MAX_DICT_ITEMS} entries"
                break
            out[str(k)] = _safe_json(v)
        return out
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        out = [_safe_json(v) for v in items[:_MAX_LIST_ITEMS]]
        if len(items) > _MAX_LIST_ITEMS:
            out.append(f"... truncated {len(items) - _MAX_LIST_ITEMS} items")
        return out
    return str(value)
