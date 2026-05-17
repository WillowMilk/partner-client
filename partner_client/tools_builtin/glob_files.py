"""glob_files — find files matching a glob pattern within a scope.

Returns matching paths sorted by modification time, newest first.
Capped at 200 results to keep tool output readable.
"""

from __future__ import annotations


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "glob_files",
        "description": (
            "Find files matching a glob pattern within one of your file scopes. "
            "Patterns: '*.md' (top-level only), '**/*.py' (recursive), "
            "'Letters/*ember*.md' (subdirectory + substring). By default "
            "searches your memory directory. Pass scope='home' or another "
            "configured scope to search elsewhere. Returns matching paths "
            "sorted by modification time (newest first). Capped at 200 results."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "Glob pattern. '*.md' matches top-level only; '**/*.md' "
                        "recurses; 'sub/*.md' matches one level under 'sub/'."
                    ),
                },
                "scope": {
                    "type": "string",
                    "description": "Scope to search. Defaults to 'memory'.",
                },
            },
            "required": ["pattern"],
        },
    },
}


_MAX_RESULTS = 200


def execute(pattern: str, scope: str = "memory") -> str:
    try:
        from partner_client.paths import list_scopes
    except ImportError:
        return "Error: path resolver not available; client may be misconfigured."

    scopes = list_scopes()
    if not scopes:
        return "Error: no file scopes configured."
    target = next((s for s in scopes if s.name == scope), None)
    if target is None:
        available = ", ".join(s.name for s in scopes)
        return f"Error: unknown scope '{scope}'. Available scopes: {available}"

    # Defense: refuse patterns that try to leave the scope. `..` in any
    # segment can climb out; absolute patterns (`/etc/*`, `C:\...`) bypass
    # the base join entirely. We reject these explicitly AND filter results
    # below in case Path.glob's behavior varies across Python versions.
    normalized = pattern.replace("\\", "/")
    pattern_parts = normalized.split("/")
    if ".." in pattern_parts:
        return f"Error: pattern '{pattern}' contains '..', which is not permitted."
    if normalized.startswith("/") or (len(normalized) >= 2 and normalized[1] == ":"):
        return f"Error: pattern '{pattern}' is absolute; provide a path relative to scope '{scope}'."

    base = target.path.expanduser()
    if not base.is_dir():
        return f"Error: scope '{scope}' path is not a directory: {base}"

    try:
        base_resolved = base.resolve(strict=False)
    except (OSError, RuntimeError):
        base_resolved = base

    try:
        matches = []
        for p in base.glob(pattern):
            if not p.is_file():
                continue
            try:
                p.resolve(strict=False).relative_to(base_resolved)
            except (ValueError, OSError):
                continue  # defense in depth: drop anything that resolved outside base
            matches.append(p)
    except (ValueError, OSError) as e:
        return f"Error globbing '{pattern}': {e}"

    # Sort by mtime descending; tolerate stat() failures on individual entries
    try:
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        matches.sort(key=lambda p: str(p))

    if not matches:
        return f"No files match '{pattern}' in scope '{scope}' ({base})."

    truncated = len(matches) > _MAX_RESULTS
    matches = matches[:_MAX_RESULTS]

    plural = "" if len(matches) == 1 else "es"
    header = f"# {len(matches)} match{plural} for '{pattern}' in scope '{scope}':"
    lines = [header]
    for p in matches:
        try:
            rel = p.relative_to(base)
        except ValueError:
            rel = p
        lines.append(str(rel))

    if truncated:
        lines.append(f"\n(+more matches truncated past {_MAX_RESULTS}; refine the pattern)")

    return "\n".join(lines)
