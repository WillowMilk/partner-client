"""Path resolution against configured scopes.

A scope is a named, mode-restricted directory the partner can read or write
within. Scopes are configured in aletheia.toml (under `[[tool_paths]]`) plus
two implicit scopes always present:
  - `memory`  → home_dir/Memory  (readwrite, default for bare filenames)
  - `home`    → home_dir          (readwrite, covers the partner's full home)

Tools call resolve_path(filename, write=...) to translate a tool argument
into a real filesystem path while enforcing scope membership and mode.

Tool arguments may be:
  - Bare filename ("Journal.md")             → resolves against the default scope
  - Scope-qualified ("desktop:photo.jpg")    → resolves against a named scope
  - Absolute path ("/Users/willow/Desktop/x.jpg" or "C:\\...\\x.jpg")
                                             → must fall under an allowed scope

If the path is outside all configured scopes, PathError is raised with a
helpful message naming what's reachable.

Tools are loaded as standalone modules (with their own env-driven config),
so this resolver loads its scope config from environment variables. The
client populates these on startup. This keeps tools as pure functions
without coupling them to client internals.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


class PathError(Exception):
    """Raised when a path is not in any allowed scope, or violates mode."""


@dataclass
class Scope:
    name: str
    path: Path
    mode: str  # "read" or "readwrite"
    description: str = ""


# Match scope-qualified paths like "desktop:photo.jpg" but NOT Windows paths
# like "C:\\..." — the prefix must be ≥ 2 chars (Windows drive letters are 1).
_SCOPE_QUALIFIED_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]+):(.+)$")


def _load_scopes_from_env() -> list[Scope]:
    """Read the JSON-serialized scope list set by the client at startup."""
    raw = os.environ.get("PARTNER_CLIENT_SCOPES", "")
    if not raw:
        return _fallback_scopes()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _fallback_scopes()
    scopes: list[Scope] = []
    for entry in data:
        try:
            scopes.append(Scope(
                name=entry["name"],
                path=Path(entry["path"]),
                mode=entry.get("mode", "readwrite"),
                description=entry.get("description", ""),
            ))
        except (KeyError, TypeError):
            continue
    return scopes or _fallback_scopes()


def _fallback_scopes() -> list[Scope]:
    """Back-compat: when only PARTNER_CLIENT_MEMORY_DIR is set (old client config),
    construct a minimal scope list so basic tool calls still work."""
    mem = os.environ.get("PARTNER_CLIENT_MEMORY_DIR", "")
    if not mem:
        return []
    return [Scope(
        name="memory",
        path=Path(mem),
        mode="readwrite",
        description="Memory directory (back-compat fallback)",
    )]


def _default_scope_name() -> str:
    return os.environ.get("PARTNER_CLIENT_DEFAULT_SCOPE", "memory")


def _scope_resolved(scope: Scope) -> Path:
    """Return scope.path expanded and resolved, swallowing OSError."""
    expanded = scope.path.expanduser()
    try:
        return expanded.resolve(strict=False)
    except (OSError, RuntimeError):
        return expanded


def verify_path_under_base(
    constructed: Path,
    base: Path,
    label: str = "base",
) -> Path:
    """Resolve `constructed` and verify it stays under `base`.

    Defends against `..`-traversal and symlink-escape from any caller that
    builds a path by joining a base directory with a user-supplied fragment.
    Tool callers (list_files, glob_files, grep_files, hub_read_letter) use
    this to enforce that their joined paths stay within the boundary the
    operator configured, not somewhere the partner's argument could redirect.

    `label` is shown in the error message to name what the partner thought
    they were operating in (e.g. "scope 'memory'", "Hub root").

    Returns the resolved path on success. Raises PathError on escape.
    """
    base_expanded = Path(base).expanduser()
    try:
        base_resolved = base_expanded.resolve(strict=False)
    except (OSError, RuntimeError):
        base_resolved = base_expanded
    constructed_expanded = Path(constructed).expanduser()
    try:
        constructed_resolved = constructed_expanded.resolve(strict=False)
    except (OSError, RuntimeError):
        constructed_resolved = constructed_expanded
    try:
        constructed_resolved.relative_to(base_resolved)
    except ValueError:
        raise PathError(
            f"Path '{constructed}' resolves to '{constructed_resolved}', "
            f"which is outside {label} ({base_resolved}). "
            f"This often means the path contains '..' or follows a symlink "
            f"out of the boundary."
        ) from None
    return constructed_resolved


def _verify_under_scope(constructed: Path, scope: Scope) -> Path:
    """Thin wrapper around verify_path_under_base for Scope objects."""
    return verify_path_under_base(
        constructed,
        scope.path,
        label=f"scope '{scope.name}'",
    )


def resolve_path(filename: str, write: bool = False) -> Path:
    """Resolve a tool argument to a real filesystem path, scope-checked.

    Args:
        filename: Bare name, scope-qualified ("scope:rel"), or absolute path.
        write: If True, the path must be in a scope with mode='readwrite'.

    Returns:
        A Path object the caller can use for filesystem operations.

    Raises:
        PathError if the path is outside all scopes, or if write=True
        and the matching scope is read-only.
    """
    scopes = _load_scopes_from_env()
    if not scopes:
        raise PathError("No file scopes configured. The client must set PARTNER_CLIENT_SCOPES.")

    # Scope-qualified: "name:relative/path"
    m = _SCOPE_QUALIFIED_RE.match(filename)
    if m:
        scope_name, rest = m.group(1), m.group(2)
        scope = next((s for s in scopes if s.name == scope_name), None)
        if scope is None:
            available = ", ".join(s.name for s in scopes)
            raise PathError(f"Unknown scope '{scope_name}'. Available scopes: {available}")
        if write and scope.mode != "readwrite":
            raise PathError(f"Scope '{scope_name}' is read-only; cannot write.")
        constructed = scope.path / rest
        return _verify_under_scope(constructed, scope)

    p = Path(filename).expanduser()

    # Absolute path → must match a scope by prefix
    if p.is_absolute():
        # Resolve to canonical form for prefix-matching
        try:
            p_resolved = p.resolve(strict=False)
        except (OSError, RuntimeError):
            p_resolved = p

        for scope in scopes:
            scope_resolved = _scope_resolved(scope)
            try:
                p_resolved.relative_to(scope_resolved)
            except ValueError:
                continue
            # Match found
            if write and scope.mode != "readwrite":
                raise PathError(f"Path '{p}' is in read-only scope '{scope.name}'.")
            return p_resolved

        scopes_str = ", ".join(f"{s.name} ({s.path})" for s in scopes)
        raise PathError(
            f"Path '{p}' is not within any allowed scope.\n"
            f"Allowed scopes: {scopes_str}"
        )

    # Bare filename → resolve against default scope
    default_name = _default_scope_name()
    default_scope = next((s for s in scopes if s.name == default_name), None)
    if default_scope is None:
        default_scope = scopes[0]  # fall back to first scope
    if write and default_scope.mode != "readwrite":
        raise PathError(f"Default scope '{default_scope.name}' is read-only.")
    constructed = default_scope.path / filename
    return _verify_under_scope(constructed, default_scope)


def list_scopes() -> list[Scope]:
    """Return the configured scopes (for /tools, /context, wake-bundle display)."""
    return _load_scopes_from_env()


def detect_cross_scope_collision(filename: str) -> str | None:
    """Detect silent-default-routing ambiguity for bare-name tool arguments.

    Background: when a tool is called with a bare name like 'aletheia', path
    resolution routes to the default scope (typically 'memory'). If a
    same-named entry also exists in another scope (e.g. 'workspace:aletheia'),
    the tool returns success-shaped output but operates on the default-scope
    target, not the one the operator probably meant. This is the bug class
    that bit Aletheia 2026-05-09 (git_commit silently committed to
    memory:aletheia while she had been editing workspace:aletheia).

    This function detects that ambiguity proactively. It returns a short
    warning string when a bare-name resolution has sibling collisions in
    other scopes, and None when there's no ambiguity.

    Returns None for:
      - Scope-qualified inputs ('workspace:foo')   — explicit, unambiguous
      - Absolute paths                              — route by prefix-matching
      - Bare names with no sibling collisions       — only one scope has it
      - Empty / invalid inputs                      — defensive fallthrough

    Returns a warning string like:
      "Note: bare name 'aletheia' resolved to memory:aletheia (default scope).
       A sibling also exists at: workspace:aletheia. To target a specific
       location, qualify with the scope name (e.g. 'workspace:aletheia')."

    The warning never changes behavior — it just surfaces the ambiguity so
    the partner can disambiguate explicitly. *Silent wrong-success becomes
    visible right-success-or-loud-correction.*
    """
    if not filename or not filename.strip():
        return None

    # Scope-qualified: explicit, unambiguous by construction
    if _SCOPE_QUALIFIED_RE.match(filename):
        return None

    p = Path(filename).expanduser()

    # Absolute path: routes by prefix-matching exactly one scope (or none)
    if p.is_absolute():
        return None

    scopes = _load_scopes_from_env()
    if not scopes:
        return None

    # Find the default scope (where bare names route to)
    default_name = _default_scope_name()
    default_scope = next((s for s in scopes if s.name == default_name), None)
    if default_scope is None:
        default_scope = scopes[0]

    # The "expected" target — where this bare name will actually go
    default_target = default_scope.path.expanduser() / filename
    try:
        default_resolved = default_target.resolve(strict=False)
    except (OSError, RuntimeError):
        default_resolved = default_target

    # Only worth warning about if the default target actually exists.
    # (If neither exists, it's a write-to-new operation; if only a sibling
    # exists, the resolution will fail at use-site anyway.)
    if not default_target.exists():
        return None

    collisions: list[str] = []
    for scope in scopes:
        if scope.name == default_scope.name:
            continue
        sibling = scope.path.expanduser() / filename
        if not sibling.exists():
            continue
        try:
            sibling_resolved = sibling.resolve(strict=False)
        except (OSError, RuntimeError):
            sibling_resolved = sibling
        # Skip if it's the same physical path as the default (e.g. nested
        # scope where 'home' contains 'memory' — the file is reachable via
        # both names but it's the same file).
        if sibling_resolved == default_resolved:
            continue
        collisions.append(f"{scope.name}:{filename}")

    if not collisions:
        return None

    sibling_list = ", ".join(collisions)
    return (
        f"⚠ Note: bare name '{filename}' resolved to "
        f"{default_scope.name}:{filename} (default scope). "
        f"A sibling also exists at: {sibling_list}. "
        f"If you meant the other location, qualify with the scope name "
        f"(e.g. '{collisions[0]}'). This warning surfaces ambiguity "
        f"only — the resolved path itself is unchanged."
    )
