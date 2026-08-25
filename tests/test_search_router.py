"""web_search router guards — free-only failover (litigators' docket, 2026-08-25)."""
import types


def _cfg(active="searxng"):
    b = {
        "searxng": types.SimpleNamespace(type="http", url="http://localhost:1", label="SearXNG · local · free", server="", tool="", cost="free"),
        "tavily": types.SimpleNamespace(type="mcp", server="tavily", tool="search", label="Tavily · metered", url="", cost="metered"),
        "ddg": types.SimpleNamespace(type="ddg", label="DuckDuckGo · free", url="", server="", tool="", cost="free"),
    }
    return types.SimpleNamespace(search=types.SimpleNamespace(active=active, max_results=5, backends=b))


def test_failover_reaches_free_engine_with_honest_label(monkeypatch):
    from partner_client import search_router as sr
    monkeypatch.setattr(sr, "_search_http", lambda *a, **k: (_ for _ in ()).throw(OSError("connection refused")))
    monkeypatch.setattr(sr, "_search_ddg", lambda q, n: "1. Result — https://x")
    out = sr.run_search(_cfg(), "test query")
    assert "DuckDuckGo (fallback" in out            # provenance: names the fallback
    assert "SearXNG · local · free failed" in out   # provenance: names the failure
    assert "1. Result" in out                       # the capability survived


def test_failover_never_reaches_metered(monkeypatch):
    """Cost honesty: auto-failover must not spend the operator's money."""
    from partner_client import search_router as sr
    calls = []
    monkeypatch.setattr(sr, "_search_http", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    monkeypatch.setattr(sr, "_search_mcp", lambda *a, **k: calls.append("METERED") or "paid result")
    monkeypatch.setattr(sr, "_search_ddg", lambda q, n: (_ for _ in ()).throw(OSError("also down")))
    out = sr.run_search(_cfg(), "q")
    assert calls == []                              # tavily never touched
    assert "failed" in out and "fallback engine also failed" in out


def test_no_failover_loop_when_active_is_ddg(monkeypatch):
    from partner_client import search_router as sr
    n_calls = {"n": 0}
    def _boom(q, n):
        n_calls["n"] += 1
        raise OSError("ddg down")
    monkeypatch.setattr(sr, "_search_ddg", _boom)
    out = sr.run_search(_cfg(active="ddg"), "q")
    assert n_calls["n"] == 1                        # no self-fallback loop
    assert "failed" in out
