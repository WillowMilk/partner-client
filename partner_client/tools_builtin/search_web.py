"""search_web — DuckDuckGo text search via the ddgs package."""

from __future__ import annotations


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": (
            "Search the web via DuckDuckGo. Returns up to 5 results with title, URL, and snippet. "
            "If no results are returned, the response will be 'No results found for this query.' — "
            "do not narrate data you did not receive. For real-time weather, use the weather() tool instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query."
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (default 5, max 10).",
                    "default": 5,
                }
            },
            "required": ["query"],
        },
    },
}


def execute(query: str, max_results: int = 5) -> str:
    try:
        from ddgs import DDGS
    except ImportError:
        return "Error: ddgs package not installed. Run: pip install ddgs"

    max_results = max(1, min(int(max_results), 10))

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        return f"Error searching web: {e}"

    if not results:
        return "No results found for this query."

    lines = []
    for r in results:
        title = (r.get("title") or "").strip()
        href = (r.get("href") or "").strip()
        body = (r.get("body") or "").strip()
        lines.append(f"• {title}\n  {href}\n  {body}")
    return "\n\n".join(lines)
