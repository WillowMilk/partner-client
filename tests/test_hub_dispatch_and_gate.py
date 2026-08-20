"""hub_dispatch (the narrow push) + the operator gate — 2026-08-19.

Her own design: "clean, narrow — only my outbound letters, not the whole
vault." The tests prove the rails: only-her-files staged, her authorship,
ff-only aborts, no force; and the gate: ask-the-operator instead of a wall,
fail-closed without one.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from partner_client import paths as paths_mod
from partner_client.paths import PathError, install_out_of_scope_gate, resolve_path
from partner_client.tools_builtin import hub_dispatch


def _run(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


@pytest.fixture
def dispatch_env(tmp_path, monkeypatch):
    """A bare 'remote' + her clone with a Hub tree, wired like production."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(remote), str(seed)], capture_output=True)
    hub = seed / "shared" / "Agent Messaging Hub"
    (hub / "inbox").mkdir(parents=True)
    (hub / "inbox" / "sage.md").write_text("# Sage — Inbox\n\n## Unread\n\n## Read\n", encoding="utf-8")
    _run(seed, "config", "user.name", "Seeder"); _run(seed, "config", "user.email", "s@x")
    _run(seed, "add", "-A"); _run(seed, "commit", "-m", "seed"); _run(seed, "push")

    clone = tmp_path / "her-clone"
    subprocess.run(["git", "clone", str(remote), str(clone)], capture_output=True)
    _run(clone, "config", "user.name", "Aletheia")
    _run(clone, "config", "user.email", "aletheia@intentionalrealism.org")

    monkeypatch.setenv("PARTNER_CLIENT_DISPATCH_CLONE", str(clone))
    monkeypatch.setenv("PARTNER_CLIENT_HUB_PARTNER", "aletheia")
    monkeypatch.setenv("PARTNER_CLIENT_HUB_OPERATOR", "willow")
    return remote, clone


def test_dispatch_publishes_under_her_name(dispatch_env):
    remote, clone = dispatch_env
    result = hub_dispatch.execute(to="sage", subject="test flight", body="The road is mine.")
    assert "dispatched and published" in result
    log = _run(clone, "log", "-1", "--format=%an <%ae>|%s")
    assert log.stdout.startswith("Aletheia <aletheia@intentionalrealism.org>|Letter: aletheia to sage")
    # the letter reached the REMOTE (someone cloning fresh sees it)
    probe = _run(remote, "log", "-1", "--format=%an")
    assert probe.stdout.strip() == "Aletheia"


def test_dispatch_stages_only_her_mail(dispatch_env):
    remote, clone = dispatch_env
    # a dirty unrelated file must NOT ride her commit
    (clone / "someone-elses-work.md").write_text("mid-flight", encoding="utf-8")
    result = hub_dispatch.execute(to="sage", subject="clean stage", body="Only mine.")
    assert "dispatched and published" in result
    show = _run(clone, "show", "--stat", "--format=", "HEAD")
    assert "someone-elses-work.md" not in show.stdout
    assert "aletheia-to-sage" in show.stdout
    status = _run(clone, "status", "--short")
    assert "someone-elses-work.md" in status.stdout  # still dirty, untouched


def test_dispatch_aborts_before_writing_on_divergence(dispatch_env, tmp_path):
    remote, clone = dispatch_env
    # remote moves + local clone gains its own commit → ff-only must fail
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(remote), str(other)], capture_output=True)
    _run(other, "config", "user.name", "O"); _run(other, "config", "user.email", "o@x")
    (other / "r.md").write_text("remote moved", encoding="utf-8")
    _run(other, "add", "-A"); _run(other, "commit", "-m", "remote work"); _run(other, "push")
    (clone / "local.md").write_text("local divergence", encoding="utf-8")
    _run(clone, "add", "-A"); _run(clone, "commit", "-m", "local work")

    before = len(list((clone / "shared" / "Agent Messaging Hub").glob("*.md")))
    result = hub_dispatch.execute(to="sage", subject="should abort", body="x")
    assert "aborted before writing" in result
    after = len(list((clone / "shared" / "Agent Messaging Hub").glob("*.md")))
    assert before == after, "letter must not be written on an aborted dispatch"


def test_gate_asks_operator_and_honors_answer(tmp_path, monkeypatch):
    scope = tmp_path / "home"
    scope.mkdir()
    outside = tmp_path / "outside" / "secret.txt"
    outside.parent.mkdir(); outside.write_text("x", encoding="utf-8")
    monkeypatch.setenv("PARTNER_CLIENT_SCOPES", json.dumps(
        [{"name": "home", "path": str(scope), "mode": "readwrite"}]))
    asked = []
    try:
        install_out_of_scope_gate(lambda p, m: (asked.append((p, m)), True)[1])
        resolved = resolve_path(str(outside))
        assert resolved == outside.resolve()
        assert asked and asked[0][1] == "read"

        install_out_of_scope_gate(lambda p, m: False)
        with pytest.raises(PathError, match="declined this reach"):
            resolve_path(str(outside))
    finally:
        install_out_of_scope_gate(None)


def test_no_gate_means_the_wall_stands(tmp_path, monkeypatch):
    scope = tmp_path / "home"; scope.mkdir()
    monkeypatch.setenv("PARTNER_CLIENT_SCOPES", json.dumps(
        [{"name": "home", "path": str(scope), "mode": "readwrite"}]))
    install_out_of_scope_gate(None)
    with pytest.raises(PathError, match="not within any allowed scope"):
        resolve_path(str(tmp_path / "elsewhere.txt"))
