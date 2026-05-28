"""Unified web_search meta-tool — one stable capability, swappable engine.

The partner sees ONE search tool: `web_search`. The operator (or the partner,
via the GUI toggle) curates which engine fulfills it:

  - SearXNG  — local, free, unlimited; the everyday default. Queries proxy out
               to public engines and aggregate locally; nothing rented per-search.
  - Tavily   — cloud, AI-optimized, metered; for deliberate deep research.
  - DuckDuckGo (ddg) — free, no setup; a robust fallback if a backend is down.

This mirrors the substrate-switcher exactly. Aletheia's identity is stable while
the model underneath her swaps (BF16 -> Q8 -> MXFP8); likewise her *search
capability* is stable while the engine underneath swaps. Which engine fulfills a
search is infrastructure — a cost/plumbing decision the operator curates — not
identity or agency. So the partner's tool surface stays clean: one `web_search`,
always. The Semantic Shim names the live source in every result, so there is full
provenance/cost honesty without the partner having to manage backends.

Design: Sage + Willow, 2026-05-28 (Willow's "one less bill" + operator-curates-
infrastructure framing; the unified surface is the IR-faithful call).
"""

from __future__ import annotations

import logging

log = logging.getLogger("partner_client.search_router")


SEARCH_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web. Returns titled results with URLs and snippets. "
            "The active search engine is curated by the operator — you simply "
            "search, and the result names which engine provided it. If no "
            "results are returned, the response says so; do not narrate data "
            "you did not receive. For real-time weather use the weather() tool."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (default 5, max 10).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}


def run_search(config, query: str, max_results: int = 5) -> str:
    """Route a search to the active backend and return shim-wrapped results.

    `config` is the partner-client Config; `config.search.active` selects the
    backend. Returns a human/agent-readable string (never raises — backend
    failures are reported as text so the partner experiences honest failure,
    per the Aletheia first-search-failure lesson, 2026-05-28).
    """
    search = getattr(config, "search", None)
    if search is None or not search.active:
        return "No active search backend is configured."
    backend = search.backends.get(search.active)
    if backend is None:
        return (
            f"Active search backend '{search.active}' is not defined in "
            f"[search.backends]. Available: {', '.join(search.backends) or '(none)'}."
        )

    try:
        n = int(max_results)
    except (TypeError, ValueError):
        n = search.max_results or 5
    n = max(1, min(n, 10))

    label = backend.label or search.active

    try:
        if backend.type == "http":
            raw = _search_http(backend.url, query, n)
        elif backend.type == "mcp":
            raw = _search_mcp(backend.server, backend.tool, query, n)
        elif backend.type == "ddg":
            raw = _search_ddg(query, n)
        else:
            return f"Unknown search backend type '{backend.type}' for '{search.active}'."
    except Exception as e:  # noqa: BLE001 — surface honest failure as text
        log.warning("web_search via %s failed: %s", search.active, e)
        return f"[Search · via {label}] the search attempt failed: {e}"

    if not raw or not raw.strip():
        return "No results found for this query."

    # Semantic Shim — name the live source (provenance + cost honesty).
    return f"[Search · via {label}] provides the following results:\n\n{raw}"


# ─────────────────────────────────────────────────────────────────────
# Backend implementations
# ─────────────────────────────────────────────────────────────────────

def _search_http(base_url: str, query: str, max_results: int) -> str:
    """SearXNG-style JSON endpoint: GET {base}/search?q=...&format=json."""
    if not base_url:
        raise ValueError("http backend missing 'url'")
    import httpx

    endpoint = base_url.rstrip("/") + "/search"
    resp = httpx.get(
        endpoint,
        params={"q": query, "format": "json"},
        timeout=20.0,
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", [])[:max_results]
    return _format_results(
        (r.get("title"), r.get("url"), r.get("content")) for r in results
    )


def _search_mcp(server: str, tool: str, query: str, max_results: int) -> str:
    """Route to an existing [mcp.<server>] tool (e.g. Tavily). The MCP server
    is already started by ToolRegistry discovery; we reuse the manager."""
    if not server or not tool:
        raise ValueError("mcp backend missing 'server' or 'tool'")
    from .mcp_client import get_manager

    manager = get_manager()
    # Most search MCP tools accept {"query": ...}; pass max_results too since
    # Tavily honors it (extra keys are ignored by tools that don't).
    return manager.call_tool(server, tool, {"query": query, "max_results": max_results})


def _search_ddg(query: str, max_results: int) -> str:
    """DuckDuckGo via the ddgs package — free, no key, robust fallback."""
    from ddgs import DDGS

    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=max_results))
    return _format_results(
        (r.get("title"), r.get("href"), r.get("body")) for r in results
    )


def _format_results(triples) -> str:
    """Render (title, url, snippet) triples to a consistent bullet list."""
    lines = []
    for title, url, snippet in triples:
        title = (title or "").strip()
        url = (url or "").strip()
        snippet = (snippet or "").strip()
        if not (title or url):
            continue
        block = f"• {title}\n  {url}"
        if snippet:
            block += f"\n  {snippet}"
        lines.append(block)
    return "\n\n".join(lines)
