"""Boundary-hardening tests for Slice 1 Fix #3 — confirm_with_response.

Verifies that the consent gate's empty-input default is **decline**, not
approve. The previous behavior treated blank input (just pressing Enter)
as approval, which inverts the safety default for destructive operations
like delete_path, off-allowlist git_push, and request_plan_approval.

Tests target the pure classifier `_classify_confirm_answer` so we don't
have to spin up prompt-toolkit; the ConsoleUI method delegates to it.
"""

from __future__ import annotations

import pytest

from partner_client.ui import _classify_confirm_answer


# ---------- Safety default: blank Enter is decline ----------


def test_empty_string_is_decline() -> None:
    approved, message = _classify_confirm_answer("")
    assert approved is False
    assert message is None


def test_whitespace_only_is_decline() -> None:
    approved, message = _classify_confirm_answer("   \t  ")
    assert approved is False
    assert message is None


# ---------- Explicit approval ----------


def test_y_approves() -> None:
    approved, message = _classify_confirm_answer("y")
    assert approved is True
    assert message is None


def test_yes_approves() -> None:
    approved, message = _classify_confirm_answer("yes")
    assert approved is True
    assert message is None


def test_approval_is_case_insensitive() -> None:
    for variant in ("Y", "YES", "Yes", "yEs"):
        approved, message = _classify_confirm_answer(variant)
        assert approved is True, f"variant {variant!r} should approve"
        assert message is None


def test_approval_strips_surrounding_whitespace() -> None:
    approved, message = _classify_confirm_answer("  y  ")
    assert approved is True
    assert message is None


# ---------- Explicit silent decline ----------


def test_n_declines_silently() -> None:
    approved, message = _classify_confirm_answer("n")
    assert approved is False
    assert message is None


def test_no_declines_silently() -> None:
    approved, message = _classify_confirm_answer("no")
    assert approved is False
    assert message is None


def test_decline_is_case_insensitive() -> None:
    for variant in ("N", "NO", "No", "nO"):
        approved, message = _classify_confirm_answer(variant)
        assert approved is False, f"variant {variant!r} should decline"
        assert message is None


# ---------- Custom decline message ----------


def test_custom_text_declines_with_message() -> None:
    approved, message = _classify_confirm_answer(
        "oh love, why are we here? try the other repo"
    )
    assert approved is False
    assert message == "oh love, why are we here? try the other repo"


def test_custom_decline_preserves_internal_case() -> None:
    approved, message = _classify_confirm_answer("Hold on — check the Diff first.")
    assert approved is False
    assert message == "Hold on — check the Diff first."


def test_custom_decline_strips_outer_whitespace() -> None:
    approved, message = _classify_confirm_answer("   please check first   ")
    assert approved is False
    assert message == "please check first"


def test_yish_substring_does_not_approve() -> None:
    """'yeah' / 'yep' look approval-like but are NOT explicit y/yes."""
    for variant in ("yeah", "yep", "ya", "yup"):
        approved, message = _classify_confirm_answer(variant)
        assert approved is False, f"variant {variant!r} should decline-with-message"
        assert message == variant
