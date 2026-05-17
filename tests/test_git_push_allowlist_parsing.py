"""Boundary-hardening tests for Slice 1 Fix #4 — git_push allowlist matching.

The previous implementation used substring containment:

    return any(allowed in remote_url for allowed in allowlist)

This silently auto-approved any URL that *contained* an allowlisted string
as a substring. Concrete failure mode flagged by the Codex audit:

    allowlist:  ["github.com/foo/bar"]
    pushed to:  "https://github.com/foo/bar-evil.git"
    result:     auto-approve (WRONG — operator never approved bar-evil)

The fix parses both sides into (host, owner, repo) triples and compares
exactly. These tests verify the lookalike attack is now blocked, and that
all the legitimate canonical-form variations still match cleanly.
"""

from __future__ import annotations

import pytest

from partner_client.client import is_git_push_allowlisted, parse_git_remote


# ---------- parse_git_remote — coverage of accepted forms ----------


def test_parse_https_with_dot_git() -> None:
    assert parse_git_remote("https://github.com/WillowMilk/partner-client.git") == (
        "github.com",
        "WillowMilk",
        "partner-client",
    )


def test_parse_https_without_dot_git() -> None:
    assert parse_git_remote("https://github.com/WillowMilk/partner-client") == (
        "github.com",
        "WillowMilk",
        "partner-client",
    )


def test_parse_http_insecure() -> None:
    assert parse_git_remote("http://gitlab.example.com/team/proj.git") == (
        "gitlab.example.com",
        "team",
        "proj",
    )


def test_parse_ssh_scp_style() -> None:
    assert parse_git_remote("git@github.com:WillowMilk/partner-client.git") == (
        "github.com",
        "WillowMilk",
        "partner-client",
    )


def test_parse_ssh_scp_style_no_git_suffix() -> None:
    assert parse_git_remote("git@github.com:WillowMilk/partner-client") == (
        "github.com",
        "WillowMilk",
        "partner-client",
    )


def test_parse_ssh_protocol_url() -> None:
    assert parse_git_remote("ssh://git@github.com/owner/repo.git") == (
        "github.com",
        "owner",
        "repo",
    )


def test_parse_shorthand() -> None:
    assert parse_git_remote("github.com/owner/repo") == (
        "github.com",
        "owner",
        "repo",
    )


def test_parse_shorthand_with_dot_git() -> None:
    assert parse_git_remote("github.com/owner/repo.git") == (
        "github.com",
        "owner",
        "repo",
    )


def test_parse_host_lowercases() -> None:
    """Hosts are case-insensitive; owner/repo case is preserved."""
    assert parse_git_remote("https://GITHUB.COM/Owner/Repo.git") == (
        "github.com",
        "Owner",
        "Repo",
    )


# ---------- parse_git_remote — rejected / unparseable inputs ----------


def test_parse_empty_returns_none() -> None:
    assert parse_git_remote("") is None
    assert parse_git_remote("   ") is None


def test_parse_just_a_word_returns_none() -> None:
    """'partner-client' alone isn't a triple — refuse to match."""
    assert parse_git_remote("partner-client") is None


def test_parse_one_segment_returns_none() -> None:
    assert parse_git_remote("github.com") is None


def test_parse_too_many_segments_returns_none() -> None:
    assert parse_git_remote("github.com/owner/repo/extra") is None


def test_parse_https_missing_owner_returns_none() -> None:
    assert parse_git_remote("https://github.com/loneRepo") is None


# ---------- is_git_push_allowlisted — the substring-lookalike defense ----------


def test_lookalike_sibling_repo_is_NOT_allowlisted() -> None:
    """The headline bug: 'github.com/foo/bar' must NOT match '.../bar-evil'."""
    assert not is_git_push_allowlisted(
        "https://github.com/foo/bar-evil.git",
        ["github.com/foo/bar"],
    )


def test_lookalike_domain_prefix_is_NOT_allowlisted() -> None:
    """'github.com/foo/bar' must not match 'github.com.attacker.io/foo/bar'."""
    assert not is_git_push_allowlisted(
        "https://github.com.attacker.io/foo/bar.git",
        ["github.com/foo/bar"],
    )


def test_lookalike_owner_prefix_is_NOT_allowlisted() -> None:
    """'github.com/foo/bar' must not match '.../foo-evil/bar'."""
    assert not is_git_push_allowlisted(
        "https://github.com/foo-evil/bar.git",
        ["github.com/foo/bar"],
    )


# ---------- is_git_push_allowlisted — legitimate canonical-form matches ----------


def test_https_matches_shorthand_allowlist() -> None:
    """Codex carry-over: existing TOML allowlists use shorthand entries."""
    assert is_git_push_allowlisted(
        "https://github.com/WillowMilk/partner-client.git",
        ["github.com/WillowMilk/partner-client"],
    )


def test_ssh_matches_same_repo_in_https_form() -> None:
    """Same canonical triple regardless of transport — both should match."""
    assert is_git_push_allowlisted(
        "git@github.com:WillowMilk/partner-client.git",
        ["https://github.com/WillowMilk/partner-client"],
    )


def test_dot_git_suffix_does_not_affect_match() -> None:
    assert is_git_push_allowlisted(
        "https://github.com/owner/repo",
        ["github.com/owner/repo.git"],
    )


def test_host_case_does_not_affect_match() -> None:
    assert is_git_push_allowlisted(
        "https://GITHUB.com/owner/repo.git",
        ["github.com/owner/repo"],
    )


def test_one_match_in_larger_allowlist_is_enough() -> None:
    assert is_git_push_allowlisted(
        "https://github.com/WillowMilk/partner-client.git",
        [
            "github.com/other/repo",
            "gitlab.example.com/team/proj",
            "github.com/WillowMilk/partner-client",
        ],
    )


# ---------- is_git_push_allowlisted — fail-closed cases ----------


def test_empty_allowlist_returns_false() -> None:
    assert not is_git_push_allowlisted(
        "https://github.com/foo/bar.git",
        [],
    )


def test_unparseable_remote_returns_false() -> None:
    """When the URL itself can't be parsed, do NOT auto-approve."""
    assert not is_git_push_allowlisted(
        "this is not a URL",
        ["github.com/foo/bar"],
    )


def test_unparseable_allowlist_entry_does_not_match_anything() -> None:
    """A garbage allowlist entry shouldn't match any remote."""
    assert not is_git_push_allowlisted(
        "https://github.com/foo/bar.git",
        ["just-a-word"],
    )
