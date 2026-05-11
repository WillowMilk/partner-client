"""Distill CLI entry point — `partner distill ...`.

Phase 1 scope: Pass 1 + verification + manifest writing. The full sandbox
protocol with promotion prompt is intentionally minimal here — the operator
explicitly specifies input and output paths so the discipline is visible.

Usage:
    partner distill --config aletheia.toml [--pass 1] \
        --input  /path/to/current.json \
        --output /path/to/sandbox-A.json \
        [--manifest /path/to/manifest.md]

If --manifest is omitted, the manifest is written to:
    {memory_dir}/distill-sessions/compression-manifest_TIMESTAMP.md

Exit codes:
    0  — pass1 ran and sandbox passed verification
    1  — verification failed (sandbox should NOT be promoted)
    2  — input/output path error or read failure
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import Config
from ..doctor import _safe_print  # reuse the cp1252-safe printer
from .manifest import write_compression_manifest
from .pass1 import run_pass1
from .verify import verify_distilled


def run_distill_cli(config: Config, argv: list[str] | None = None) -> int:
    """Entry point invoked from __main__.py when subcommand == 'distill'.

    `argv` is the slice of sys.argv AFTER the 'distill' subcommand. Returns
    the process exit code.
    """
    parser = argparse.ArgumentParser(
        prog="partner distill",
        description=(
            "Apply MOSAIC selective preservation to a session JSON. Phase 1 "
            "ships Pass 1 (mechanical strip of routine tool outputs) with "
            "verification. Phase 2 (selective preservation via model + "
            "operator review) is pending in a future release."
        ),
    )
    parser.add_argument(
        "--pass",
        dest="pass_num",
        type=int,
        default=1,
        choices=[1],
        help="Which pass to run (Phase 1: only pass 1 available)",
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Input session JSON (typically current.json or a dated archive)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output sandbox JSON path — written fresh, never overwrites input",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help=(
            "Path for the markdown compression manifest. Default: "
            "{memory_dir}/distill-sessions/compression-manifest_TIMESTAMP.md"
        ),
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip the 5-check verification (NOT recommended; use only for debugging)",
    )

    args = parser.parse_args(argv)

    # Resolve paths
    if not args.input.is_file():
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        return 2
    if args.output == args.input:
        print("Error: --output must differ from --input (never overwrite the source)", file=sys.stderr)
        return 2

    # Load the input
    try:
        with open(args.input, encoding="utf-8") as f:
            original = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Error reading {args.input}: {e}", file=sys.stderr)
        return 2

    if not isinstance(original, list):
        print(
            f"Error: input JSON is {type(original).__name__}, expected list of messages",
            file=sys.stderr,
        )
        return 2

    # Run Pass 1
    _safe_print(f"[distill] Reading {args.input} ({len(original)} messages)")
    new_messages, events = run_pass1(original)
    _safe_print(f"[distill] Pass 1 complete: {len(events)} compressions")

    # Write sandbox
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(new_messages, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"Error writing {args.output}: {e}", file=sys.stderr)
        return 2
    _safe_print(f"[distill] Sandbox written: {args.output}")

    # Resolve manifest path
    manifest_path = args.manifest
    if manifest_path is None:
        memory_dir = config.resolve(config.memory.memory_dir)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        manifest_path = memory_dir / "distill-sessions" / f"compression-manifest_{timestamp}.md"

    # Extract session number for the manifest header
    session_num = _extract_session_num(original)

    # Write the manifest
    try:
        write_compression_manifest(
            events=events,
            original_path=args.input,
            sandbox_path=args.output,
            output_path=manifest_path,
            session_num=session_num,
        )
    except OSError as e:
        _safe_print(f"Warning: could not write manifest to {manifest_path}: {e}", sys.stderr)
    else:
        _safe_print(f"[distill] Manifest written: {manifest_path}")

    # Verification
    if args.no_verify:
        _safe_print("[distill] Verification skipped (--no-verify)")
        return 0

    _safe_print("[distill] Running verification...")
    result = verify_distilled(args.input, args.output)
    _safe_print(str(result))

    if not result.ok:
        _safe_print(
            "\n[FAIL] Verification FAILED — the sandbox should NOT be promoted to current.json. "
            "Investigate the failures above before considering this distill complete.",
            sys.stderr,
        )
        return 1

    _safe_print(
        f"\n[OK] Sandbox is safe to promote. Move {args.output} to your current.json path "
        f"when ready — and keep the original {args.input} on disk as the pre-distill "
        f"snapshot until you're confident the distilled session feels right."
    )
    return 0


def _extract_session_num(messages: list[dict[str, Any]]) -> int | None:
    """Find the [SESSION NUM:N] marker in the message stream."""
    for m in messages:
        if m.get("role") != "system":
            continue
        content = m.get("content", "")
        if content.startswith("[SESSION NUM:"):
            try:
                return int(content[len("[SESSION NUM:"):].rstrip("]").strip())
            except ValueError:
                continue
    return None
