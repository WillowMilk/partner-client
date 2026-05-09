from __future__ import annotations

import json
from pathlib import Path

import pytest

from partner_client.tools_builtin import delete_path as delete_path_tool
from partner_client.tools_builtin import move_path as move_path_tool


def configure_scopes(monkeypatch, memory_dir: Path, readonly_dir: Path) -> None:
    """Mirror the scope set used by test_paths.py so the resolver behaves
    the same way across tests."""
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
                {
                    "name": "downloads",
                    "path": str(readonly_dir),
                    "mode": "read",
                    "description": "downloads",
                },
            ]
        ),
    )
    monkeypatch.setenv("PARTNER_CLIENT_DEFAULT_SCOPE", "memory")


def test_move_path_renames_file_within_scope(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "Memory"
    downloads = tmp_path / "Downloads"
    memory.mkdir()
    downloads.mkdir()
    (memory / "draft.md").write_text("hello", encoding="utf-8")
    configure_scopes(monkeypatch, memory, downloads)

    result = move_path_tool.execute(source="draft.md", destination="final.md")

    assert "Moved file" in result
    assert not (memory / "draft.md").exists()
    assert (memory / "final.md").read_text(encoding="utf-8") == "hello"


def test_move_path_into_existing_directory_uses_unix_semantics(
    tmp_path, monkeypatch
) -> None:
    memory = tmp_path / "Memory"
    downloads = tmp_path / "Downloads"
    memory.mkdir()
    downloads.mkdir()
    (memory / "draft.md").write_text("hello", encoding="utf-8")
    (memory / "archive").mkdir()
    configure_scopes(monkeypatch, memory, downloads)

    result = move_path_tool.execute(source="draft.md", destination="archive")

    assert "Moved file" in result
    assert (memory / "archive" / "draft.md").read_text(encoding="utf-8") == "hello"
    assert not (memory / "draft.md").exists()


def test_move_path_creates_missing_destination_parents(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "Memory"
    downloads = tmp_path / "Downloads"
    memory.mkdir()
    downloads.mkdir()
    (memory / "src.md").write_text("x", encoding="utf-8")
    configure_scopes(monkeypatch, memory, downloads)

    result = move_path_tool.execute(
        source="src.md",
        destination="nested/sub/relocated.md",
    )

    assert "Moved file" in result
    assert (memory / "nested" / "sub" / "relocated.md").read_text(
        encoding="utf-8"
    ) == "x"


def test_move_path_refuses_when_source_missing(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "Memory"
    downloads = tmp_path / "Downloads"
    memory.mkdir()
    downloads.mkdir()
    configure_scopes(monkeypatch, memory, downloads)

    result = move_path_tool.execute(source="ghost.md", destination="final.md")
    assert result.startswith("Error: source does not exist")


def test_move_path_refuses_readonly_destination(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "Memory"
    downloads = tmp_path / "Downloads"
    memory.mkdir()
    downloads.mkdir()
    (memory / "draft.md").write_text("hello", encoding="utf-8")
    configure_scopes(monkeypatch, memory, downloads)

    result = move_path_tool.execute(
        source="draft.md",
        destination="downloads:saved.md",
    )

    assert result.startswith("Error resolving destination")
    assert "read-only" in result
    # Source should be untouched on a setup failure.
    assert (memory / "draft.md").exists()


def test_move_path_requires_both_arguments(monkeypatch) -> None:
    assert move_path_tool.execute(source="", destination="x").startswith(
        "Error: source"
    )
    assert move_path_tool.execute(source="x", destination="").startswith(
        "Error: destination"
    )


def test_delete_path_execute_is_a_safety_stub() -> None:
    """delete_path is special-cased in client.py; the stub must refuse to act."""
    result = delete_path_tool.execute(path="anything", recursive=True)
    assert "must be handled by the client" in result
