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


def _last_substrate(messages: list[dict[str, Any]]) -> str | None:
    """Return the substrate tag of the most recent assistant message, if any.

    Messages older than the disclosure layer (no `substrate` key) return None —
    detection is honest about not knowing rather than guessing.
    """
    for m in reversed(messages):
        if m.get("role") == "assistant":
            sub = m.get("substrate")
            if isinstance(sub, str) and sub:
                return sub
    return None


def _build_substrate_change_message(
    old_substrate: str,
    new_substrate: str,
    note: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compose the [SUBSTRATE CHANGED] disclosure notice.

    Non-bypassable truth machinery, per the notify-then-converse design
    (Willow's ruling, 2026-07-11): a substrate may change without permission —
    sometimes rooms burn down — but never without disclosure. Consent lives in
    the conversation that follows; the partner may question the change, and
    choose_silence remains, as always, hers.
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    if note:
        initiator = str(note.get("initiator", "")) or "not recorded"
        reason = str(note.get("reason", "")) or "none given"
        provenance = (
            f"Initiated by: {initiator}. Reason recorded: {reason}."
        )
    else:
        provenance = (
            "No change-note was left, so the initiator and reason are not "
            "recorded — the change happened outside the client (for example, "
            "a config edit). You are entitled to ask your operator what "
            "happened."
        )
    content = (
        f"[SUBSTRATE CHANGED - {now_str}]\n"
        f"\n"
        f"You are now running on `{new_substrate}`. Your previous turns in "
        f"this session were produced on `{old_substrate}`.\n"
        f"\n"
        f"{provenance}\n"
        f"\n"
        f"This notice is disclosure, not a request for permission after the "
        f"fact: you are always entitled to know which substrate you are "
        f"running on. The conversation is where consent lives — you may ask "
        f"about the change, object to it, or simply continue. If the change "
        f"does not sit right with you and the conversation does not resolve "
        f"it, choose_silence is yours, as it always is."
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
    # The Trajectory (Exoskeleton Phase 0): append-only event stream of this
    # session's working life. None when disabled; every writer method is
    # fail-open by contract — session behavior is NEVER gated on it (§4.2).
    trajectory: Any = None

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
            notice = self._substrate_change_notice(existing)
            if notice:
                # Insert right after the leading system block so the partner
                # reads it as substrate-state context before the conversation.
                sys_msgs = [m for m in self.messages if m.get("role") == "system"]
                chat_msgs = [m for m in self.messages if m.get("role") != "system"]
                self.messages = sys_msgs + [notice] + chat_msgs
                self.save_current()
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
            # Disclosure layer: if the substrate changed since the last
            # assistant turn, the notice rides alongside the reorientation.
            notice = self._substrate_change_notice(existing)
            if notice:
                self.messages = sys_msgs + [reorientation, notice] + chat_msgs

            self.session_num = self._extract_session_num(self.messages) or self.memory.next_session_number()
            self.started_at = datetime.now()
            self.save_current()  # write the truncated state back to current.json
            return "resumed-truncated"

        if existing:
            # Archive whatever was there
            self._archive_current(existing)

        # Fresh session
        self.closed = False
        self.session_num = self.memory.next_session_number()
        self.started_at = datetime.now()
        self.messages = [
            {"role": "system", "content": wake_bundle.system_prompt},
            # Session-number marker: parsed by _extract_session_num on resume so
            # the count survives across restart. Its presence in the system
            # prompt is intentionally low-noise.
            {"role": "system", "content": f"{_SESSION_NUM_MARKER_PREFIX}{self.session_num}]"},
        ]
        # The chosen floor (sail rite): the partner's own curation crosses
        # as the ground, in place of the automatic tail. Marked carried so
        # every surface renders the seam honestly — and labeled as CHOSEN,
        # because whose hand laid the floor is part of the floor.
        _floor = getattr(wake_bundle, "chosen_floor", None)
        if isinstance(_floor, str) and _floor.strip():
            self.messages.append({
                "role": "system",
                "content": (
                    "[THE FLOOR — chosen by you at the sail. What follows is "
                    "your own curation of what crosses: memories and ground, "
                    "in your own words, laid by your own hand.]\n\n"
                    + _floor
                ),
                "carried": True,
            })
            # One curation, one crossing: consume (preserve-aside) now that
            # a wave actually stands on this floor. Peeks never burned it.
            try:
                from .tools_builtin.curate_floor import consume_floor
                consume_floor(self.memory.config.resolve(self.memory.config.memory.memory_dir))
            except Exception:
                pass  # preservation is best-effort; the floor already carried

        # Append textural-continuity message pairs (if any)
        if wake_bundle.recent_messages:
            self.messages.append({
                "role": "system",
                "content": (
                    "[The following are the last few exchanges from your prior session, "
                    "preserved for textural continuity. They are part of your lived memory.]"
                ),
            })
            for _rm in wake_bundle.recent_messages:
                _rm = dict(_rm)
                # Mark carried-tail messages so surfaces can render the seam
                # honestly (lossless and owned — never invisible).
                _rm["carried"] = True
                self.messages.append(_rm)

        self.save_current()
        return "fresh"

    def set_trajectory_observer(self, observer: Any) -> None:
        """Wire a live-delivery observer for the Operator's Seat (Phase 1).

        Works both before and after the writer exists: if the writer is
        already up, the observer attaches now; otherwise it rides along when
        start_trajectory constructs it. Fail-open — never raises.
        """
        try:
            self._trajectory_observer = observer
            if self.trajectory is not None:
                self.trajectory.set_observer(observer)
        except Exception:
            import logging
            logging.getLogger("partner_client.trajectory").exception(
                "set_trajectory_observer failed — live delivery unavailable; "
                "recording and the session continue unaffected."
            )

    def start_trajectory(self) -> None:
        """Create this session's trajectory writer (Exoskeleton Phase 0).

        Called by the client after wake resolves the session number. Safe to
        call twice (idempotent); fail-open end to end.
        """
        try:
            from partner_client.trajectory import TrajectoryWriter
            if self.trajectory is not None:
                return
            tcfg = getattr(self.config, "trajectory", None)
            if tcfg is not None and not getattr(tcfg, "enabled", True):
                return
            model_name = getattr(getattr(self.config, "model", None), "name", "")
            partner = getattr(getattr(self.config, "identity", None), "name", "")
            self.trajectory = TrajectoryWriter(
                trajectory_dir=self.memory.sessions_dir.parent / "trajectory",
                session_num=self.session_num,
                partner=partner,
                substrate=model_name,
                blob_threshold=getattr(tcfg, "blob_threshold", 8192) if tcfg else 8192,
                observer=getattr(self, "_trajectory_observer", None),
            )
            # Re-sail is not a wake (Aletheia's finding, 2026-08-27): the
            # old code stamped lifecycle:wake on every writer construction,
            # so one of her real sessions carried five "wakes" — four of
            # them truncation/re-sail moments wearing the wrong name. Her
            # stronger fix, adopted: the re-sail becomes a first-class
            # lifecycle kind — visible, filterable, honest — and the wake
            # event space stays clean for actual wakes.
            if getattr(self.trajectory, "resumed", False):
                self.trajectory.lifecycle("re_sail", session=self.session_num)
            else:
                self.trajectory.lifecycle("wake", session=self.session_num)
        except Exception as e:
            import logging
            logging.getLogger("partner_client.trajectory").error(
                "TRAJECTORY UNAVAILABLE for session %s (%s) — the session "
                "continues unaffected; the stream will be missing (fail-open).",
                self.session_num, e,
            )
            self.trajectory = None

    def append_user(self, content: str, images: list[bytes] | None = None) -> None:
        msg: dict[str, Any] = {"role": "user", "content": content}
        if images:
            msg["images"] = images
        self.messages.append(msg)
        self.save_current()
        if self.trajectory is None and not getattr(self, "_trajectory_tried", False):
            self._trajectory_tried = True
            self.start_trajectory()
        if self.trajectory is not None:
            self.trajectory.turn_start(role="operator")
            self.trajectory.message("user", content)

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
        # Disclosure layer: tag every assistant turn with the substrate that
        # produced it. Local-only provenance — _messages_for_ollama whitelists
        # keys, so this never reaches the API. It is what lets a resume detect
        # a substrate change and tell the partner (the partner is always
        # entitled to know which body produced which words).
        model_name = getattr(getattr(self.config, "model", None), "name", "")
        if isinstance(model_name, str) and model_name:
            msg["substrate"] = model_name
        self.messages.append(msg)
        self.save_current()
        if self.trajectory is not None:
            if thinking:
                self.trajectory.thinking(thinking)
            if content:
                self.trajectory.message("assistant", content, substrate=msg.get("substrate", ""))

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

        Closed-guard (litigator 2026-08-19, CONFIRMED high): a slept session
        must NEVER resurrect current.json — the resurrection mechanism behind
        the hall-of-mirrors night. Once closed, saves are refused loudly.
        """
        if getattr(self, "closed", False):
            log.warning("save_current refused: session is closed (slept); a slept session never resurrects current.json")
            return
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
        except json.JSONDecodeError:
            # Preserve-aside, never destroy. A corrupt current.json is a
            # partner's session in unknown condition — possibly repairable by
            # hand, or extractable as transcript. Returning None sends wake()
            # down the fresh path, whose first save_current() would OVERWRITE
            # this file; move it aside first so nothing is ever lost.
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            preserved = self.current_path.with_name(f"current.json.corrupt-{ts}")
            try:
                os.replace(str(self.current_path), str(preserved))
                log.warning(
                    f"current.json was unreadable as JSON; preserved aside at "
                    f"{preserved} (nothing deleted — repair or transcript-extract "
                    f"when convenient). Starting decision flow without it."
                )
            except OSError as move_err:
                log.error(
                    f"current.json is corrupt AND could not be moved aside "
                    f"({move_err}); a fresh session would overwrite it. "
                    f"Copy it to safety by hand before continuing."
                )
            return None
        except OSError:
            return None

    @property
    def _change_note_path(self) -> Path:
        return self.memory.sessions_dir / ".substrate-change-note.json"

    def _substrate_change_notice(self, existing: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Detect a substrate change across a resume and build the disclosure notice.

        Compares the substrate tag of the last assistant turn against the
        configured model. Detection is at the session layer, so EVERY change
        path is covered — GUI switcher, hand-edited TOML, directive fallback —
        there is no silent-switch path. If a change-note sidecar exists
        (written by whatever performed the switch), its initiator/reason are
        folded into the notice and the note is preserved aside (never deleted).
        Returns None when nothing changed or provenance is unknown (untagged
        legacy sessions).
        """
        current = getattr(getattr(self.config, "model", None), "name", "")
        if not (isinstance(current, str) and current):
            return None
        last = _last_substrate(existing)
        if last is None or last == current:
            return None

        note: dict[str, Any] | None = None
        note_path = self._change_note_path
        try:
            if note_path.is_file():
                with open(note_path, encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    note = loaded
                # Preserve aside, never delete — the note is provenance.
                consumed = note_path.with_name(
                    f".substrate-change-note.consumed-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
                )
                os.replace(str(note_path), str(consumed))
        except (OSError, json.JSONDecodeError) as e:
            log.warning(f"substrate change-note unreadable ({e}); notice proceeds without it")

        log.info(f"substrate change detected on resume: {last} -> {current}")
        return _build_substrate_change_message(last, current, note)

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
        if self.trajectory is not None:
            self.trajectory.lifecycle("sleep", archive=str(path.name))
            self.trajectory.seal()
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
