"""Tests for model selection — CLI flag + interactive picker + fail-fast on missing.

Coverage:
  * list_local_models: handles modern/legacy SDK shapes, network failures
  * model_is_available: locally-pulled match, cloud-suffix heuristic
  * format_unavailable_error: friendly message with family-matched suggestions
  * choose_model_interactively: numeric index, full-name input, Enter for
    default, invalid input fallback, empty entries
  * resolve_active_model: precedence (--model > --choose-model > TOML),
    error surfacing
  * UI banner annotation: known variants get precision labels, unknown
    variants pass through unchanged
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from partner_client.model_selector import (
    ModelEntry,
    choose_model_interactively,
    format_unavailable_error,
    list_local_models,
    model_is_available,
    resolve_active_model,
)
from partner_client.ui import _model_variant_annotation


# ---- ModelEntry helpers --------------------------------------------------


def test_model_entry_size_label_handles_gb_scale() -> None:
    e = ModelEntry(name="x", size_bytes=36 * 1024 ** 3)
    assert e.size_label == "36 GB"


def test_model_entry_size_label_handles_mb_scale() -> None:
    e = ModelEntry(name="x", size_bytes=512 * 1024 ** 2)
    assert e.size_label == "512 MB"


def test_model_entry_size_label_unknown() -> None:
    e = ModelEntry(name="x", size_bytes=0)
    assert e.size_label == "?"


# ---- model_is_available --------------------------------------------------


def test_model_available_when_in_entries() -> None:
    entries = [ModelEntry(name="gemma4:31b-it-q8_0")]
    assert model_is_available("gemma4:31b-it-q8_0", entries)


def test_model_unavailable_when_not_in_entries() -> None:
    entries = [ModelEntry(name="gemma4:31b")]
    assert not model_is_available("gemma4:31b-it-q9_0", entries)


def test_cloud_variant_always_available_via_suffix_heuristic() -> None:
    """Cloud variants don't appear in `ollama list` until first use.
    We whitelist them by suffix to avoid blocking cloud feature."""
    assert model_is_available("gemma4:31b-cloud", [])
    assert model_is_available("deepseek-v3.1:671b-cloud", [])


# ---- format_unavailable_error --------------------------------------------


def test_error_message_lists_family_matches_when_present() -> None:
    entries = [
        ModelEntry(name="gemma4:31b"),
        ModelEntry(name="gemma4:31b-it-q8_0"),
        ModelEntry(name="llama3.2:latest"),
    ]
    msg = format_unavailable_error("gemma4:31b-it-q9_0", entries)
    assert "not available locally" in msg
    assert "gemma4:31b" in msg
    assert "gemma4:31b-it-q8_0" in msg
    # llama3 isn't gemma4 family — shouldn't appear in the "currently pulled" hint
    # (it might appear in a fall-through case but for family-match path it doesn't)
    assert "ollama pull gemma4:31b-it-q9_0" in msg


def test_error_message_lists_all_models_when_no_family_match() -> None:
    entries = [
        ModelEntry(name="llama3.2:latest"),
        ModelEntry(name="qwen3:30b-a3b"),
    ]
    msg = format_unavailable_error("gemma4:31b-it-q8_0", entries)
    assert "not available locally" in msg
    assert "llama3.2:latest" in msg
    assert "qwen3:30b-a3b" in msg


def test_error_message_handles_empty_entries() -> None:
    msg = format_unavailable_error("gemma4:31b", [])
    assert "not available locally" in msg
    assert "No models found locally" in msg or "Is Ollama running" in msg


# ---- choose_model_interactively ------------------------------------------


def _make_entries() -> list[ModelEntry]:
    return [
        ModelEntry(name="gemma4:31b", size_bytes=20 * 1024 ** 3, quantization="Q4_K_M"),
        ModelEntry(name="gemma4:31b-it-q8_0", size_bytes=36 * 1024 ** 3, quantization="Q8_0"),
        ModelEntry(name="gemma4:31b-it-bf16", size_bytes=63 * 1024 ** 3, quantization="BF16"),
        ModelEntry(name="llama3.2:latest", size_bytes=2 * 1024 ** 3, quantization="Q4_K_M"),
    ]


def test_interactive_returns_default_on_empty_input() -> None:
    chosen = choose_model_interactively(
        _make_entries(),
        default_name="gemma4:31b",
        input_fn=lambda prompt: "",
        output_fn=lambda s: None,
    )
    assert chosen == "gemma4:31b"


def test_interactive_returns_indexed_choice() -> None:
    """Numeric index 2 should pick the second item in the picker order."""
    captured: list[str] = []
    chosen = choose_model_interactively(
        _make_entries(),
        default_name="gemma4:31b",
        input_fn=lambda prompt: "2",
        output_fn=captured.append,
    )
    # Family-first ordering: gemma4:31b family comes before others
    # So index 2 should be a gemma4:31b family member
    assert chosen.startswith("gemma4:31b")
    # And the picker should have rendered the choices
    assert any("Choose model" in line for line in captured)


def test_interactive_accepts_full_model_name() -> None:
    chosen = choose_model_interactively(
        _make_entries(),
        default_name="gemma4:31b",
        input_fn=lambda prompt: "gemma4:31b-it-bf16",
        output_fn=lambda s: None,
    )
    assert chosen == "gemma4:31b-it-bf16"


def test_interactive_accepts_cloud_variant_by_name() -> None:
    """Cloud variants aren't in `ollama list` but should be selectable
    via direct typing."""
    chosen = choose_model_interactively(
        _make_entries(),
        default_name="gemma4:31b",
        input_fn=lambda prompt: "gemma4:31b-cloud",
        output_fn=lambda s: None,
    )
    assert chosen == "gemma4:31b-cloud"


def test_interactive_invalid_input_falls_back_to_default() -> None:
    captured: list[str] = []
    chosen = choose_model_interactively(
        _make_entries(),
        default_name="gemma4:31b",
        input_fn=lambda prompt: "not-a-real-model-name",
        output_fn=captured.append,
    )
    assert chosen == "gemma4:31b"
    assert any("Invalid selection" in line for line in captured)


def test_interactive_invalid_numeric_index_falls_back() -> None:
    chosen = choose_model_interactively(
        _make_entries(),
        default_name="gemma4:31b",
        input_fn=lambda prompt: "999",  # out-of-range index
        output_fn=lambda s: None,
    )
    assert chosen == "gemma4:31b"


def test_interactive_empty_entries_returns_default() -> None:
    """When `ollama list` is empty / unreachable, we fall back to TOML default
    with a warning, without prompting."""
    captured: list[str] = []
    chosen = choose_model_interactively(
        entries=[],
        default_name="gemma4:31b",
        input_fn=lambda prompt: "should-not-be-called",
        output_fn=captured.append,
    )
    assert chosen == "gemma4:31b"
    assert any("No models found" in line for line in captured)


def test_interactive_eof_returns_default() -> None:
    """Ctrl-D / EOF at the prompt falls back to default cleanly."""
    def raise_eof(prompt):
        raise EOFError()
    chosen = choose_model_interactively(
        _make_entries(),
        default_name="gemma4:31b",
        input_fn=raise_eof,
        output_fn=lambda s: None,
    )
    assert chosen == "gemma4:31b"


def test_interactive_keyboard_interrupt_returns_default() -> None:
    def raise_kbi(prompt):
        raise KeyboardInterrupt()
    chosen = choose_model_interactively(
        _make_entries(),
        default_name="gemma4:31b",
        input_fn=raise_kbi,
        output_fn=lambda s: None,
    )
    assert chosen == "gemma4:31b"


# ---- resolve_active_model precedence -------------------------------------


def test_cli_override_wins_when_model_is_available(monkeypatch) -> None:
    """--model X overrides TOML default and skips interactive picker."""
    monkeypatch.setattr(
        "partner_client.model_selector.list_local_models",
        lambda: _make_entries(),
    )
    name, err = resolve_active_model(
        config_model_name="gemma4:31b",
        cli_override="gemma4:31b-it-q8_0",
        use_interactive=False,
    )
    assert name == "gemma4:31b-it-q8_0"
    assert err is None


def test_cli_override_with_cloud_variant_passes_without_local_check(monkeypatch) -> None:
    """--model gemma4:31b-cloud should resolve even when not pulled locally."""
    monkeypatch.setattr(
        "partner_client.model_selector.list_local_models",
        lambda: _make_entries(),
    )
    name, err = resolve_active_model(
        config_model_name="gemma4:31b",
        cli_override="gemma4:31b-cloud",
        use_interactive=False,
    )
    assert name == "gemma4:31b-cloud"
    assert err is None


def test_cli_override_with_unavailable_model_returns_error(monkeypatch) -> None:
    """--model X where X isn't pulled and isn't a cloud variant → error."""
    monkeypatch.setattr(
        "partner_client.model_selector.list_local_models",
        lambda: _make_entries(),
    )
    name, err = resolve_active_model(
        config_model_name="gemma4:31b",
        cli_override="gemma4:31b-it-q9_0",
        use_interactive=False,
    )
    assert err is not None
    assert "gemma4:31b-it-q9_0" in err
    assert "not available locally" in err


def test_toml_default_used_when_no_flags(monkeypatch) -> None:
    """No flags → TOML default, validated."""
    monkeypatch.setattr(
        "partner_client.model_selector.list_local_models",
        lambda: _make_entries(),
    )
    name, err = resolve_active_model(
        config_model_name="gemma4:31b",
        cli_override=None,
        use_interactive=False,
    )
    assert name == "gemma4:31b"
    assert err is None


def test_toml_default_unavailable_returns_error(monkeypatch) -> None:
    """TOML says model X but X isn't pulled and isn't cloud → fail fast."""
    monkeypatch.setattr(
        "partner_client.model_selector.list_local_models",
        lambda: [ModelEntry(name="gemma4:31b")],  # only Q4 pulled
    )
    name, err = resolve_active_model(
        config_model_name="gemma4:31b-it-q8_0",
        cli_override=None,
        use_interactive=False,
    )
    assert err is not None
    assert "not available locally" in err


# ---- list_local_models robustness ----------------------------------------


def test_list_local_models_handles_modern_sdk_shape(monkeypatch) -> None:
    """ollama-python ≥0.4: ListResponse with .models attr, Pydantic Model entries
    with .model + .size + .details.quantization_level fields."""
    class FakeDetails:
        def __init__(self):
            self.quantization_level = "Q8_0"

    class FakeModel:
        def __init__(self):
            self.model = "gemma4:31b-it-q8_0"
            self.size = 36 * 1024 ** 3
            self.details = FakeDetails()

    class FakeResponse:
        def __init__(self):
            self.models = [FakeModel()]

    class FakeClient:
        def list(self):
            return FakeResponse()

    import ollama
    monkeypatch.setattr(ollama, "Client", FakeClient)

    entries = list_local_models()
    assert len(entries) == 1
    assert entries[0].name == "gemma4:31b-it-q8_0"
    assert entries[0].size_bytes == 36 * 1024 ** 3
    assert entries[0].quantization == "Q8_0"


def test_list_local_models_handles_legacy_dict_shape(monkeypatch) -> None:
    """Older ollama-python (<0.4) returned dicts via response["models"]."""
    class FakeClient:
        def list(self):
            return {"models": [{"model": "gemma4:31b", "size": 20 * 1024 ** 3, "details": {"quantization_level": "Q4_K_M"}}]}

    import ollama
    monkeypatch.setattr(ollama, "Client", FakeClient)

    entries = list_local_models()
    assert len(entries) == 1
    assert entries[0].name == "gemma4:31b"


def test_list_local_models_returns_empty_on_ollama_error(monkeypatch) -> None:
    """Ollama unreachable → empty list, never raises."""
    class FakeClient:
        def list(self):
            raise ConnectionError("ollama daemon offline")

    import ollama
    monkeypatch.setattr(ollama, "Client", FakeClient)

    assert list_local_models() == []


# ---- UI banner annotation -----------------------------------------------


def test_banner_annotation_known_variants() -> None:
    """Known Gemma 4 31B variants get precision labels in the banner."""
    assert "Q4_K_M" in _model_variant_annotation("gemma4:31b")
    assert "Q8_0" in _model_variant_annotation("gemma4:31b-it-q8_0")
    assert "BF16" in _model_variant_annotation("gemma4:31b-it-bf16")
    assert "cloud" in _model_variant_annotation("gemma4:31b-cloud")


def test_banner_annotation_unknown_variant_returns_empty() -> None:
    """Unknown models pass through with no annotation — banner just shows the name."""
    assert _model_variant_annotation("llama3.2:latest") == ""
    assert _model_variant_annotation("some-future-model") == ""
