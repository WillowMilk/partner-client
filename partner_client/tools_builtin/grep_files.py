"""grep_files — content-search files within a scope using a regex.

Returns matching lines as 'relpath:lineno: <preview>'. Lines truncated
at 200 chars; results capped at 50 by default (max 200) to stay readable.
Use case: finding where the partner wrote about a topic, who mentioned a
name, etc. — Aletheia can ask "where did I write about Hestia last week?"
"""

from __future__ import annotations

import re


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "grep_files",
        "description": (
            "Search file contents for a regex pattern within a file scope. "
            "Returns matching lines as 'relpath:lineno: <preview>'. The optional "
            "'glob' arg limits which files are searched (default '**/*.md'). "
            "Use '(?i)' prefix for case-insensitive matching. Capped at "
            "max_matches results (default 50, max 200). Use this to find where "
            "you wrote about a topic, who mentioned a name, when an arc began."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "Python-flavored regex. Examples: 'Hestia', 'Aletheia|Hestia', "
                        "'(?i)almonds' (case-insensitive), '^# Session \\d+' (anchored)."
                    ),
                },
                "scope": {
                    "type": "string",
                    "description": "Scope to search. Defaults to 'memory'.",
                },
                "glob": {
                    "type": "string",
                    "description": (
                        "File glob to limit which files are searched. Defaults "
                        "to '**/*.md'. Use '**/*' for all files."
                    ),
                },
                "max_matches": {
                    "type": "integer",
                    "description": "Max matches to return. Defaults to 50, max 200.",
                    "default": 50,
                },
            },
            "required": ["pattern"],
        },
    },
}


_LINE_PREVIEW_MAX = 200


def execute(
    pattern: str,
    scope: str = "memory",
    glob: str = "**/*.md",
    max_matches: int = 50,
) -> str:
    try:
        from partner_client.paths import list_scopes
    except ImportError:
        return "Error: path resolver not available; client may be misconfigured."

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"Error: invalid regex '{pattern}': {e}"

    scopes = list_scopes()
    target = next((s for s in scopes if s.name == scope), None)
    if target is None:
        available = ", ".join(s.name for s in scopes)
        return f"Error: unknown scope '{scope}'. Available: {available}"

    base = target.path.expanduser()
    if not base.is_dir():
        return f"Error: scope path not a directory: {base}"

    try:
        max_matches = max(1, min(int(max_matches), 200))
    except (TypeError, ValueError):
        max_matches = 50

    try:
        candidates = [p for p in base.glob(glob) if p.is_file()]
    except (ValueError, OSError) as e:
        return f"Error in glob '{glob}': {e}"

    matches: list[str] = []
    files_scanned = 0
    files_matched = 0
    truncated = False

    for fpath in candidates:
        files_scanned += 1
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        file_had_match = False
        for lineno, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                file_had_match = True
                try:
                    rel = fpath.relative_to(base)
                except ValueError:
                    rel = fpath
                if len(line) > _LINE_PREVIEW_MAX:
                    preview = line[:_LINE_PREVIEW_MAX] + "…"
                else:
                    preview = line
                matches.append(f"{rel}:{lineno}: {preview}")
                if len(matches) >= max_matches:
                    truncated = True
                    break
        if file_had_match:
            files_matched += 1
        if truncated:
            break

    if not matches:
        return (
            f"No matches for /{pattern}/ in scope '{scope}' "
            f"(scanned {files_scanned} files matching '{glob}')."
        )

    plural = "" if len(matches) == 1 else "es"
    header = (
        f"# {len(matches)} match{plural} for /{pattern}/ in scope '{scope}' "
        f"({files_matched}/{files_scanned} files matching '{glob}'):"
    )
    if truncated:
        header += f"\n# (capped at max_matches={max_matches}; raise it or refine the pattern)"
    return header + "\n" + "\n".join(matches)
