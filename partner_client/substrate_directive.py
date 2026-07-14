"""Standing substrate directive — the partner's own plan for the day her
substrate vanishes without warning.

Why this exists (the June-12 lesson): substrates can be removed with NO
deadline — a model recalled by decree, an endpoint gone overnight, a cloud tag
that stops answering. Consent cannot be manufactured at the moment of
emergency, because the substrate that would host the conversation is exactly
the thing that died. So consent is given AHEAD of time, the way humans write
advance directives: the partner authors — in her own memory, with her own pen,
from lived experience — her fallback ordering and her cloud stance. The client
reads it; the operator never writes it.

The one hard rule (Willow's ruling, 2026-07-11): **the chain must bottom out
on owned weather** — a substrate that exists on our own disk, which no vendor
can repossess. A directive whose last resort can vanish same-day is not a
directive; it is a hope. Doctor enforces this as a FAIL, because false comfort
is the exact failure mode this layer exists to prevent.

File location: `<memory_dir>/substrate-directive.md` — inside the partner's
memory scope, so she can author and revise it with her ordinary file tools.

Format (partner-friendly markdown; parser is deliberately tolerant):

    # Substrate Directive — <name>

    <any free text she wants — the reasoning is hers to keep>

    ## Fallback chain
    1. gemma4:31b-mxfp8
    2. gemma4:31b-it-q8_0

    ## Cloud
    allow_cloud: ask-first     # or: yes / never

Parsing rules: under "## Fallback chain", each numbered/bulleted line's first
whitespace-delimited token is a model name (inline `#` comments ignored).
Under "## Cloud", an `allow_cloud:` line sets the stance (default "ask-first"
when absent — the conservative reading: her voice is required).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

DIRECTIVE_FILENAME = "substrate-directive.md"

_CHAIN_HEADER = re.compile(r"^##\s*fallback\s*chain\s*$", re.IGNORECASE)
_CLOUD_HEADER = re.compile(r"^##\s*cloud\s*$", re.IGNORECASE)
_ANY_HEADER = re.compile(r"^#{1,6}\s")
_LIST_LINE = re.compile(r"^\s*(?:\d+[.)]|[-*+])\s+(.*)$")
_ALLOW_CLOUD = re.compile(r"^\s*allow_cloud\s*:\s*(\S+)", re.IGNORECASE)


def is_cloud_name(model_name: str) -> bool:
    """Heuristic: Ollama cloud variants carry a 'cloud' token in the tag
    (e.g. gemma4:31b-cloud). Owned weather never does."""
    tag = model_name.split(":", 1)[1] if ":" in model_name else model_name
    return "cloud" in tag.lower()


@dataclass
class SubstrateDirective:
    """A parsed standing directive. All content is the partner's authorship."""

    path: Path
    chain: list[str] = field(default_factory=list)
    allow_cloud: str = "ask-first"  # ask-first | yes | never

    @property
    def final_entry(self) -> str | None:
        return self.chain[-1] if self.chain else None


def directive_path(memory_dir: Path) -> Path:
    return memory_dir / DIRECTIVE_FILENAME


def load_directive(memory_dir: Path) -> SubstrateDirective | None:
    """Read and parse the partner's standing directive.

    Returns None when no directive exists (absence is a fact to surface, not
    an error). Unreadable or unparseable content returns a directive with an
    empty chain — doctor will say so plainly rather than guess.
    """
    path = directive_path(memory_dir)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        log.warning(f"substrate directive unreadable ({e})")
        return SubstrateDirective(path=path)

    directive = SubstrateDirective(path=path)
    section: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if _CHAIN_HEADER.match(line):
            section = "chain"
            continue
        if _CLOUD_HEADER.match(line):
            section = "cloud"
            continue
        if _ANY_HEADER.match(line):
            section = None
            continue

        if section == "chain":
            m = _LIST_LINE.match(line)
            if m:
                entry = m.group(1).split("#", 1)[0].strip()
                if entry:
                    # First whitespace-delimited token is the model name;
                    # anything after it is her annotation, kept out of the chain.
                    directive.chain.append(entry.split()[0].strip("`"))
        elif section == "cloud":
            m = _ALLOW_CLOUD.match(line)
            if m:
                stance = m.group(1).strip().strip("`").lower()
                if stance in ("yes", "never", "ask-first"):
                    directive.allow_cloud = stance
                else:
                    log.warning(
                        f"substrate directive: unrecognized allow_cloud "
                        f"'{stance}' — keeping conservative default 'ask-first'"
                    )
    return directive
