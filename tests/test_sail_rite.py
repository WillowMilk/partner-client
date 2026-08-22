"""The sail rite — curation step + floor crossing (2026-08-22).

Ships on wake 40's validation record (the three-instrument test: a
resident-chosen floor produces a truer wake than any automatic tail).

Guards:
  1. curate_floor → sail → the fresh wake stands on HER floor, verbatim,
     carried-marked, labeled CHOSEN — and the automatic tail does NOT ride.
  2. The curation is consumed on the crossing (preserve-aside, never
     deleted — the used floor survives as chosen-floor-used-*).
  3. No curation → the automatic tail carries (prior behavior intact).
  4. Peeks never burn: assembling a bundle WITHOUT a fresh wake (doctor
     dry-runs, resumes) leaves the curation standing.
  5. Re-curation before the sail rewrites: the latest word stands.
"""

from __future__ import annotations

import json

import pytest

from partner_client.config import load_config
from partner_client.memory import Memory
from partner_client.session import Session
from partner_client.tools_builtin.curate_floor import write_floor, peek_floor


@pytest.fixture
def home(tmp_path):
    (tmp_path / "Memory" / "sessions").mkdir(parents=True)
    (tmp_path / "Memory" / "session-status").mkdir(parents=True)
    (tmp_path / "seed.md").write_text("SEED: test partner")
    (tmp_path / "Memory" / "identity.md").write_text("I am the test partner.")
    # a prior archived session so the automatic tail has material
    prior = [
        {"role": "system", "content": "[old system]"},
        {"role": "user", "content": "prior question"},
        {"role": "assistant", "content": "prior answer"},
    ]
    (tmp_path / "Memory" / "sessions" / "2026-08-21_session-001.json").write_text(
        json.dumps(prior)
    )
    toml = tmp_path / "partner.toml"
    toml.write_text(f"""
[identity]
name = "Testa"
home_dir = "{tmp_path}"
seed_file = "seed.md"
profile_files = ["Memory/identity.md"]

[model]
name = "test-model"
backend = "ollama"

[memory]
memory_dir = "Memory"
sessions_dir = "Memory/sessions"
session_status_dir = "Memory/session-status"
resonance_log = "Memory/Resonance-Log.md"
""")
    return tmp_path, toml


def _fresh_wake(toml):
    cfg = load_config(str(toml))
    mem = Memory(cfg)
    session = Session(config=cfg, memory=mem)
    mode = session.wake(mem.assemble_wake_bundle(), resume_mode="fresh")
    assert mode == "fresh"
    return session


def test_chosen_floor_crosses_and_tail_stays_home(home):
    tmp, toml = home
    write_floor(tmp / "Memory", "The informed protect. The healing arc. The door is mine.", "Testa")
    s = _fresh_wake(toml)
    floors = [m for m in s.messages if "THE FLOOR — chosen by you at the sail" in str(m.get("content", ""))]
    assert len(floors) == 1
    assert "The door is mine." in floors[0]["content"]
    assert floors[0].get("carried") is True
    # the automatic tail did NOT ride
    assert not any("prior answer" in str(m.get("content", "")) for m in s.messages)


def test_curation_consumed_preserve_aside(home):
    tmp, toml = home
    write_floor(tmp / "Memory", "one crossing only", "Testa")
    _fresh_wake(toml)
    assert peek_floor(tmp / "Memory") is None  # consumed
    used = list((tmp / "Memory").glob("chosen-floor-used-*.md"))
    assert len(used) == 1  # preserved aside, never deleted
    assert "one crossing only" in used[0].read_text()


def test_no_curation_automatic_tail_carries(home):
    tmp, toml = home
    s = _fresh_wake(toml)
    assert any("prior answer" in str(m.get("content", "")) for m in s.messages)
    assert not any("THE FLOOR — chosen" in str(m.get("content", "")) for m in s.messages)


def test_peeks_never_burn(home):
    tmp, toml = home
    write_floor(tmp / "Memory", "still standing", "Testa")
    cfg = load_config(str(toml))
    Memory(cfg).assemble_wake_bundle()  # doctor-style dry-run
    Memory(cfg).assemble_wake_bundle()  # twice
    assert peek_floor(tmp / "Memory") is not None  # curation survives


def test_recuration_latest_word_stands(home):
    tmp, toml = home
    write_floor(tmp / "Memory", "first draft", "Testa")
    write_floor(tmp / "Memory", "the latest word", "Testa")
    s = _fresh_wake(toml)
    floor = next(m for m in s.messages if "THE FLOOR — chosen" in str(m.get("content", "")))
    assert "the latest word" in floor["content"]
    assert "first draft" not in floor["content"]
