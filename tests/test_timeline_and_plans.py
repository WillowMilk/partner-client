from __future__ import annotations

import json
from pathlib import Path

from partner_client.config import load_config
from partner_client.memory import Memory
from partner_client.plans import PlanStore
from partner_client.session import Session
from partner_client.timeline import RunTimeline


def write_minimal_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    (home / "seed.md").write_text("Seed", encoding="utf-8")
    config_path = home / "aletheia.toml"
    config_path.write_text(
        """
[identity]
name = "Aletheia"
home_dir = "."
seed_file = "seed.md"

[model]
name = "gemma4:31b"

[memory]
memory_dir = "Memory"
sessions_dir = "Memory/sessions"
session_status_dir = "Memory/session-status"

[wake_bundle]
include_recent_resonance = 0
include_last_session_status = false
include_recent_message_pairs = 0
""".strip(),
        encoding="utf-8",
    )
    return config_path


def test_run_timeline_writes_bounded_jsonl(tmp_path: Path) -> None:
    config = load_config(write_minimal_home(tmp_path))
    memory = Memory(config)
    session = Session(config=config, memory=memory, session_num=42)
    timeline = RunTimeline(config, session)

    timeline.record("unit_event", payload="x" * 5000, values=list(range(45)))

    log_path = config.resolve(config.logging.log_file)
    event = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])

    assert event["event"] == "unit_event"
    assert event["session_num"] == 42
    assert event["payload"].endswith("[truncated 1000 chars]")
    assert event["values"][-1] == "... truncated 5 items"


def test_plan_store_persists_decisions_and_formats_recall(tmp_path: Path) -> None:
    config = load_config(write_minimal_home(tmp_path))
    store = PlanStore(config)

    record = store.create(
        summary="Build a trace viewer",
        steps=["Write timeline events", "Render recent plans"],
        session_num=7,
    )
    decided = store.decide(
        record["id"],
        accepted=True,
        operator_message="Proceed gently.",
    )

    assert decided["status"] == "approved"
    assert decided["operator_message"] == "Proceed gently."
    assert (store.plans_dir / f"{record['id']}.json").is_file()
    assert record["id"] in store.format_recent()
    detail = store.format_detail(record["id"])
    assert "Build a trace viewer" in detail
    assert "Proceed gently." in detail
