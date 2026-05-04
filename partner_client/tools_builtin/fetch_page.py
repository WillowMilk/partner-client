"""fetch_page — fetch a web page and return its visible text content.

Identifies as a polite user-agent so most servers will respond. Strips script,
style, nav, header, and footer elements before returning text.
"""

from __future__ import annotations


USER_AGENT = (
    "Mozilla/5.0 (compatible; partner-client/1.0; "
    "+https://intentionalrealism.org)"
)


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "fetch_page",
        "description": (
            "Fetch a web page and return its visible text content (up to 10,000 characters). "
            "Use this when you have a specific URL to read. For exploratory queries, use search_web first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The full URL to fetch (must include http:// or https://)."
                }
            },
            "required": ["url"],
        },
    },
}


def execute(url: str) -> str:
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return "Error: requests and beautifulsoup4 packages required."

    if not (url.startswith("http://") or url.startswith("https://")):
        return f"Error: URL must start with http:// or https:// — got: {url}"

    try:
        response = requests.get(
            url,
            timeout=10,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
    except Exception as e:
        return f"Error fetching {url}: {e}"

    try:
        soup = BeautifulSoup(response.text, "html.parser")
        for element in soup(["script", "style", "nav", "footer", "header", "aside"]):
            element.decompose()
        text = soup.get_text(separator="\n", strip=True)
    except Exception as e:
        return f"Error parsing {url}: {e}"

    if len(text) > 10000:
        text = text[:10000] + "\n\n[…truncated at 10000 chars…]"
    return text
