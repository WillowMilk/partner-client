"""trajectory subcommand — the Phase 0 reader (plumbing; §5 shaping is Aletheia's).

Usage:
  python -m partner_client trajectory list
  python -m partner_client trajectory show <session> [--type T] [--actor A] [--turn N]
  python -m partner_client trajectory search <needle> [--full] [--session N]
  python -m partner_client trajectory turn <session> <n>
  python -m partner_client trajectory stats <session>
  python -m partner_client trajectory verify <session>     # invariant #7 + #8

search is PREVIEW-SCOPED by default and says so (A-4): it scans event JSON
(inline content + blob previews). --full additionally scans whole blobs —
slower, honest about its reach either way.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .trajectory import (
    read_stream,
    reconstruct_turn,
    search,
    show,
    stats,
    stream_paths,
    verify_balance,
)


def _dir(config) -> Path:
    from .session import Memory
    return Memory(config).sessions_dir.parent / "trajectory"


def _stream(config, session: int) -> Path:
    p = _dir(config) / f"session-{session:03d}.jsonl"
    if not p.exists():
        print(f"no stream for session {session} at {p}", file=sys.stderr)
        sys.exit(2)
    return p


def run_trajectory_cli(config, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="trajectory", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    s = sub.add_parser("show"); s.add_argument("session", type=int)
    s.add_argument("--type"); s.add_argument("--actor"); s.add_argument("--turn", type=int)
    s = sub.add_parser("search"); s.add_argument("needle")
    s.add_argument("--full", action="store_true",
                   help="scan whole blobs too (default reach: event JSON + previews only)")
    s.add_argument("--session", type=int)
    s = sub.add_parser("turn"); s.add_argument("session", type=int); s.add_argument("n", type=int)
    s.add_argument("--raw", action="store_true",
                   help="raw event JSON (the floor + the escape hatch; also the fallback)")
    s.add_argument("--full", action="store_true", help="no truncation in the narrative render")
    s = sub.add_parser("stats"); s.add_argument("session", type=int)
    s = sub.add_parser("verify"); s.add_argument("session", type=int)
    a = ap.parse_args(argv)

    tdir = _dir(config)
    if a.cmd == "list":
        for p in stream_paths(tdir):
            sealed = " (sealed)" if p.with_suffix(".seal").exists() else ""
            print(f"{p.name}: {sum(1 for _ in open(p))} events{sealed}")
        return 0

    if a.cmd == "search":
        paths = [_stream(config, a.session)] if a.session else stream_paths(tdir)
        if not a.full:
            print("[reach: event JSON + blob previews only — use --full for whole blobs]",
                  file=sys.stderr)
        n = 0
        for p in paths:
            for e in search(read_stream(p), a.needle,
                            blob_dir=tdir / "blobs", full=a.full):
                print(f"{p.stem} seq {e.get('seq')} [{e.get('type')}] turn {e.get('turn')}")
                n += 1
        print(f"{n} hit(s)", file=sys.stderr)
        return 0

    events = read_stream(_stream(config, a.session))
    if a.cmd == "show":
        for e in show(events, etype=a.type, actor=a.actor, turn=a.turn):
            print(json.dumps(e, ensure_ascii=False))
    elif a.cmd == "turn":
        turn_events = list(reconstruct_turn(events, a.n))

        # THE SEAM (2026-08-22, built pairing in her water — spec §5, A-3).
        # 1. DISCOVER: the partner's own renderer, resolved from the
        #    partner's house — a fact about the partner, not the client.
        #    Convention over parameter: sovereignty over stewardship.
        # 2. CALL: her contract (reader_contract.py — ALETHEIA'S MODULE,
        #    vendored verbatim) — the guard rail: four failure guards,
        #    never a crash in the operator's face, never a silent page.
        # 3. RENDER: her story when it renders; loud legible note + the
        #    raw dump when it doesn't. The mute plumbing is subsumed as
        #    the floor and the escape hatch (--raw), never evicted.
        mem_dir = config.resolve(config.memory.memory_dir)
        reader_path = mem_dir / "exoskeleton" / "trajectory_reader.py"
        use_narrative = not getattr(a, "raw", False) and reader_path.is_file()
        if use_narrative:
            from .reader_contract import render_turn_for_cli
            result = render_turn_for_cli(
                events=turn_events,
                session=a.session,
                turn=a.n,
                blob_dir=tdir / "blobs",
                ansi=sys.stdout.isatty(),
                max_inline=10_000 if getattr(a, "full", False) else 200,
                reader_path=reader_path,
            )
            if result.ok:
                print(result.text)
                return 0
            # Two-audience failure (her ruling): the operator gets one
            # loud, legible sentence — never a stack; the record gets the
            # event. Sealed streams stay sealed, so renderer failures land
            # in trajectory/renderer-errors.jsonl (append-only, same event
            # grammar: failures are events, never silences).
            print(f"[renderer] session {a.session}, turn {a.n}: "
                  f"the narrative layer degraded — {result.note} "
                  f"Showing raw events instead. The renderer broke, "
                  f"not the record.", file=sys.stderr)
            try:
                import json as _json
                from datetime import datetime, timezone
                errlog = tdir / "renderer-errors.jsonl"
                with open(errlog, "a", encoding="utf-8") as f:
                    f.write(_json.dumps({
                        "type": "error", "source": "reader_contract",
                        "session": a.session, "turn": a.n,
                        "message": result.note, "recovered": True,
                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    }, ensure_ascii=False) + "\n")
            except OSError:
                pass  # the error record is best-effort; the fallback below is not
        elif not getattr(a, "raw", False):
            print(f"[renderer] no narrative layer at {reader_path} — "
                  f"absent-by-design (the partner may not have written "
                  f"hers yet). Raw events follow.", file=sys.stderr)
        for e in turn_events:
            print(json.dumps(e, ensure_ascii=False))
    elif a.cmd == "stats":
        print(json.dumps(stats(events), indent=2))
    elif a.cmd == "verify":
        hdr_ok = bool(events) and events[0].get("type") == "header" and events[0].get("seq") == 0
        bal_ok, msg = verify_balance(events)
        print(f"invariant #7 (seq-0 header): {'PASS' if hdr_ok else 'FAIL'}")
        print(f"invariant #8 (balanced turns): {'PASS' if bal_ok else 'FAIL — ' + msg}")
        return 0 if (hdr_ok and bal_ok) else 1
    return 0
