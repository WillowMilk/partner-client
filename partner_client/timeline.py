"""Run timeline logging + reading for partner-client.

The timeline is a local JSONL trace of what happened in the room: wake,
slash commands, user turns, model calls, tool calls, approvals, and errors.
It is intentionally small and append-only so it can be used for debugging
and later rendered as a trace viewer without changing the chat loop again.

Two halves:
- `RunTimeline` writes events as the chat loop produces them.
- `TimelineReader` reads them back for the `/timeline` slash command,
  with category filters and a compact one-line-per-event view.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

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


# --- Reader ---------------------------------------------------------------


# Categories used by `/timeline <category>`. Keep these stable: the keys are
# the user-visible filter words. New event types should be added to the
# matching set so the filter remains exhaustive.
TIMELINE_CATEGORIES: dict[str, set[str]] = {
    "tools": {"tool_call", "tool_loop_limit"},
    "errors": {
        "model_call_error",
        "chat_error",
        "plan_persist_error",
        "plan_decision_persist_error",
        "config_reload_error",
    },
    "approvals": {
        "plan_proposed",
        "plan_decision",
        "checkpoint_requested",
        "checkpoint_decision",
        "git_push_requested",
        "git_push_decision",
    },
    "model": {
        "model_call_start",
        "model_call_end",
        "model_call_error",
        "assistant_response",
    },
    "user": {"user_message", "slash_command"},
    "session": {
        "session_wake",
        "config_reloaded",
        "config_reload_error",
        "generation_cancelled",
    },
}


class TimelineReader:
    """Read events from the timeline JSONL for in-client display.

    Pairs with `RunTimeline` (writer). Reading is best-effort: corrupted
    lines are skipped, missing files surface friendly messages instead of
    raising. The reader never modifies the file.
    """

    def __init__(self, config: Config):
        self.config = config
        self.path = RunTimeline._timeline_path(config)

    def list_recent(
        self,
        limit: int = 20,
        event_types: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return up to `limit` most recent events (chronological order),
        optionally filtered by event-type set."""
        if self.path is None or not self.path.is_file():
            return []
        events: list[dict[str, Any]] = []
        try:
            with self.path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event_types is not None and rec.get("event") not in event_types:
                        continue
                    events.append(rec)
        except OSError:
            return []
        if limit > 0 and len(events) > limit:
            return events[-limit:]
        return events

    def format_recent(
        self,
        limit: int = 20,
        event_types: set[str] | None = None,
        category_label: str | None = None,
    ) -> str:
        """Format recent events as a compact one-line-per-event view.

        Output is chronological (oldest visible first, newest at the bottom)
        so reading top-to-bottom matches the order events occurred.
        """
        if self.path is None:
            return (
                "No timeline configured. Set [logging] log_file in your "
                "config to start recording."
            )
        if not self.path.is_file():
            return f"No timeline events recorded yet at {self.path}."

        events = self.list_recent(limit=limit, event_types=event_types)
        if not events:
            if event_types is not None:
                label = category_label or "filter"
                return f"No timeline events match '{label}'."
            return f"No timeline events recorded yet at {self.path}."

        header = "Recent timeline events"
        if category_label:
            header += f" ({category_label})"
        header += f" — showing {len(events)}"
        lines = [header, ""]
        idx_width = max(2, len(str(len(events))))
        for i, ev in enumerate(events, start=1):
            lines.append(_format_event_line(i, ev, idx_width=idx_width))
        return "\n".join(lines)

    def format_detail(
        self,
        index: int,
        limit: int = 20,
        event_types: set[str] | None = None,
    ) -> str:
        """Format one event, identified by 1-based index from the recent view.

        The same `limit`/`event_types` that produced the listing should be
        passed for indices to line up. By default the indices match
        `format_recent()`'s default of 20 unfiltered events.
        """
        if self.path is None:
            return "No timeline configured."
        if not self.path.is_file():
            return f"No timeline events recorded yet at {self.path}."

        events = self.list_recent(limit=limit, event_types=event_types)
        if not events:
            return "No timeline events available."
        if index < 1 or index > len(events):
            return f"Index {index} out of range (1..{len(events)})."

        ev = events[index - 1]
        ts = _short_ts(ev.get("ts", ""))
        lines = [
            f"Event #{index} — {ev.get('event', '?')} @ {ts}",
            "",
        ]
        for k in sorted(ev.keys()):
            if k in {"event", "ts"}:
                continue
            v = ev[k]
            if isinstance(v, str) and "\n" in v:
                lines.append(f"  {k}:")
                for sub in v.splitlines():
                    lines.append(f"    {sub}")
            elif isinstance(v, str):
                lines.append(f"  {k}: {v}")
            else:
                lines.append(
                    f"  {k}: {json.dumps(v, ensure_ascii=False, default=str)}"
                )
        return "\n".join(lines)


# Per-event-type compact summarizers. Each takes the event dict and returns
# a short string with the most-relevant fields for that event. Unknown
# event types fall back to a generic summarizer.
_EVENT_SUMMARIZERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "session_wake": lambda r: (
        f"status={r.get('status', '?')} wake={r.get('wake_bundle_chars', 0)}ch"
    ),
    "user_message": lambda r: (
        f"{r.get('chars', 0)}ch"
        + (f" +{r['images']}img" if r.get("images") else "")
    ),
    "slash_command": lambda r: r.get("command", "?"),
    "model_call_start": lambda r: (
        f"iter={r.get('iteration', '?')} ctx={r.get('context_tokens', '?')}"
    ),
    "model_call_end": lambda r: (
        f"iter={r.get('iteration', '?')} "
        f"{r.get('duration_ms', '?')}ms "
        f"{r.get('content_chars', 0)}ch "
        f"tools={r.get('tool_call_count', 0)}"
    ),
    "model_call_error": lambda r: (
        f"iter={r.get('iteration', '?')} "
        f"{_truncate(str(r.get('error', '')), 60)}"
    ),
    "assistant_response": lambda r: (
        f"{r.get('content_chars', 0)}ch tools={r.get('tool_invocation_count', 0)}"
    ),
    "tool_call": lambda r: (
        f"{r.get('name', '?')} {r.get('duration_ms', '?')}ms"
    ),
    "tool_loop_limit": lambda r: f"max={r.get('max_iterations', '?')}",
    "plan_proposed": lambda r: (
        f"steps={r.get('step_count', '?')} "
        f"{_truncate(str(r.get('summary', '')), 50)}"
    ),
    "plan_decision": lambda r: (
        f"{r.get('status', '?')}"
        + (" +msg" if r.get("custom_message") else "")
    ),
    "plan_persist_error": lambda r: _truncate(str(r.get("error", "")), 60),
    "plan_decision_persist_error": lambda r: _truncate(str(r.get("error", "")), 60),
    "checkpoint_requested": lambda r: _truncate(str(r.get("reason", "")), 60),
    "checkpoint_decision": lambda r: (
        ("accepted" if r.get("accepted") else "declined")
        + (" +msg" if r.get("custom_message") else "")
    ),
    "git_push_requested": lambda r: (
        f"{r.get('repo', '?')} commits={r.get('commit_count', 0)}"
    ),
    "git_push_decision": lambda r: (
        ("accepted" if r.get("accepted") else "declined")
        + (" +msg" if r.get("custom_message") else "")
    ),
    "generation_cancelled": lambda r: f"ctx={r.get('context_tokens', '?')}",
    "chat_error": lambda r: _truncate(str(r.get("error", "")), 60),
    "config_reloaded": lambda r: "",
    "config_reload_error": lambda r: _truncate(str(r.get("error", "")), 60),
}


def _format_event_line(index: int, ev: dict[str, Any], idx_width: int = 2) -> str:
    ts = _short_ts(ev.get("ts", ""))
    name = ev.get("event", "?")
    summary = _summarize_event(ev)
    return f"  {index:>{idx_width}}  {ts}  {name:<22}  {summary}"


def _summarize_event(ev: dict[str, Any]) -> str:
    fn = _EVENT_SUMMARIZERS.get(ev.get("event", ""))
    if fn is not None:
        try:
            return fn(ev)
        except Exception:
            return ""
    # Fallback: pick a few interesting numeric or short-string fields.
    keep: list[str] = []
    for k, v in ev.items():
        if k in {"ts", "event", "session_num"}:
            continue
        if isinstance(v, bool):
            keep.append(f"{k}={v}")
        elif isinstance(v, (int, float)):
            keep.append(f"{k}={v}")
        elif isinstance(v, str) and len(v) <= 40:
            keep.append(f"{k}={v}")
        if len(keep) >= 3:
            break
    return " ".join(keep)


def _short_ts(iso_ts: str) -> str:
    """Return HH:MM:SS from an ISO timestamp; fall back to the raw value."""
    if not iso_ts or "T" not in iso_ts:
        return iso_ts
    time_part = iso_ts.split("T", 1)[1]
    for sep in ("+", "-", "Z"):
        # Strip TZ suffix while keeping HH:MM:SS intact. Skip leading
        # `-` inside the time itself (HH:MM:SS has no `-`), but a `-`
        # preceded by a digit in positions 8+ marks the TZ offset.
        if sep == "-":
            for i in range(8, len(time_part)):
                if time_part[i] == "-":
                    time_part = time_part[:i]
                    break
        elif sep in time_part:
            time_part = time_part.split(sep, 1)[0]
    if "." in time_part:
        time_part = time_part.split(".", 1)[0]
    return time_part


def _truncate(s: str, max_len: int) -> str:
    if max_len <= 0 or len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"
