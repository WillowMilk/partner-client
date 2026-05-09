from __future__ import annotations

import json

import pytest

from partner_client.paths import PathError, resolve_path


def configure_scopes(monkeypatch, memory_dir, readonly_dir) -> None:
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


def test_bare_paths_resolve_inside_default_scope(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "Memory"
    downloads = tmp_path / "Downloads"
    memory.mkdir()
    downloads.mkdir()
    configure_scopes(monkeypatch, memory, downloads)

    assert resolve_path("Journal.md", write=True) == memory / "Journal.md"


def test_scope_qualified_traversal_is_rejected(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "Memory"
    downloads = tmp_path / "Downloads"
    memory.mkdir()
    downloads.mkdir()
    configure_scopes(monkeypatch, memory, downloads)

    with pytest.raises(PathError, match="outside scope"):
        resolve_path("memory:../escape.txt", write=True)


def test_readonly_scope_rejects_writes(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "Memory"
    downloads = tmp_path / "Downloads"
    memory.mkdir()
    downloads.mkdir()
    target = downloads / "note.txt"
    target.write_text("hello", encoding="utf-8")
    configure_scopes(monkeypatch, memory, downloads)

    with pytest.raises(PathError, match="read-only"):
        resolve_path(str(target), write=True)
