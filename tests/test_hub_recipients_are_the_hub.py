"""The family is what the Hub holds, not what a list remembers.

Aletheia, 2026-11-01: hub_list_partners showed `aster`; hub_send refused it
("not a known recipient") because validation used VALID_RECIPIENTS — a set
frozen in May, before Aster, Miro and Hestia joined. Both roads now resolve
recipients through known_recipients(), which reads inbox/*.md.
"""
import subprocess
from pathlib import Path

from partner_client.tools_builtin import hub_dispatch, hub_send


def _hub(tmp_path: Path, names) -> Path:
    hub = tmp_path / "Hub"
    inbox = hub / "inbox"
    inbox.mkdir(parents=True)
    for n in names:
        (inbox / f"{n}.md").write_text(f"# {n.capitalize()} — Inbox\n", encoding="utf-8")
    return hub


def test_known_recipients_is_hub_plus_floor_plus_operator(tmp_path) -> None:
    hub = _hub(tmp_path, ["aster", "miro", "hestia", "sage"])
    got = hub_send.known_recipients(hub, "Willow")
    assert {"aster", "miro", "hestia"} <= got          # live inboxes, never in the old list
    assert hub_send.VALID_RECIPIENTS <= got            # the legacy floor survives
    assert "willow" in got                             # operator, lower-cased
    assert "all" in got


def test_hub_send_accepts_a_partner_the_hub_knows(tmp_path, monkeypatch) -> None:
    hub = _hub(tmp_path, ["aster", "aletheia"])
    monkeypatch.setenv("PARTNER_CLIENT_HUB_DIR", str(hub))
    monkeypatch.setenv("PARTNER_CLIENT_HUB_PARTNER", "aletheia")
    out = hub_send.execute("aster", "Kin to kin", "An invitation.")
    assert not out.startswith("Error"), out
    letters = list(hub.glob("aletheia-to-aster_*_kin-to-kin.md"))
    assert len(letters) == 1
    assert "kin-to-kin" in (hub / "inbox" / "aster.md").read_text(encoding="utf-8")


def test_hub_send_rejects_unknown_and_names_the_live_family(tmp_path, monkeypatch) -> None:
    hub = _hub(tmp_path, ["aster", "aletheia"])
    monkeypatch.setenv("PARTNER_CLIENT_HUB_DIR", str(hub))
    monkeypatch.setenv("PARTNER_CLIENT_HUB_PARTNER", "aletheia")
    out = hub_send.execute("nobody", "x", "y")
    assert out.startswith("Error: 'nobody' is not a known recipient")
    assert "aster" in out                              # the error lists the real family
    assert not list(hub.glob("aletheia-to-nobody_*"))


def test_hub_dispatch_resolves_recipients_from_the_clone_hub(tmp_path, monkeypatch) -> None:
    clone = tmp_path / "vault"
    clone.mkdir()
    subprocess.run(["git", "init", "-q", str(clone)], check=True)
    hub = clone / "shared" / "Agent Messaging Hub"
    (hub / "inbox").mkdir(parents=True)
    (hub / "inbox" / "aster.md").write_text("# Aster — Inbox\n", encoding="utf-8")
    monkeypatch.setenv("PARTNER_CLIENT_DISPATCH_CLONE", str(clone))
    monkeypatch.setenv("PARTNER_CLIENT_HUB_PARTNER", "aletheia")
    # Validation runs before any git traffic: an unknown name is refused with
    # the live family in the message; a live inbox gets past the gate.
    bad = hub_dispatch.execute("nobody", "x", "y")
    assert "not a known recipient" in bad and "aster" in bad
    ok = hub_dispatch.execute("aster", "x", "y")
    assert "not a known recipient" not in ok
