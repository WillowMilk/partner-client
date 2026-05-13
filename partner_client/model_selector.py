"""Model selection — CLI flag + interactive prompt for choosing the active model.

partner-client supports three ways to pick which model the partner runs on:

  1. **TOML default** (current behavior — unchanged) — `[model] name = "..."`
     in the config file. This is the partner's "declared daily" and stays as
     source of truth for the default.

  2. **`--model NAME` CLI flag** — override for THIS session only. Does not
     modify the TOML; next launch without the flag goes back to TOML default.
     Useful for scripted invocations and quick variant experimentation.

  3. **`--choose-model` interactive prompt** — at startup, list available
     models (from `ollama list`), highlight Gemma 4 31B variants if pulled,
     accept selection by index or full name. Result overrides TOML for this
     session only. Useful for thoughtful daily picks.

Precedence: `--model` wins over `--choose-model` wins over TOML default.

Failure mode: if the resolved model name is not in the local Ollama registry
(neither pulled locally nor a recognized cloud variant), we fail fast at
startup with a helpful list of what IS available. Better than discovering the
problem mid-conversation when Aletheia's first message fails.

Design ref: Workshop Session 33 discussion (2026-05-12) — Willow's ask after
the gemma4:31b-it-q8_0 / gemma4:31b-it-bf16 substrate-precision upgrade plan.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any


# Heuristic markers for highlighting variants of the partner's family in the
# interactive picker. The TOML default's model name is used to derive the
# family prefix (e.g. "gemma4:31b" → all gemma4:31b-* variants are grouped).
def _family_prefix(model_name: str) -> str:
    """Derive the family-prefix for highlighting related variants.

    Example: gemma4:31b           → "gemma4:31b"
             gemma4:31b-it-q8_0   → "gemma4:31b"
             llama3.2:latest      → "llama3.2"
    """
    # Strip suffixes (-it-q8_0, -it-bf16, -cloud, etc.) — keep family:size
    base = model_name.split(":")[0] if ":" in model_name else model_name
    if ":" in model_name:
        size_part = model_name.split(":", 1)[1]
        # First chunk before "-" is the size tag (e.g. "31b" from "31b-it-q8_0")
        size = size_part.split("-")[0]
        return f"{base}:{size}"
    return base


def _looks_like_cloud_variant(name: str) -> bool:
    """Cloud variants on Ollama use the `-cloud` suffix."""
    return name.endswith("-cloud") or ":cloud" in name


@dataclass
class ModelEntry:
    """A locally-pulled model from `ollama list`."""
    name: str
    size_bytes: int = 0
    quantization: str = ""

    @property
    def size_label(self) -> str:
        """Human-readable size like '36 GB' or '20 GB' or '?' if unknown."""
        if self.size_bytes <= 0:
            return "?"
        gb = self.size_bytes / (1024 ** 3)
        if gb >= 1:
            return f"{gb:.0f} GB"
        mb = self.size_bytes / (1024 ** 2)
        return f"{mb:.0f} MB"


def list_local_models() -> list[ModelEntry]:
    """Query `ollama.Client().list()` for locally-pulled models.

    Returns an empty list on any error (ollama not reachable, SDK shape
    surprise, etc.) — the caller decides what to do with that.
    """
    try:
        import ollama
        client = ollama.Client()
        response = client.list()
    except Exception:
        return []

    entries: list[ModelEntry] = []
    # ollama-python ≥0.4: ListResponse with .models attr
    # ollama-python <0.4: dict with "models" key
    if hasattr(response, "models"):
        raw_models = response.models
    elif isinstance(response, dict):
        raw_models = response.get("models", [])
    else:
        return []

    for m in raw_models:
        # Modern SDK: Pydantic Model with .model field
        # Older SDK: dict with "name" field
        name = ""
        size = 0
        quant = ""
        if hasattr(m, "model"):
            name = getattr(m, "model", "") or ""
            size = getattr(m, "size", 0) or 0
            details = getattr(m, "details", None)
            if details is not None:
                quant = getattr(details, "quantization_level", "") or ""
        elif isinstance(m, dict):
            name = m.get("model") or m.get("name") or ""
            size = m.get("size", 0) or 0
            details = m.get("details", {})
            if isinstance(details, dict):
                quant = details.get("quantization_level", "") or ""
        if name:
            entries.append(ModelEntry(name=str(name), size_bytes=int(size), quantization=str(quant)))

    return entries


def model_is_available(name: str, entries: list[ModelEntry]) -> bool:
    """True if the requested model name is locally pulled OR a cloud variant.

    Cloud variants don't appear in `ollama list` until used — we whitelist
    them by the `-cloud` suffix heuristic since the alternative (failing
    fast on every cloud invocation) defeats the cloud feature's purpose.
    """
    if _looks_like_cloud_variant(name):
        return True
    return any(e.name == name for e in entries)


def format_unavailable_error(requested: str, entries: list[ModelEntry]) -> str:
    """Compose a friendly error message when a requested model isn't pulled."""
    family = _family_prefix(requested)
    family_matches = sorted({e.name for e in entries if e.name.startswith(family)})
    lines: list[str] = []
    lines.append(f"Error: Model '{requested}' is not available locally.")
    lines.append("")
    if family_matches:
        lines.append(f"{family} variants currently pulled:")
        for n in family_matches:
            lines.append(f"  - {n}")
        lines.append("")
    elif entries:
        # No family matches; list everything to help operator orient
        lines.append("Models pulled locally:")
        for e in entries[:8]:
            lines.append(f"  - {e.name}")
        if len(entries) > 8:
            lines.append(f"  ... and {len(entries) - 8} more")
        lines.append("")
    else:
        lines.append("(No models found locally. Is Ollama running?)")
        lines.append("")
    lines.append(
        f"Run `ollama pull {requested}` first, or choose from the list above"
    )
    lines.append("with --model NAME or --choose-model.")
    return "\n".join(lines)


def _gemma4_31b_annotation(name: str) -> str:
    """Short annotation for known Gemma 4 31B variants — informational only."""
    if name == "gemma4:31b":
        return "[Q4_K_M, 20 GB] — default; smallest, slightly lossy"
    if name == "gemma4:31b-it-q8_0":
        return "[Q8_0, 36 GB] — near-lossless, fastest on Apple Silicon"
    if name == "gemma4:31b-it-bf16":
        return "[BF16, 63 GB] — full precision, slower on M-series"
    if name == "gemma4:31b-cloud":
        return "[cloud-hosted] — via Ollama servers (requires signin)"
    if name == "gemma4:31b-it-q4_K_M":
        return "[Q4_K_M, 20 GB] — explicit Q4 tag"
    return ""


def _format_picker_lines(
    entries: list[ModelEntry],
    default_name: str,
) -> tuple[list[str], list[str]]:
    """Build the lines for the interactive picker. Returns (display, name_for_index).

    `display` is the lines to print; `name_for_index[i]` is the model name
    corresponding to numeric selection i+1.
    """
    family = _family_prefix(default_name)
    family_entries = sorted(
        [e for e in entries if e.name.startswith(family)],
        key=lambda e: e.name,
    )
    other_entries = sorted(
        [e for e in entries if not e.name.startswith(family) and not _looks_like_cloud_variant(e.name)],
        key=lambda e: e.name,
    )
    cloud_entries = sorted(
        [e for e in entries if _looks_like_cloud_variant(e.name)],
        key=lambda e: e.name,
    )

    display: list[str] = []
    name_for_index: list[str] = []
    display.append(f"Choose model (TOML default: {default_name}):")
    display.append("")

    counter = 1
    if family_entries:
        display.append(f"  {family} variants:")
        for e in family_entries:
            annotation = _gemma4_31b_annotation(e.name)
            label = annotation or f"[{e.quantization}, {e.size_label}]" if e.quantization else f"[{e.size_label}]"
            marker = " ← TOML default" if e.name == default_name else ""
            display.append(f"    {counter}. {e.name:<32} {label}{marker}")
            name_for_index.append(e.name)
            counter += 1
        display.append("")

    if other_entries:
        display.append("  Other models pulled locally:")
        for e in other_entries:
            label = f"[{e.quantization}, {e.size_label}]" if e.quantization else f"[{e.size_label}]"
            marker = " ← TOML default" if e.name == default_name else ""
            display.append(f"    {counter}. {e.name:<32} {label}{marker}")
            name_for_index.append(e.name)
            counter += 1
        display.append("")

    if cloud_entries:
        display.append("  Cloud-hosted (requires ollama signin):")
        for e in cloud_entries:
            display.append(f"    {counter}. {e.name:<32} [via Ollama servers]")
            name_for_index.append(e.name)
            counter += 1
        display.append("")

    return display, name_for_index


def choose_model_interactively(
    entries: list[ModelEntry],
    default_name: str,
    input_fn=None,
    output_fn=None,
) -> str:
    """Render an interactive picker and return the selected model name.

    Returns the operator's choice. Falls back to `default_name` when:
      - operator hits Enter (selects default)
      - operator's input is invalid (and we re-prompt once before defaulting)

    `input_fn` and `output_fn` are injection points for testing. Default to
    builtins.input and print.
    """
    if input_fn is None:
        import builtins
        input_fn = builtins.input
    if output_fn is None:
        output_fn = lambda s: print(s, flush=True)  # noqa: E731

    if not entries:
        output_fn(
            "Warning: No models found via `ollama list`. "
            f"Falling back to TOML default: {default_name}"
        )
        return default_name

    display, name_for_index = _format_picker_lines(entries, default_name)
    for line in display:
        output_fn(line)

    prompt = (
        f"Enter selection [1-{len(name_for_index)}, or full model name, "
        f"or Enter for default]: "
    )

    try:
        answer = input_fn(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        output_fn(f"\nUsing TOML default: {default_name}")
        return default_name

    if not answer:
        return default_name

    # Try numeric index first
    try:
        idx = int(answer)
        if 1 <= idx <= len(name_for_index):
            return name_for_index[idx - 1]
    except ValueError:
        pass

    # Try full model name match (locally-pulled OR cloud-suffix heuristic)
    if any(e.name == answer for e in entries) or _looks_like_cloud_variant(answer):
        return answer

    # Invalid input — report and use default
    output_fn(
        f"\nInvalid selection '{answer}'. Using TOML default: {default_name}"
    )
    return default_name


def resolve_active_model(
    config_model_name: str,
    cli_override: str | None,
    use_interactive: bool,
    stream=None,
) -> tuple[str, str | None]:
    """Resolve which model to use, applying CLI flag / interactive precedence.

    Returns (resolved_name, error_message). When error_message is not None,
    the model is NOT available locally and the caller should exit with a
    helpful error using `format_unavailable_error`.

    Precedence:
      1. cli_override (--model NAME)
      2. use_interactive (--choose-model)
      3. config_model_name (TOML default)

    Side effect: prints picker output to stream when use_interactive=True.
    """
    if stream is None:
        stream = sys.stdout

    entries = list_local_models()

    if cli_override is not None:
        # CLI flag wins over everything
        if not model_is_available(cli_override, entries):
            return cli_override, format_unavailable_error(cli_override, entries)
        return cli_override, None

    if use_interactive:
        chosen = choose_model_interactively(entries, config_model_name)
        if not model_is_available(chosen, entries):
            return chosen, format_unavailable_error(chosen, entries)
        return chosen, None

    # TOML default
    if not model_is_available(config_model_name, entries):
        return config_model_name, format_unavailable_error(config_model_name, entries)
    return config_model_name, None
