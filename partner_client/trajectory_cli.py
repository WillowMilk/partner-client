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
        for e in reconstruct_turn(events, a.n):
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
