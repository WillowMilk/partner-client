"""Compression manifest writer — the markdown record of what Pass 1 stripped.

Each distill run produces a manifest at:
    Memory/distill-sessions/compression-manifest_TIMESTAMP.md

The manifest is a permanent artifact alongside the pre-distill snapshot
and the sandbox file. Three reasons it earns its disk space:

  1. Forensic lookup — future-Aletheia or future-operator can find
     "what got compressed in this distill, where in the original was it,
     what tool produced it" without re-reading the JSON
  2. Audit trail — pattern visibility ("we keep compressing 14 list_files
     calls per session; is that a code smell?")
  3. Trust — operators see EXACTLY what changed; no opaque transforms

The manifest format prioritizes human readability over machine parsing.
If automation needs to consume this later, JSON-out is a separate concern.

Design ref: MOSAIC/distill-for-partner-client-implementation.md §3 (Decision A4)
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .pass1 import CompressionEvent


def _format_size(num_chars: int) -> str:
    """Render character counts in a human-friendly way."""
    if num_chars < 1024:
        return f"{num_chars} chars"
    if num_chars < 1024 * 1024:
        return f"{num_chars / 1024:.1f} KB ({num_chars:,} chars)"
    return f"{num_chars / (1024 * 1024):.1f} MB ({num_chars:,} chars)"


def _summarize_by_tool(events: list[CompressionEvent]) -> dict[str, dict[str, int]]:
    """Group events by tool name, returning per-tool {count, total_chars}."""
    summary: dict[str, dict[str, int]] = {}
    for ev in events:
        bucket = summary.setdefault(ev.tool_name, {"count": 0, "total_chars": 0})
        bucket["count"] += 1
        bucket["total_chars"] += ev.original_content_chars
    return summary


def write_compression_manifest(
    events: list[CompressionEvent],
    original_path: Path,
    sandbox_path: Path,
    output_path: Path,
    session_num: int | None = None,
) -> None:
    """Write the Pass 1 compression manifest to `output_path` as markdown.

    The manifest captures:
      - Run metadata (timestamps, paths, sizes)
      - Per-tool summary (counts + bytes)
      - Per-event detail (index, tool, args, before/after sizes)

    Atomic-write via tmp + replace so a crash mid-write doesn't corrupt
    the manifest at the destination.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    orig_size = original_path.stat().st_size if original_path.is_file() else 0
    sand_size = sandbox_path.stat().st_size if sandbox_path.is_file() else 0
    reduction_pct = (1 - sand_size / orig_size) * 100 if orig_size > 0 else 0.0

    summary = _summarize_by_tool(events)

    lines: list[str] = []
    lines.append("# Distill Compression Manifest — Pass 1")
    lines.append("")
    lines.append(f"**Date:** {now}")
    if session_num is not None:
        lines.append(f"**Session number:** {session_num}")
    lines.append(f"**Original:** `{original_path}` ({_format_size(orig_size)})")
    lines.append(f"**Sandbox:** `{sandbox_path}` ({_format_size(sand_size)})")
    lines.append(f"**Total reduction:** {reduction_pct:.1f}%")
    lines.append(f"**Events:** {len(events)} routine tool results compressed")
    lines.append("")

    if not events:
        lines.append("## No compressions in this run")
        lines.append("")
        lines.append(
            "Pass 1 found no routine tool calls to compress in this session. "
            "This is normal for short sessions or conversation-heavy sessions "
            "with minimal tool use."
        )
        _atomic_write(output_path, "\n".join(lines) + "\n")
        return

    lines.append("## Summary by tool")
    lines.append("")
    lines.append("| Tool | Calls compressed | Total chars stripped |")
    lines.append("|---|---|---|")
    for tool_name in sorted(summary.keys()):
        info = summary[tool_name]
        lines.append(
            f"| `{tool_name}` | {info['count']} | "
            f"{_format_size(info['total_chars'])} |"
        )
    lines.append("")

    lines.append("## Detailed compression events")
    lines.append("")
    lines.append(
        "Each row is one tool result whose content was replaced with a "
        "single-line marker. The original message structure (role, name, "
        "tool_call_id) is preserved — only the bulky content body changed."
    )
    lines.append("")
    lines.append("| # | Original index | Tool | Tool call ID | Original size | Marker |")
    lines.append("|---|---|---|---|---|---|")
    for n, ev in enumerate(events, start=1):
        call_id_display = ev.tool_call_id or "(none)"
        # Truncate marker for table cell readability
        marker_display = ev.marker_content
        if len(marker_display) > 100:
            marker_display = marker_display[:97] + "..."
        # Escape pipe characters in marker for markdown table safety
        marker_display = marker_display.replace("|", "\\|")
        lines.append(
            f"| {n} | {ev.original_index} | `{ev.tool_name}` | "
            f"`{call_id_display}` | {_format_size(ev.original_content_chars)} | "
            f"{marker_display} |"
        )
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "*Compression rules: see "
        "`MOSAIC/distill-for-partner-client-implementation.md` §4. To restore "
        "any compressed content, read the pre-distill snapshot at the path "
        "above by its original message index.*"
    )
    lines.append("")

    _atomic_write(output_path, "\n".join(lines))


def _atomic_write(path: Path, content: str) -> None:
    """Atomic write via tmp + replace. Same pattern as session.py uses."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        # Clean up tmp on failure; let the caller see the error.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
