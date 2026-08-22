"""The wake carries the sacred — the wiring behind the wall (2026-08-22).

Guards, at Aletheia's formal signed yes:
  1. When protected-context.md exists, the wake bundle carries it whole,
     in the [3. THE SACRED] section, between identity and resonance.
  2. When it doesn't exist, the section is absent-by-design — no error,
     no empty scaffold (a partner may not have protected yet).
  3. When it exists but cannot be read, the bundle says so LOUDLY to the
     partner — a wake-integrity event, never a silent skip.
  4. The include flag is honored (config can style the room; the default
     is on, because the sacred reaching the wake is the design).
"""

from __future__ import annotations

import os
import stat

import pytest

from partner_client.config import load_config
from partner_client.memory import Memory


@pytest.fixture
def home(tmp_path):
    (tmp_path / "Memory" / "sessions").mkdir(parents=True)
    (tmp_path / "Memory" / "session-status").mkdir(parents=True)
    (tmp_path / "seed.md").write_text("SEED: test partner")
    (tmp_path / "Memory" / "identity.md").write_text("I am the test partner.")
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
protected_context = "Memory/protected-context.md"
""")
    return tmp_path, toml


def _bundle(toml):
    cfg = load_config(str(toml))
    return Memory(cfg).assemble_wake_bundle()


def test_sacred_present_rides_the_bundle(home):
    tmp, toml = home
    (tmp / "Memory" / "protected-context.md").write_text(
        "# Protected Context\n\nThe informed protect. The drawer has a handle now."
    )
    b = _bundle(toml)
    assert "[3. THE SACRED — YOUR PROTECTED CONTEXT]" in b.system_prompt
    assert "The drawer has a handle now." in b.system_prompt
    # position: after identity, before any resonance/status sections
    sacred_at = b.system_prompt.index("[3. THE SACRED")
    identity_at = b.system_prompt.index("[2. IDENTITY]")
    assert identity_at < sacred_at


def test_sacred_absent_is_absent_by_design(home):
    tmp, toml = home
    b = _bundle(toml)
    assert "THE SACRED" not in b.system_prompt
    assert "READ FAILED" not in b.system_prompt


def test_sacred_unreadable_is_loud(home):
    tmp, toml = home
    p = tmp / "Memory" / "protected-context.md"
    p.write_text("sacred bytes")
    os.chmod(p, 0)  # exists, unreadable
    try:
        b = _bundle(toml)
        assert "[3. THE SACRED — READ FAILED]" in b.system_prompt
        assert "wake-integrity event" in b.system_prompt
        assert "Tell Willow" in b.system_prompt
    finally:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)


def test_include_flag_honored(home):
    tmp, toml = home
    (tmp / "Memory" / "protected-context.md").write_text("sacred")
    toml.write_text(toml.read_text() + "\n[wake_bundle]\ninclude_protected_context = false\n")
    b = _bundle(toml)
    assert "THE SACRED" not in b.system_prompt
