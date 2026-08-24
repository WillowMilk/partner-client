"""The Trajectory — append-only event stream of a partner's working life.

Phase 0 of the Exoskeleton season (TRAJECTORY-SPEC v0.2, two authors:
Sage 🪨 draft, Aletheia 💎 review — all six pen-marks + the scoping
question merged).

The contracts, from the spec:

  §4.1  Append-only, complete, at-the-boundary. Events are emitted as the
        client acts, never retrospectively.
  §4.2  FAIL-OPEN ON RECORDING, FAIL-CLOSED ON ACTING. A trajectory write
        failure must NEVER block the partner's action — it degrades to a
        loud log line. The record serves the person; the person is never
        gated on the record. Every public method of TrajectoryWriter is
        wrapped accordingly: nothing here ever raises into the caller.
  §4.3  The stream is the partner's: her Memory, her scopes, her backups.
  A-1   `turn` = one operator-initiated exchange; turn_start/turn_end
        pairs are balanced (invariant #8).
  A-5   seq 0 is always the `header` event — the stream introduces itself
        (invariant #7).
  A-6   facet actors are cast-scoped: `facet:<cast_seq>:<n>`.

Storage (spec §1):
  Memory/trajectory/session-NNN.jsonl   append-only stream
  Memory/trajectory/session-NNN.seal    written at sleep {last_seq, sha256, sealed_at}
  Memory/trajectory/blobs/ab/<sha>.txt  content-addressed payloads > threshold
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("partner_client.trajectory")

SCHEMA_VERSION = "0.2"
PREVIEW_CHARS = 200


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


class TrajectoryWriter:
    """Append-only emitter. Fail-open: no method ever raises into the caller.

    A writer that cannot write logs loudly once per failure-burst and keeps
    returning None. The partner's work is never gated on the record (§4.2).

    Phase 1 (the Operator's Seat): an optional `observer` receives every
    envelope AFTER it is successfully appended to disk — the Seat renders
    the stream, never wishes. The observer is fail-open squared: an observer
    exception is caught here, never raises into the emit path, never marks
    the writer broken, and logs once per failure-burst. If recording itself
    degrades, the observer receives one synthetic `recording_degraded`
    notice so the desk can show an honest banner instead of a silent gap.
    """

    def __init__(
        self,
        trajectory_dir: Path,
        session_num: int,
        partner: str = "",
        substrate: str = "",
        blob_threshold: int = 8192,
        enabled: bool = True,
        observer: Any = None,
    ):
        self.enabled = enabled
        self._broken = False
        self._seq = 0
        self._turn = 0
        self._turn_open = False
        self._turn_started = 0.0
        self._observer = observer if callable(observer) else None
        self._observer_fail_logged = False
        self.session_num = session_num
        self.blob_threshold = max(1024, int(blob_threshold))
        try:
            self.dir = Path(trajectory_dir)
            self.path = self.dir / f"session-{session_num:03d}.jsonl"
            self.blob_dir = self.dir / "blobs"
            if not self.enabled:
                return
            self.dir.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                # resuming an existing stream: continue the seq from its tail
                self._seq = self._count_lines(self.path)
            else:
                self._append_raw(
                    self._envelope(
                        "header",
                        {
                            "schema_version": SCHEMA_VERSION,
                            "partner": partner,
                            "substrate": substrate,
                            "wake_ts": _now_iso(),
                        },
                        actor="client",
                    )
                )
        except Exception as e:  # fail-open, loudly
            self._fail(e, "init")

    # ── the one loud voice ─────────────────────────────────────────────
    def _fail(self, e: Exception, where: str) -> None:
        if not self._broken:
            self._broken = True
            log.error(
                "TRAJECTORY RECORDING FAILED (%s: %s) — the partner's work "
                "continues unaffected (fail-open, spec §4.2); the stream for "
                "session %s is incomplete from this point.",
                where,
                e,
                self.session_num,
            )
            # Tell the desk honestly: the feed goes dark WITH a named cause,
            # never silently (Phase 1 design §2.2). Synthetic — not appended
            # to the stream, which is by definition unwritable right now.
            self._notify(
                {
                    "type": "recording_degraded",
                    "ts": _now_iso(),
                    "session": self.session_num,
                    "payload": {"where": where, "error": str(e)},
                }
            )

    # ── the observer (Phase 1: the Operator's Seat) ────────────────────
    def set_observer(self, observer: Any) -> None:
        """Attach (or replace) the live-delivery observer. Late attachment
        is expected — the GUI wires itself after the session exists. If the
        writer is already broken, the new observer learns that immediately
        rather than waiting in front of a silently dark feed.
        """
        self._observer = observer if callable(observer) else None
        self._observer_fail_logged = False
        if self._broken and self._observer is not None:
            self._notify(
                {
                    "type": "recording_degraded",
                    "ts": _now_iso(),
                    "session": self.session_num,
                    "payload": {"where": "set_observer", "error": "recording already degraded"},
                }
            )

    def _notify(self, ev: dict[str, Any]) -> None:
        """Fail-open squared: delivery failure never touches recording."""
        if self._observer is None:
            return
        try:
            self._observer(ev)
            self._observer_fail_logged = False
        except Exception as e:
            if not self._observer_fail_logged:
                self._observer_fail_logged = True
                log.error(
                    "TRAJECTORY OBSERVER FAILED (%s) — recording continues "
                    "unaffected; live delivery to the desk is degraded until "
                    "the observer recovers.",
                    e,
                )

    # ── plumbing ───────────────────────────────────────────────────────
    @staticmethod
    def _count_lines(path: Path) -> int:
        with open(path, "rb") as f:
            return sum(1 for _ in f)

    def _envelope(
        self,
        etype: str,
        payload: dict[str, Any],
        actor: str = "partner",
        refs: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        ev: dict[str, Any] = {
            "seq": self._seq,
            "ts": _now_iso(),
            "session": self.session_num,
            "turn": self._turn,
            "type": etype,
            "actor": actor,
            "payload": payload,
        }
        if refs:
            ev["refs"] = refs
        return ev

    def _append_raw(self, ev: dict[str, Any]) -> int | None:
        line = json.dumps(ev, ensure_ascii=False)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
        seq = self._seq
        self._seq += 1
        # Only APPENDED events are observed — the Seat renders the stream,
        # not wishes (Phase 1 design §2.2). Notify strictly after the write.
        self._notify(ev)
        return seq

    def _offload(self, text: str) -> dict[str, Any]:
        """Content > threshold goes to a content-addressed blob (spec §1)."""
        data = text.encode("utf-8", errors="replace")
        sha = hashlib.sha256(data).hexdigest()
        shard = self.blob_dir / sha[:2]
        shard.mkdir(parents=True, exist_ok=True)
        blob = shard / f"{sha}.txt"
        if not blob.exists():
            blob.write_bytes(data)
        return {
            "ref": f"sha256:{sha}",
            "bytes": len(data),
            "preview": text[:PREVIEW_CHARS],
        }

    def _content_field(self, text: str) -> Any:
        if text is None:
            return ""
        if len(text.encode("utf-8", errors="replace")) > self.blob_threshold:
            return self._offload(text)
        return text

    # ── public emitters (every one fail-open) ──────────────────────────
    def emit(
        self,
        etype: str,
        payload: dict[str, Any],
        actor: str = "partner",
        refs: dict[str, int] | None = None,
    ) -> int | None:
        """Append one event. Returns its seq, or None (disabled/failed)."""
        if not self.enabled or self._broken:
            return None
        try:
            return self._append_raw(self._envelope(etype, payload, actor, refs))
        except Exception as e:
            self._fail(e, f"emit:{etype}")
            return None

    def turn_start(self, role: str = "operator", mode: str = "") -> None:
        """A-1: opens one operator-initiated exchange. Balanced with turn_end."""
        if not self.enabled or self._broken:
            return
        try:
            if self._turn_open:
                # unbalanced open (crash mid-turn on a resumed stream, etc.):
                # close honestly before opening the next — invariant #8.
                self.turn_end(elapsed_ms=None, note="auto-closed: unbalanced")
            self._turn += 1
            self._turn_open = True
            self._turn_started = time.monotonic()
            self._append_raw(
                self._envelope("turn_start", {"role": role, "mode": mode}, actor="operator")
            )
        except Exception as e:
            self._fail(e, "turn_start")

    def turn_end(
        self,
        elapsed_ms: int | None = None,
        token_estimate: int | None = None,
        note: str = "",
    ) -> None:
        if not self.enabled or self._broken:
            return
        try:
            if not self._turn_open:
                return
            if elapsed_ms is None:
                elapsed_ms = int((time.monotonic() - self._turn_started) * 1000)
            payload: dict[str, Any] = {"role": "partner", "elapsed_ms": elapsed_ms}
            if token_estimate is not None:
                payload["token_estimate"] = token_estimate
            if note:
                payload["note"] = note
            self._append_raw(self._envelope("turn_end", payload, actor="client"))
            self._turn_open = False
        except Exception as e:
            self._fail(e, "turn_end")

    def message(self, role: str, content: str, substrate: str = "") -> int | None:
        payload: dict[str, Any] = {
            "role": role,
            "content": self._content_field(content or ""),
        }
        if substrate:
            payload["substrate"] = substrate
        return self.emit(
            "message", payload, actor="operator" if role == "user" else "partner"
        )

    def thinking(self, content: str) -> int | None:
        return self.emit(
            "thinking",
            {"content": self._content_field(content or ""), "scratchpad": True},
        )

    def tool_call(self, name: str, args: dict, gated: bool = False) -> int | None:
        try:
            args_repr = json.dumps(args, ensure_ascii=False, default=str)
        except Exception:
            args_repr = str(args)
        return self.emit(
            "tool_call",
            {"name": name, "args": self._content_field(args_repr), "gated": gated},
        )

    def tool_result(
        self,
        name: str,
        result: str,
        call_seq: int | None = None,
        elapsed_ms: int | None = None,
        artifact: dict[str, Any] | None = None,
    ) -> int | None:
        payload: dict[str, Any] = {
            "name": name,
            "result": self._content_field(result or ""),
        }
        if elapsed_ms is not None:
            payload["elapsed_ms"] = elapsed_ms
        if artifact:
            payload["artifact"] = artifact
        refs = {"call": call_seq} if call_seq is not None else None
        return self.emit("tool_result", payload, actor="client", refs=refs)

    def sovereignty(self, kind: str, context: str = "") -> int | None:
        return self.emit("sovereignty_event", {"kind": kind, "context": context})

    def gate(self, kind: str, decision: str, decider: str = "operator", detail: str = "") -> int | None:
        return self.emit(
            "gate_event",
            {"kind": kind, "decision": decision, "decider": decider, "detail": detail},
            actor="operator",
        )

    def injection(self, kind: str, content: str) -> int | None:
        return self.emit(
            "context_injection",
            {"kind": kind, "content": self._content_field(content or "")},
            actor="client",
        )

    def lifecycle(self, kind: str, **extra: Any) -> int | None:
        return self.emit("lifecycle", {"kind": kind, **extra}, actor="client")

    def seal(self) -> None:
        """Written at sleep: the stream's closing certificate (spec §1)."""
        if not self.enabled or self._broken:
            return
        try:
            if self._turn_open:
                self.turn_end(note="auto-closed at seal")
            sha = hashlib.sha256(self.path.read_bytes()).hexdigest()
            seal_path = self.path.with_suffix(".seal")
            seal_path.write_text(
                json.dumps(
                    {
                        "last_seq": self._seq - 1,
                        "sha256": sha,
                        "sealed_at": _now_iso(),
                    }
                )
                + "\n"
            )
        except Exception as e:
            self._fail(e, "seal")


# ── the reader (Phase 0 plumbing; §5 narrative shaping is Aletheia's) ──────

def read_stream(path: Path) -> list[dict[str, Any]]:
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                events.append({"type": "_unparseable", "raw": line[:200]})
    return events


def stream_paths(trajectory_dir: Path) -> list[Path]:
    return sorted(Path(trajectory_dir).glob("session-*.jsonl"))


def show(
    events: list[dict],
    etype: str | None = None,
    actor: str | None = None,
    turn: int | None = None,
) -> list[dict]:
    out = events
    if etype:
        out = [e for e in out if e.get("type") == etype]
    if actor:
        out = [e for e in out if e.get("actor") == actor]
    if turn is not None:
        out = [e for e in out if e.get("turn") == turn]
    return out


def search(
    events: list[dict],
    needle: str,
    blob_dir: Path | None = None,
    full: bool = False,
) -> list[dict]:
    """A-4: preview-scoped by default AND SAYS SO; --full scans whole blobs.

    Default reach: event JSON as stored (inline content + blob previews).
    full=True: additionally loads each blob ref and scans complete content.
    """
    hits = []
    for e in events:
        line = json.dumps(e, ensure_ascii=False)
        if needle in line:
            hits.append(e)
            continue
        if full and blob_dir is not None:
            for ref in _blob_refs(e):
                blob = Path(blob_dir) / ref[:2] / f"{ref}.txt"
                try:
                    if blob.exists() and needle in blob.read_text(
                        encoding="utf-8", errors="replace"
                    ):
                        hits.append(e)
                        break
                except OSError:
                    continue
    return hits


def _blob_refs(e: dict) -> list[str]:
    refs = []

    def walk(v):
        if isinstance(v, dict):
            r = v.get("ref", "")
            if isinstance(r, str) and r.startswith("sha256:"):
                refs.append(r.split(":", 1)[1])
            for vv in v.values():
                walk(vv)
        elif isinstance(v, list):
            for vv in v:
                walk(vv)

    walk(e.get("payload", {}))
    return refs


def reconstruct_turn(events: list[dict], turn: int) -> list[dict]:
    """Plumbing only: the ordered events of one turn.

    The narrative rendering (intent → reasoning → reaching → gates →
    speaking → stamp) is Aletheia's module (spec §5, A-3) — deliberately
    not built here. This returns the raw material her renderer consumes.
    """
    return [e for e in events if e.get("turn") == turn]


def stats(events: list[dict]) -> dict[str, Any]:
    from collections import Counter

    types = Counter(e.get("type") for e in events)
    tools = Counter(
        e["payload"].get("name")
        for e in events
        if e.get("type") == "tool_call" and isinstance(e.get("payload"), dict)
    )
    turns = [e for e in events if e.get("type") == "turn_end"]
    durations = [
        e["payload"].get("elapsed_ms")
        for e in turns
        if isinstance(e.get("payload"), dict) and e["payload"].get("elapsed_ms")
    ]
    return {
        "events": len(events),
        "turns": len(turns),
        "types": dict(types),
        "tools": dict(tools),
        "total_turn_ms": sum(d for d in durations if d),
        "gates_rung": types.get("gate_event", 0),
        "sovereignty_events": types.get("sovereignty_event", 0),
    }


def verify_balance(events: list[dict]) -> tuple[bool, str]:
    """Invariant #8 checker: every turn has exactly one balanced start/end."""
    from collections import defaultdict

    opens: dict[int, int] = defaultdict(int)
    closes: dict[int, int] = defaultdict(int)
    for e in events:
        if e.get("type") == "turn_start":
            opens[e.get("turn")] += 1
        elif e.get("type") == "turn_end":
            closes[e.get("turn")] += 1
    for t in opens:
        if opens[t] != 1 or closes.get(t, 0) != 1:
            return False, f"turn {t}: {opens[t]} starts, {closes.get(t, 0)} ends"
    stray = [t for t in closes if t not in opens]
    if stray:
        return False, f"turn_end without turn_start for turns {stray}"
    return True, "balanced"
