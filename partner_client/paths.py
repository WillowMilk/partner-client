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


def _verify_under_scope(constructed: Path, scope: Scope) -> Path:
    """Resolve `constructed` and verify it stays under scope.path.

    Defends against `..`-traversal from scope-qualified or bare-filename
    inputs (e.g. 'memory:../../etc/passwd'). The absolute-path branch in
    resolve_path is already defended because Path.resolve() collapses `..`
    before its relative_to check; this helper applies the same protection
    to the relative-construction branches.

    Returns the resolved path on success. Raises PathError if the resolved
    path is outside the scope.
    """
    scope_resolved = _scope_resolved(scope)
    constructed_expanded = constructed.expanduser()
    try:
        constructed_resolved = constructed_expanded.resolve(strict=False)
    except (OSError, RuntimeError):
        constructed_resolved = constructed_expanded
    try:
        constructed_resolved.relative_to(scope_resolved)
    except ValueError:
        raise PathError(
            f"Path '{constructed}' resolves to '{constructed_resolved}', "
            f"which is outside scope '{scope.name}' ({scope_resolved}). "
            f"This often means the path contains '..' or follows a symlink "
            f"out of the scope."
        ) from None
    return constructed_resolved


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
