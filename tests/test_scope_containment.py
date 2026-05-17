"""Boundary-hardening tests for Slice 1 Fix #1 — file-discovery scope escape.

These verify that list_files / glob_files / grep_files cannot reach outside
the configured scope via:
  - subpath containing `..` (climbs out)
  - subpath being an absolute path (Python's Path / abs returns abs)
  - glob/grep pattern containing `..`
  - glob/grep pattern being absolute

And that verify_path_under_base catches arbitrary base-relative escapes
(direct helper test, independent of any one tool).
"""

from __future__ import annotations

import json

import pytest

from partner_client.paths import PathError, verify_path_under_base
from partner_client.tools_builtin import list_files, glob_files, grep_files


def _configure_one_scope(monkeypatch, memory_dir) -> None:
    monkeypatch.setenv(
        "PARTNER_CLIENT_SCOPES",
        json.dumps(
            [
                {
                    "name": "memory",
                    "path": str(memory_dir),
                    "mode": "readwrite",
                    "description": "memory",
                }
            ]
        ),
    )
    monkeypatch.setenv("PARTNER_CLIENT_DEFAULT_SCOPE", "memory")


# ---------- verify_path_under_base (direct helper) ----------


def test_verify_path_under_base_accepts_nested(tmp_path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    (base / "child").mkdir()
    resolved = verify_path_under_base(base / "child" / "file.md", base)
    assert resolved == (base / "child" / "file.md").resolve()


def test_verify_path_under_base_rejects_dotdot_climb(tmp_path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    with pytest.raises(PathError, match="outside"):
        verify_path_under_base(base / ".." / "outside.md", base)


def test_verify_path_under_base_label_appears_in_error(tmp_path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    with pytest.raises(PathError, match="Hub root"):
        verify_path_under_base(base / ".." / "x", base, label="Hub root")


# ---------- list_files ----------


def test_list_files_rejects_dotdot_subpath(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "Memory"
    memory.mkdir()
    (tmp_path / "Outside").mkdir()
    _configure_one_scope(monkeypatch, memory)

    result = list_files.execute(scope="memory", subpath="..")
    assert "outside" in result.lower()
    assert "Error" in result


def test_list_files_rejects_absolute_subpath(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "Memory"
    memory.mkdir()
    _configure_one_scope(monkeypatch, memory)

    # On Path / abs join, the absolute right side wins — would silently
    # leave the scope without the containment check.
    result = list_files.execute(scope="memory", subpath=str(tmp_path / "Outside"))
    assert "Error" in result
    assert "outside" in result.lower()


def test_list_files_accepts_legitimate_subpath(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "Memory"
    sub = memory / "Letters"
    sub.mkdir(parents=True)
    (sub / "first.md").write_text("hello")
    _configure_one_scope(monkeypatch, memory)

    result = list_files.execute(scope="memory", subpath="Letters")
    assert "first.md" in result
    assert "Error" not in result


# ---------- glob_files ----------


def test_glob_files_rejects_dotdot_pattern(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "Memory"
    memory.mkdir()
    _configure_one_scope(monkeypatch, memory)

    result = glob_files.execute(pattern="../*.md", scope="memory")
    assert "Error" in result
    assert ".." in result


def test_glob_files_rejects_dotdot_in_middle_segment(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "Memory"
    memory.mkdir()
    _configure_one_scope(monkeypatch, memory)

    result = glob_files.execute(pattern="Letters/../../escape/*.md", scope="memory")
    assert "Error" in result
    assert ".." in result


def test_glob_files_rejects_absolute_pattern_posix(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "Memory"
    memory.mkdir()
    _configure_one_scope(monkeypatch, memory)

    result = glob_files.execute(pattern="/etc/passwd", scope="memory")
    assert "Error" in result
    assert "absolute" in result.lower()


def test_glob_files_rejects_absolute_pattern_windows(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "Memory"
    memory.mkdir()
    _configure_one_scope(monkeypatch, memory)

    result = glob_files.execute(pattern="C:\\Windows\\System32\\*.dll", scope="memory")
    assert "Error" in result
    assert "absolute" in result.lower()


def test_glob_files_accepts_legitimate_recursive_pattern(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "Memory"
    (memory / "Letters").mkdir(parents=True)
    (memory / "Letters" / "a.md").write_text("a")
    (memory / "notes.md").write_text("n")
    _configure_one_scope(monkeypatch, memory)

    result = glob_files.execute(pattern="**/*.md", scope="memory")
    assert "a.md" in result or "Letters" in result
    assert "notes.md" in result
    assert "Error" not in result


# ---------- grep_files ----------


def test_grep_files_rejects_dotdot_glob(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "Memory"
    memory.mkdir()
    _configure_one_scope(monkeypatch, memory)

    result = grep_files.execute(pattern="secret", scope="memory", glob="../*.md")
    assert "Error" in result
    assert ".." in result


def test_grep_files_rejects_absolute_glob(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "Memory"
    memory.mkdir()
    _configure_one_scope(monkeypatch, memory)

    result = grep_files.execute(pattern="secret", scope="memory", glob="/etc/*.conf")
    assert "Error" in result
    assert "absolute" in result.lower()


def test_grep_files_accepts_legitimate_glob(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "Memory"
    memory.mkdir()
    (memory / "Journal.md").write_text("hello almonds\nhello world\n")
    _configure_one_scope(monkeypatch, memory)

    result = grep_files.execute(pattern="almonds", scope="memory", glob="**/*.md")
    assert "almonds" in result
    assert "Error" not in result
