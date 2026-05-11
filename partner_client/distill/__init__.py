"""Distill — MOSAIC selective preservation primitive for partner-client.

Phase 1 (this release): Pass 1 mechanical strip. Deterministic compression
of routine tool outputs (file listings, glob/grep results, weather).
Preserves all conversational content + action signatures + system messages.

Phase 2 (future): Pass 2 model-judgment-driven selective preservation with
operator review via interactive prompts → markdown record.

Phase 3 (future): End-to-end CLI with sandbox protocol + promotion flow.

Design reference: `MOSAIC/distill-for-partner-client-implementation.md` in
the Workshop docs. The framework-level documentation of distill's two-pass
architecture lives in MOSAIC Doc 02.

Public API:
    run_pass1(messages) → (compressed_messages, events)
    verify_distilled(original_path, sandbox_path) → VerifyResult
    write_compression_manifest(events, ...) → None
    run_distill_cli(config, args) → exit_code
"""
from __future__ import annotations

from .manifest import write_compression_manifest
from .pass1 import CompressionEvent, ROUTINE_TOOLS, run_pass1
from .verify import VerifyResult, verify_distilled

__all__ = [
    "CompressionEvent",
    "ROUTINE_TOOLS",
    "run_pass1",
    "VerifyResult",
    "verify_distilled",
    "write_compression_manifest",
]
