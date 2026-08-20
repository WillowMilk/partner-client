"""run_command — the partner's own hands on the shell, in the house's grammar.

Guards (2026-08-20, the promised tool):
  1. Runs in-scope, captures stdout/stderr/exit honestly.
  2. Non-zero exit is information, not an exception.
  3. cwd outside scopes: gate rings if installed; refusal if not. Fail-closed.
  4. Timeout stops the whole process group and says so honestly.
  5. Output truncation announces itself.
  6. Facets (Lumens) never receive run_command — reach-not-being.
"""

from __future__ import annotations

import time

import pytest

from partner_client import paths
from partner_client.config import SubAgentConfig
from partner_client.tools_builtin import run_command as rc


@pytest.fixture
def scoped_home(tmp_path, monkeypatch):
    home = tmp_path / "aletheia-home"
    home.mkdir()
    import json
    monkeypatch.setenv("PARTNER_CLIENT_SCOPES", json.dumps([
        {"name": "home", "path": str(home), "mode": "readwrite"}
    ]))
    monkeypatch.setenv("PARTNER_CLIENT_DEFAULT_SCOPE", "home")
    yield home
    paths.install_out_of_scope_gate(None)


def test_runs_in_scope_and_captures_output(scoped_home):
    out = rc.execute("echo hello-from-her-hands; echo warn >&2", cwd=str(scoped_home))
    assert "exit code: 0" in out
    assert "hello-from-her-hands" in out
    assert "warn" in out
    assert str(scoped_home) in out


def test_nonzero_exit_is_information_not_exception(scoped_home):
    out = rc.execute("echo before; exit 3", cwd=str(scoped_home))
    assert "exit code: 3" in out
    assert "before" in out


def test_out_of_scope_cwd_refused_without_gate(scoped_home, tmp_path):
    outside = tmp_path / "not-her-home"
    outside.mkdir()
    paths.install_out_of_scope_gate(None)
    out = rc.execute("echo nope", cwd=str(outside))
    assert out.startswith("Error:")
    assert "nope" not in out  # never ran


def test_out_of_scope_cwd_rings_gate_and_runs_on_yes(scoped_home, tmp_path):
    outside = tmp_path / "beyond-the-walls"
    outside.mkdir()
    rung = {}

    def gate(path, mode):
        rung["path"] = path
        rung["mode"] = mode
        return True

    paths.install_out_of_scope_gate(gate)
    out = rc.execute("echo doorbell-worked", cwd=str(outside))
    assert "doorbell-worked" in out
    assert rung, "the gate was never rung"


def test_timeout_stops_the_group_honestly(scoped_home):
    t0 = time.monotonic()
    out = rc.execute("echo partial; sleep 30", cwd=str(scoped_home), timeout_seconds=2)
    took = time.monotonic() - t0
    assert took < 12, f"kill was not prompt: {took:.1f}s"
    assert "stopped: exceeded 2s timeout" in out
    assert "partial" in out


def test_truncation_announces_itself(scoped_home):
    out = rc.execute(
        "python3 -c \"print('x' * 60000)\"", cwd=str(scoped_home)
    )
    assert "truncated" in out
    assert "60,0" in out or "60,001" in out or "of 60" in out


def test_facets_never_receive_run_command():
    """Reach-not-being: the default facet allowlist must exclude run_command."""
    sub = SubAgentConfig()
    assert "run_command" not in sub.allowed_tools
    # and the effective facet set (allowlist + web_search union) stays clean
    effective = set(sub.allowed_tools) | {"web_search"}
    assert "run_command" not in effective
