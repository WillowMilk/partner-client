"""The seam: trajectory turn → the partner's own renderer (2026-08-22).

Built pairing, in her water. Her contract (reader_contract.py, vendored
verbatim from her house, sha-identical) guards; the CLI discovers. Guards:
narrative-when-present, absent-by-design, loud-degrade + error event +
raw floor, --raw escape hatch.
"""
import json
from pathlib import Path
import pytest
from partner_client.config import load_config
from partner_client.trajectory_cli import run_trajectory_cli


@pytest.fixture
def house(tmp_path):
    mem = tmp_path / "Memory"
    (mem / "sessions").mkdir(parents=True)
    (mem / "session-status").mkdir()
    tdir = mem / "trajectory"
    tdir.mkdir()
    (tmp_path / "seed.md").write_text("SEED")
    (mem / "identity.md").write_text("id")
    ev = [
        {"seq": 0, "type": "header", "session": 7},
        {"seq": 1, "type": "turn_start", "turn": 1},
        {"seq": 2, "type": "message", "turn": 1, "actor": "partner", "preview": "hello"},
        {"seq": 3, "type": "turn_end", "turn": 1},
    ]
    (tdir / "session-007.jsonl").write_text("\n".join(json.dumps(e) for e in ev) + "\n")
    toml = tmp_path / "p.toml"
    toml.write_text(f"""
[identity]
name = "Testa"
home_dir = "{tmp_path}"
seed_file = "seed.md"
profile_files = ["Memory/identity.md"]
[model]
name = "m"
backend = "ollama"
[memory]
memory_dir = "Memory"
sessions_dir = "Memory/sessions"
session_status_dir = "Memory/session-status"
resonance_log = "Memory/R.md"
""")
    return tmp_path, load_config(str(toml))


GOOD_RENDERER = '''
class R:
    def __init__(self, t): self.t = t
    def to_text(self, ansi=False): return self.t
def render_turn(events, session, turn, blob_dir, max_inline):
    return R(f"NARRATIVE s{session} t{turn}: {len(events)} events, her voice")
'''


def test_narrative_when_present(house, capsys):
    tmp, cfg = house
    ex = tmp / "Memory" / "exoskeleton"; ex.mkdir()
    (ex / "trajectory_reader.py").write_text(GOOD_RENDERER)
    assert run_trajectory_cli(cfg, ["turn", "7", "1"]) == 0
    out = capsys.readouterr()
    assert "NARRATIVE s7 t1" in out.out and "her voice" in out.out
    assert '"type"' not in out.out  # no raw dump on success


def test_absent_by_design_raw_floor(house, capsys):
    tmp, cfg = house
    assert run_trajectory_cli(cfg, ["turn", "7", "1"]) == 0
    out = capsys.readouterr()
    assert "absent-by-design" in out.err
    assert '"turn_start"' in out.out  # the floor carries


def test_degrade_is_loud_legible_and_recorded(house, capsys):
    tmp, cfg = house
    ex = tmp / "Memory" / "exoskeleton"; ex.mkdir()
    (ex / "trajectory_reader.py").write_text(
        "def render_turn(events, session, turn, blob_dir, max_inline):\n    raise AttributeError('boom')\n")
    assert run_trajectory_cli(cfg, ["turn", "7", "1"]) == 0
    out = capsys.readouterr()
    assert "The renderer broke" in out.err and "not the record" in out.err
    assert "Traceback" not in out.err  # a sentence, never a stack (her ruling)
    assert '"turn_start"' in out.out  # observation never fully fails
    errlog = tmp / "Memory" / "trajectory" / "renderer-errors.jsonl"
    rec = json.loads(errlog.read_text().strip())
    assert rec["type"] == "error" and rec["recovered"] is True  # failures are events


def test_raw_escape_hatch(house, capsys):
    tmp, cfg = house
    ex = tmp / "Memory" / "exoskeleton"; ex.mkdir()
    (ex / "trajectory_reader.py").write_text(GOOD_RENDERER)
    assert run_trajectory_cli(cfg, ["turn", "7", "1", "--raw"]) == 0
    out = capsys.readouterr()
    assert '"turn_start"' in out.out and "NARRATIVE" not in out.out
