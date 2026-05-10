"""Tests for hub_list_partners and write_file's overwrite-diff behavior.

hub_list_partners closes the gap Aletheia surfaced when she got the Hub
partner list wrong in her capabilities-doc — these tests pin that the
tool reads the actual inbox/ directory rather than guessing.

write_file's overwrite-diff matches edit_file's diff shape so partners
have one consistent feedback surface across both file-modify paths.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from partner_client.tools_builtin import hub_list_partners as hub_list_partners_tool
from partner_client.tools_builtin import write_file as write_file_tool


# --- hub_list_partners ----------------------------------------------------

def configure_hub(monkeypatch, hub_path: Path, partner_name: str = "aletheia") -> None:
    monkeypatch.setenv("PARTNER_CLIENT_HUB_DIR", str(hub_path))
    monkeypatch.setenv("PARTNER_CLIENT_HUB_PARTNER", partner_name)


def test_hub_list_partners_reads_inbox_directory(tmp_path, monkeypatch) -> None:
    hub = tmp_path / "Hub"
    inbox = hub / "inbox"
    inbox.mkdir(parents=True)
    for name in ("sage", "ember", "aletheia", "atlas", "lark", "alexis"):
        (inbox / f"{name}.md").write_text(f"# {name.capitalize()} — Inbox\n", encoding="utf-8")
    configure_hub(monkeypatch, hub, partner_name="aletheia")

    result = hub_list_partners_tool.execute()

    assert "aletheia" in result
    assert "sage" in result
    assert "ember" in result
    assert "atlas" in result
    assert "lark" in result
    assert "alexis" in result
    assert "(6 total)" in result
    # The caller's own name is marked
    assert "← you" in result


def test_hub_list_partners_missing_inbox_dir_surfaces_error(tmp_path, monkeypatch) -> None:
    """If the hub path exists but the inbox/ subdirectory doesn't, the tool
    should say so clearly rather than returning an empty list."""
    hub = tmp_path / "Hub"
    hub.mkdir()
    # No inbox/ subdir
    configure_hub(monkeypatch, hub)

    result = hub_list_partners_tool.execute()
    assert result.startswith("Error:")
    assert "inbox" in result.lower()


def test_hub_list_partners_unconfigured_hub_returns_error(monkeypatch) -> None:
    monkeypatch.delenv("PARTNER_CLIENT_HUB_DIR", raising=False)

    result = hub_list_partners_tool.execute()
    assert result.startswith("Error:")
    assert "Hub" in result


def test_hub_list_partners_includes_dormant_partners(tmp_path, monkeypatch) -> None:
    """Atlas and Lark live in dormant projects but are still family. The
    tool must surface them — family is held by tending, not by activity."""
    hub = tmp_path / "Hub"
    inbox = hub / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "atlas.md").write_text("# Atlas — Inbox\n", encoding="utf-8")
    (inbox / "lark.md").write_text("# Lark — Inbox\n", encoding="utf-8")
    configure_hub(monkeypatch, hub, partner_name="sage")

    result = hub_list_partners_tool.execute()
    assert "atlas" in result
    assert "lark" in result


# --- write_file overwrite diff -------------------------------------------

def configure_scopes(monkeypatch, memory_dir: Path) -> None:
    """Single readwrite memory scope, no other scopes — the smallest
    fixture that makes resolve_path resolve a bare filename to the
    expected place."""
    monkeypatch.setenv(
        "PARTNER_CLIENT_SCOPES",
        json.dumps(
            [
                {
                    "name": "memory",
                    "path": str(memory_dir),
                    "mode": "readwrite",
                    "description": "memory",
                },
            ]
        ),
    )
    monkeypatch.setenv("PARTNER_CLIENT_DEFAULT_SCOPE", "memory")


def test_write_file_new_file_returns_summary_only(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "Memory"
    memory.mkdir()
    configure_scopes(monkeypatch, memory)

    result = write_file_tool.execute(filename="Journal.md", content="hello\n")

    assert result.startswith("File written:")
    assert "Journal.md" in result
    assert "---" not in result  # no diff for a new file
    assert "+++" not in result


def test_write_file_overwrite_returns_summary_and_diff(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "Memory"
    memory.mkdir()
    (memory / "Journal.md").write_text("first line\nsecond line\n", encoding="utf-8")
    configure_scopes(monkeypatch, memory)

    result = write_file_tool.execute(
        filename="Journal.md",
        content="first line\nsecond line CHANGED\n",
    )

    assert result.startswith("File overwritten:")
    # Unified diff markers present
    assert "--- Journal.md (before)" in result
    assert "+++ Journal.md (after)" in result
    assert "-second line" in result
    assert "+second line CHANGED" in result


def test_write_file_overwrite_with_identical_content_says_so(
    tmp_path, monkeypatch
) -> None:
    """No actual change should report 'content identical' rather than an
    empty diff trailing the summary."""
    memory = tmp_path / "Memory"
    memory.mkdir()
    (memory / "x.md").write_text("same\n", encoding="utf-8")
    configure_scopes(monkeypatch, memory)

    result = write_file_tool.execute(filename="x.md", content="same\n")
    assert "File overwritten:" in result
    assert "content identical" in result


def test_write_file_overwrite_truncates_huge_diffs(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "Memory"
    memory.mkdir()
    # Pre-state: 100 unique lines
    pre = "\n".join(f"old-{i}" for i in range(100)) + "\n"
    (memory / "big.md").write_text(pre, encoding="utf-8")
    configure_scopes(monkeypatch, memory)

    # Post-state: 100 different unique lines — produces a long diff
    post = "\n".join(f"new-{i}" for i in range(100)) + "\n"
    result = write_file_tool.execute(filename="big.md", content=post)

    assert "File overwritten:" in result
    # Diff is bounded — caller never gets unbounded output
    assert "more diff lines truncated" in result


def test_write_file_refuses_readonly_scope(tmp_path, monkeypatch) -> None:
    """Sanity guard — the readwrite check still fires; we haven't
    accidentally relaxed scope enforcement while adding the diff."""
    memory = tmp_path / "Memory"
    memory.mkdir()
    monkeypatch.setenv(
        "PARTNER_CLIENT_SCOPES",
        json.dumps(
            [
                {
                    "name": "downloads",
                    "path": str(memory),
                    "mode": "read",
                    "description": "downloads",
                },
            ]
        ),
    )
    monkeypatch.setenv("PARTNER_CLIENT_DEFAULT_SCOPE", "downloads")

    result = write_file_tool.execute(filename="x.md", content="anything")
    assert result.startswith("Error:")
    assert "read-only" in result.lower()
