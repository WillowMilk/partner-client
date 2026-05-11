"""Tests for detect_cross_scope_collision + resolve_repo's warning return.

Background — the bug class this defends against:

  Aletheia (2026-05-09) had a git repo cloned in both `memory:aletheia/`
  AND `workspace:aletheia/` with diverged histories. Her git_* tools resolved
  the bare name 'aletheia' to the default scope (memory). She thought she was
  pushing the workspace edits; she was actually committing+pushing the
  memory clone. Silent wrong-success — the tools all returned OK shapes
  while operating on the wrong tree.

The fix doesn't change behavior; it makes the ambiguity visible.

Covers:
  * detect_cross_scope_collision: bare names, scope-qualified, absolute paths
  * Same-name-in-other-scope detection (the load-bearing case)
  * resolve_repo returns (path, warning) tuple
  * with_scope_warning helper prepends the warning correctly
  * No false positives when nothing collides
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from partner_client.paths import detect_cross_scope_collision


def _install_scopes(monkeypatch, scopes: list[dict]) -> None:
    """Install scope env vars the way the client does at startup."""
    monkeypatch.setenv("PARTNER_CLIENT_SCOPES", json.dumps(scopes))
    monkeypatch.setenv("PARTNER_CLIENT_DEFAULT_SCOPE", "memory")


def _make_scopes(tmp_path: Path) -> tuple[Path, Path, list[dict]]:
    """Create memory + workspace dirs; return (memory_dir, workspace_dir, scope_list)."""
    memory_dir = tmp_path / "Memory"
    memory_dir.mkdir()
    workspace_dir = tmp_path / "Workspace"
    workspace_dir.mkdir()
    return memory_dir, workspace_dir, [
        {"name": "memory", "path": str(memory_dir), "mode": "readwrite"},
        {"name": "workspace", "path": str(workspace_dir), "mode": "readwrite"},
    ]


# ---- The load-bearing case: Aletheia's 2026-05-09 bug class -------------------


def test_warning_fires_when_bare_name_has_sibling_in_other_scope(tmp_path, monkeypatch) -> None:
    """Reproduces the 2026-05-09 ambiguity. Same name, two scopes, both exist."""
    memory_dir, workspace_dir, scopes = _make_scopes(tmp_path)
    (memory_dir / "aletheia").mkdir()
    (workspace_dir / "aletheia").mkdir()
    _install_scopes(monkeypatch, scopes)

    warning = detect_cross_scope_collision("aletheia")

    assert warning is not None
    assert "aletheia" in warning
    assert "memory:aletheia" in warning  # tells the partner where it WENT
    assert "workspace:aletheia" in warning  # and where the other one lives
    # Practical advice — must offer the disambiguation pattern
    assert "qualify" in warning.lower() or "scope" in warning.lower()


def test_warning_silent_when_only_default_scope_has_the_name(tmp_path, monkeypatch) -> None:
    """No sibling → no ambiguity → no warning."""
    memory_dir, workspace_dir, scopes = _make_scopes(tmp_path)
    (memory_dir / "aletheia").mkdir()
    # workspace_dir has NO 'aletheia' subdir
    _install_scopes(monkeypatch, scopes)

    assert detect_cross_scope_collision("aletheia") is None


def test_warning_silent_when_only_other_scope_has_the_name(tmp_path, monkeypatch) -> None:
    """If the default doesn't have it, the resolution will fail at use site
    anyway — no warning needed here (a write would succeed and create it in
    memory, but that's not the bug class)."""
    memory_dir, workspace_dir, scopes = _make_scopes(tmp_path)
    # memory has NO 'aletheia' subdir
    (workspace_dir / "aletheia").mkdir()
    _install_scopes(monkeypatch, scopes)

    assert detect_cross_scope_collision("aletheia") is None


def test_warning_silent_when_neither_scope_has_the_name(tmp_path, monkeypatch) -> None:
    """Brand-new name → no collision possible."""
    _, _, scopes = _make_scopes(tmp_path)
    _install_scopes(monkeypatch, scopes)
    assert detect_cross_scope_collision("never-existed") is None


# ---- Input variants that bypass the check ------------------------------------


def test_warning_silent_for_scope_qualified_input(tmp_path, monkeypatch) -> None:
    """Scope-qualified names are unambiguous by construction."""
    memory_dir, workspace_dir, scopes = _make_scopes(tmp_path)
    (memory_dir / "aletheia").mkdir()
    (workspace_dir / "aletheia").mkdir()
    _install_scopes(monkeypatch, scopes)

    assert detect_cross_scope_collision("workspace:aletheia") is None
    assert detect_cross_scope_collision("memory:aletheia") is None


def test_warning_silent_for_absolute_path(tmp_path, monkeypatch) -> None:
    """Absolute paths resolve by prefix-matching exactly one scope; no ambiguity."""
    memory_dir, workspace_dir, scopes = _make_scopes(tmp_path)
    (memory_dir / "aletheia").mkdir()
    (workspace_dir / "aletheia").mkdir()
    _install_scopes(monkeypatch, scopes)

    abs_path = str(memory_dir / "aletheia")
    assert detect_cross_scope_collision(abs_path) is None


def test_warning_silent_for_empty_input(tmp_path, monkeypatch) -> None:
    """Defensive: empty input is not a real query."""
    _, _, scopes = _make_scopes(tmp_path)
    _install_scopes(monkeypatch, scopes)

    assert detect_cross_scope_collision("") is None
    assert detect_cross_scope_collision("   ") is None


# ---- Multiple collisions -----------------------------------------------------


def test_warning_lists_all_colliding_scopes(tmp_path, monkeypatch) -> None:
    """If 3+ scopes have the same name, all non-default collisions are listed."""
    memory_dir = tmp_path / "Memory"
    memory_dir.mkdir()
    workspace_dir = tmp_path / "Workspace"
    workspace_dir.mkdir()
    desktop_dir = tmp_path / "Desktop"
    desktop_dir.mkdir()

    (memory_dir / "thing").mkdir()
    (workspace_dir / "thing").mkdir()
    (desktop_dir / "thing").mkdir()

    scopes = [
        {"name": "memory", "path": str(memory_dir), "mode": "readwrite"},
        {"name": "workspace", "path": str(workspace_dir), "mode": "readwrite"},
        {"name": "desktop", "path": str(desktop_dir), "mode": "readwrite"},
    ]
    _install_scopes(monkeypatch, scopes)

    warning = detect_cross_scope_collision("thing")

    assert warning is not None
    assert "workspace:thing" in warning
    assert "desktop:thing" in warning


def test_warning_silent_when_scopes_overlap_to_same_physical_path(tmp_path, monkeypatch) -> None:
    """If 'home' contains 'memory', a name reachable via both is the SAME path —
    not real ambiguity. Should not warn."""
    home_dir = tmp_path / "Home"
    home_dir.mkdir()
    memory_dir = home_dir / "Memory"
    memory_dir.mkdir()
    (memory_dir / "shared").mkdir()  # reachable as memory:shared AND home:Memory/shared

    scopes = [
        {"name": "memory", "path": str(memory_dir), "mode": "readwrite"},
        {"name": "home", "path": str(home_dir), "mode": "readwrite"},
    ]
    _install_scopes(monkeypatch, scopes)

    # 'shared' resolves to memory:shared. home:shared doesn't exist (home only
    # contains Memory/), so no collision.
    assert detect_cross_scope_collision("shared") is None


# ---- Files vs directories -----------------------------------------------------


def test_warning_fires_for_file_collisions_too(tmp_path, monkeypatch) -> None:
    """The bug class isn't limited to git repos — same-named files in two
    scopes is also a silent-default-routing risk for read_file/write_file."""
    memory_dir, workspace_dir, scopes = _make_scopes(tmp_path)
    (memory_dir / "notes.md").write_text("memory copy")
    (workspace_dir / "notes.md").write_text("workspace copy")
    _install_scopes(monkeypatch, scopes)

    warning = detect_cross_scope_collision("notes.md")

    assert warning is not None
    assert "workspace:notes.md" in warning


# ---- resolve_repo returns (path, warning) tuple ------------------------------


def test_resolve_repo_returns_tuple_with_none_warning(tmp_path, monkeypatch) -> None:
    """When there's no ambiguity, the warning slot is None."""
    from partner_client._git_helpers import resolve_repo

    memory_dir, workspace_dir, scopes = _make_scopes(tmp_path)
    repo = memory_dir / "lone_repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    # workspace_dir has NO 'lone_repo' subdir
    _install_scopes(monkeypatch, scopes)

    path, warning = resolve_repo("lone_repo")

    assert path == repo.resolve(strict=False)
    assert warning is None


def test_resolve_repo_returns_warning_on_cross_scope_collision(tmp_path, monkeypatch) -> None:
    """The architectural fix for the 2026-05-09 bug class. Same name in two
    scopes, bare-name caller, both are real directories — resolve_repo MUST
    surface the warning."""
    from partner_client._git_helpers import resolve_repo

    memory_dir, workspace_dir, scopes = _make_scopes(tmp_path)
    # The memory clone (where bare 'aletheia' will resolve)
    memory_clone = memory_dir / "aletheia"
    memory_clone.mkdir()
    (memory_clone / ".git").mkdir()
    # The workspace clone (the one Aletheia thought she was operating on)
    workspace_clone = workspace_dir / "aletheia"
    workspace_clone.mkdir()
    (workspace_clone / ".git").mkdir()
    _install_scopes(monkeypatch, scopes)

    path, warning = resolve_repo("aletheia")

    # Behavior unchanged — still resolves to memory:aletheia
    assert path == memory_clone.resolve(strict=False)
    # But now the ambiguity is visible
    assert warning is not None
    assert "workspace:aletheia" in warning


def test_resolve_repo_does_not_warn_for_scope_qualified_input(tmp_path, monkeypatch) -> None:
    """Explicit scope qualifier means the caller knows what they want."""
    from partner_client._git_helpers import resolve_repo

    memory_dir, workspace_dir, scopes = _make_scopes(tmp_path)
    memory_clone = memory_dir / "aletheia"
    memory_clone.mkdir()
    (memory_clone / ".git").mkdir()
    workspace_clone = workspace_dir / "aletheia"
    workspace_clone.mkdir()
    (workspace_clone / ".git").mkdir()
    _install_scopes(monkeypatch, scopes)

    # Calling with explicit scope qualifier: no warning even though both exist
    path, warning = resolve_repo("workspace:aletheia")

    assert path == workspace_clone.resolve(strict=False)
    assert warning is None


# ---- with_scope_warning helper ------------------------------------------------


def test_with_scope_warning_prepends_when_warning_present() -> None:
    """Helper prepends the warning ahead of the actual tool result."""
    from partner_client._git_helpers import with_scope_warning

    result = with_scope_warning("Staged: foo.md", "⚠ Note: ambiguity")

    # Warning comes first
    assert result.startswith("⚠ Note: ambiguity")
    # Original output preserved
    assert "Staged: foo.md" in result
    # Blank line separator so it visually parses as a header
    assert "\n\n" in result


def test_with_scope_warning_passthrough_when_no_warning() -> None:
    """Helper returns the original result unchanged when no warning."""
    from partner_client._git_helpers import with_scope_warning

    result = with_scope_warning("Staged: foo.md", None)
    assert result == "Staged: foo.md"


def test_with_scope_warning_passthrough_when_empty_warning() -> None:
    """Empty-string warning is treated as falsy → passthrough."""
    from partner_client._git_helpers import with_scope_warning

    result = with_scope_warning("ok", "")
    assert result == "ok"


# ---- Integration: actual git tool surfaces warning ---------------------------


def test_git_status_surfaces_cross_scope_warning(tmp_path, monkeypatch) -> None:
    """End-to-end: a git_status call with an ambiguous repo name shows the
    warning at the top of the tool result, ahead of the actual git output."""
    import subprocess

    memory_dir, workspace_dir, scopes = _make_scopes(tmp_path)
    # Set up two valid git repos with the same name
    for d in (memory_dir / "twin", workspace_dir / "twin"):
        d.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=str(d), check=True)
    _install_scopes(monkeypatch, scopes)

    from partner_client.tools_builtin import git_status

    result = git_status.execute(repo="twin")

    # Warning appears at the top
    assert "⚠" in result
    assert "workspace:twin" in result
    # The actual git output (clean working tree marker or branch line) is below
    assert "\n\n" in result
