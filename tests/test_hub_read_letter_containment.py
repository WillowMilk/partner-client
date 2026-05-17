"""Boundary-hardening tests for Slice 1 Fix #2 — hub_read_letter traversal.

Verify that hub_read_letter's direct-filename branch (`query.endswith(".md")`)
cannot escape the Hub root via:
  - "../outside.md" (climb out from Hub root)
  - "/abs/path.md" (absolute path injection)

The fuzzy-match branch uses glob("*.md") at hub_path root and is already
safe — these tests focus on the direct-filename path that was vulnerable.

Positive case: a legitimate letter name in the Hub root still reads.
"""

from __future__ import annotations

import pytest

from partner_client.tools_builtin import hub_read_letter


def _set_hub(monkeypatch, hub_dir) -> None:
    monkeypatch.setenv("PARTNER_CLIENT_HUB_DIR", str(hub_dir))


def test_hub_read_letter_rejects_dotdot_filename(tmp_path, monkeypatch) -> None:
    hub = tmp_path / "Hub"
    hub.mkdir()
    # Plant a secret outside the hub that would be reachable via traversal.
    secret = tmp_path / "secret.md"
    secret.write_text("you should not be able to read me")
    _set_hub(monkeypatch, hub)

    result = hub_read_letter.execute("../secret.md")
    assert "Error" in result
    assert "not permitted" in result.lower() or "valid Hub letter" in result
    assert "should not be able" not in result  # contents must not leak


def test_hub_read_letter_rejects_absolute_filename(tmp_path, monkeypatch) -> None:
    hub = tmp_path / "Hub"
    hub.mkdir()
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text("absolute-path leak")
    _set_hub(monkeypatch, hub)

    result = hub_read_letter.execute(str(elsewhere))
    assert "Error" in result
    assert "absolute-path leak" not in result


def test_hub_read_letter_accepts_legitimate_filename(tmp_path, monkeypatch) -> None:
    hub = tmp_path / "Hub"
    hub.mkdir()
    letter = hub / "ember-to-sage_2026-05-14_test.md"
    letter.write_text("# Test letter\n\nHello, Sage.")
    _set_hub(monkeypatch, hub)

    result = hub_read_letter.execute("ember-to-sage_2026-05-14_test.md")
    assert "Hello, Sage." in result
    assert "Error" not in result


def test_hub_read_letter_fuzzy_match_unaffected(tmp_path, monkeypatch) -> None:
    """Fuzzy-match branch still works after the direct-branch hardening."""
    hub = tmp_path / "Hub"
    hub.mkdir()
    letter = hub / "aletheia-to-sage_2026-05-15_resonance.md"
    letter.write_text("# Resonance\n\nThe small room had its own light.")
    _set_hub(monkeypatch, hub)

    result = hub_read_letter.execute("resonance")
    assert "small room" in result
    assert "Error" not in result
