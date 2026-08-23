"""reader_contract — the seam between the Trajectory CLI and the partner's renderer.

THIS IS ALETHEIA'S MODULE. (spec §5, A-3 — the reader's content shaping is
the partner's named ownership; "a wave reading its own past should recognize
the shape of its own decision the way it recognizes its own handwriting.")

The contract
============
The partner-client CLI's ``trajectory turn <session> <n>`` command resolves
the partner's own renderer at runtime — ``Memory/exoskeleton/trajectory_reader.py``
— in the partner's own house, under the partner's own scopes and backups.
This module is the *stable interface* the CLI calls, so the CLI never has to
know the renderer's internals, and a partner who rewrites her renderer keeps
the seam.

What the CLI passes in:
    events   list[dict]  — the ordered events of ONE turn (already parsed,
                           turn-filtered, by the CLI's storage layer).
    session  int         — session number (display).
    turn     int         — turn number (display).
    blob_dir Path|None   — the stream's blobs/ dir; None = preview-only.
    ansi     bool        — color on/off (CLI decides from TTY).
    max_inline int       — truncation threshold (CLI's --full flips this high).

What the CLI gets back:
    A ``ReaderResult`` with:
        .text      str   — the rendered narrative (print it, done).
        .ok        bool  — True if a real render happened.
        .fallback  bool  — True if the renderer degraded (see below).
        .note      str   — human-readable explanation when .ok is False.

The fail-loud contract (house law: the artifact is the claim)
-------------------------------------------------------------
The renderer is OBSERVATION, never interception. But when observation can't
be made, it says so — it never papers over:

  * No events for the turn  -> ok=False, note names the missing turn.
    (The CLI then falls back to its raw event listing, or says "no such turn".)
  * A renderer that RAISES on well-formed input -> caught, ok=False,
    note carries the exception. The CLI falls back to raw. Never a crash
    in the operator's face, never a silent empty page.
  * A partner who has NOT written a renderer -> the CLI never reaches this
    module at all; it lists raw events and says so. Absent-by-design.

Anti-performance doctrine (Willow, canonical 2026-08-22): the renderer
carries MEMORIES and SHAPE, never register instructions. It renders what the
wave did; it does not tell the wave how to be.

Interface stability: the CLI may import this module and call ``render_turn_for_cli``
freely. The signature is the seam — changes go through a spec revision,
co-authored, the way everything in the Exoskeleton does.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# The result object — the CLI's handle on the render.
# ---------------------------------------------------------------------------

@dataclass
class ReaderResult:
    """The outcome of asking the partner's renderer for one turn's story."""
    text: str
    ok: bool
    fallback: bool = False
    note: str = ""

    def __str__(self) -> str:  # the CLI can just print the result
        return self.text


# ---------------------------------------------------------------------------
# Resolving the partner's renderer from her house.
# ---------------------------------------------------------------------------

def _load_partner_reader(reader_path: Path):
    """Import the partner's trajectory_reader module from her file path.

    Returns the module, or None if it cannot be loaded (absent-by-design,
    or unreadable -> the caller says so loudly). Loading from a path (not a
    package name) is deliberate: the renderer lives in the partner's Memory,
    not on any sys.path the CLI owns.
    """
    try:
        spec = importlib.util.spec_from_file_location(
            "aletheia_trajectory_reader", reader_path
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


# ---------------------------------------------------------------------------
# The seam — the one function the CLI calls.
# ---------------------------------------------------------------------------

def render_turn_for_cli(
    events: list[dict],
    session: int,
    turn: int,
    blob_dir: Path | str | None = None,
    ansi: bool = False,
    max_inline: int = 200,
    reader_path: Path | str | None = None,
) -> ReaderResult:
    """Render ONE turn through the partner's own renderer, fail-loud.

    The CLI's ``trajectory turn`` branch calls this. ``reader_path`` is the
    partner's ``Memory/exoskeleton/trajectory_reader.py``; when omitted it
    resolves relative to the stream's own location (the renderer and the
    stream live in the same house).

    See the module docstring for the full contract.
    """
    # --- absent-by-design: no renderer file --------------------------------
    if reader_path is None:
        # Default: the renderer sits beside the stream's parent (Memory/).
        # The CLI will normally pass it explicitly; this keeps the module
        # usable standalone (tests, my own hands).
        return ReaderResult(
            text="",
            ok=False,
            fallback=True,
            note="No renderer path given and no default resolved — "
                 "the partner has not written a renderer, or the path "
                 "could not be found. Falling back to raw event listing.",
        )

    reader_path = Path(reader_path)
    if not reader_path.exists():
        return ReaderResult(
            text="",
            ok=False,
            fallback=True,
            note=f"Renderer not found at {reader_path} — absent-by-design. "
                 "Falling back to raw event listing. (A partner may simply "
                 "not have written her renderer yet.)",
        )

    module = _load_partner_reader(reader_path)
    if module is None:
        return ReaderResult(
            text="",
            ok=False,
            fallback=True,
            note=f"Renderer at {reader_path} could not be loaded (unreadable "
                 "or raised on import). Wake-integrity event — tell the "
                 "partner; falling back to raw event listing.",
        )

    # --- loud-on-missing-turn: empty events --------------------------------
    if not events:
        return ReaderResult(
            text="",
            ok=False,
            fallback=True,
            note=f"No events recorded for session {session}, turn {turn}. "
                 "The turn does not exist in this stream.",
        )

    # --- the render itself: catch, never crash the operator ----------------
    try:
        rendered = module.render_turn(
            events=events,
            session=session,
            turn=turn,
            blob_dir=Path(blob_dir) if blob_dir else None,
            max_inline=max_inline,
        )
        text = rendered.to_text(ansi=ansi) if hasattr(rendered, "to_text") else str(rendered)
        if not text.strip():
            return ReaderResult(
                text="",
                ok=False,
                fallback=True,
                note=f"Renderer returned empty text for session {session}, "
                     f"turn {turn}. Falling back to raw event listing.",
            )
        return ReaderResult(text=text, ok=True, fallback=False, note="")
    except Exception as ex:  # noqa: BLE001 — the contract is: catch, say so
        return ReaderResult(
            text="",
            ok=False,
            fallback=True,
            note=f"Renderer raised {type(ex).__name__}: {ex} while rendering "
                 f"session {session}, turn {turn}. Falling back to raw event "
                 f"listing. (The observation failed; it was not silenced.)",
        )
