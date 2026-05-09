from __future__ import annotations

import json
from pathlib import Path

from partner_client.config import load_config
from partner_client.memory import Memory
from partner_client.plans import PlanStore
from partner_client.session import Session
from partner_client.timeline import RunTimeline, TimelineReader, TIMELINE_CATEGORIES


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


def test_plan_store_status_filter_returns_matching_records(tmp_path: Path) -> None:
    config = load_config(write_minimal_home(tmp_path))
    store = PlanStore(config)

    proposed_record = store.create(
        summary="Pending plan",
        steps=["Step one"],
        session_num=1,
    )
    approved_record = store.create(
        summary="Approved plan",
        steps=["Step one"],
        session_num=2,
    )
    store.decide(approved_record["id"], accepted=True)

    proposed_only = store.list_recent(status_filter="proposed")
    assert len(proposed_only) == 1
    assert proposed_only[0]["id"] == proposed_record["id"]

    approved_only = store.list_recent(status_filter="approved")
    assert len(approved_only) == 1
    assert approved_only[0]["id"] == approved_record["id"]

    formatted = store.format_recent(status_filter="approved")
    assert "status=approved" in formatted
    assert approved_record["id"] in formatted
    assert proposed_record["id"] not in formatted

    declined_listing = store.format_recent(status_filter="declined")
    assert "No durable plans with status 'declined'" in declined_listing


def test_timeline_reader_formats_recent_events_chronologically(tmp_path: Path) -> None:
    config = load_config(write_minimal_home(tmp_path))
    session = Session(config=config, memory=Memory(config), session_num=4)
    timeline = RunTimeline(config, session)
    timeline.record("session_wake", status="fresh", wake_bundle_chars=2048)
    timeline.record("user_message", chars=42, images=0, context_tokens=128)
    timeline.record(
        "tool_call",
        iteration=1,
        name="read_file",
        args={"path": "Journal.md"},
        result_preview="hello",
        result_chars=5,
        duration_ms=12,
    )

    reader = TimelineReader(config)
    formatted = reader.format_recent(limit=10)

    lines = formatted.splitlines()
    assert lines[0].startswith("Recent timeline events")
    # Events should appear in chronological order: oldest visible first.
    session_idx = next(i for i, line in enumerate(lines) if "session_wake" in line)
    user_idx = next(i for i, line in enumerate(lines) if "user_message" in line)
    tool_idx = next(i for i, line in enumerate(lines) if "tool_call" in line)
    assert session_idx < user_idx < tool_idx
    # Compact summary should expose the tool name and duration.
    tool_line = lines[tool_idx]
    assert "read_file" in tool_line
    assert "12ms" in tool_line


def test_timeline_reader_category_filter_limits_event_types(tmp_path: Path) -> None:
    config = load_config(write_minimal_home(tmp_path))
    session = Session(config=config, memory=Memory(config), session_num=5)
    timeline = RunTimeline(config, session)
    timeline.record("user_message", chars=5)
    timeline.record("tool_call", name="weather", duration_ms=140)
    timeline.record("model_call_end", iteration=1, duration_ms=910, content_chars=200, tool_call_count=1)

    reader = TimelineReader(config)
    tools_view = reader.format_recent(
        limit=20,
        event_types=TIMELINE_CATEGORIES["tools"],
        category_label="tools",
    )
    assert "tool_call" in tools_view
    assert "user_message" not in tools_view
    assert "model_call_end" not in tools_view
    assert "(tools)" in tools_view


def test_timeline_reader_detail_resolves_by_index(tmp_path: Path) -> None:
    config = load_config(write_minimal_home(tmp_path))
    session = Session(config=config, memory=Memory(config), session_num=6)
    timeline = RunTimeline(config, session)
    timeline.record("user_message", chars=10)
    timeline.record(
        "tool_call",
        iteration=1,
        name="git_status",
        args={"repo": "aletheia-sandbox"},
        result_preview="clean",
        result_chars=5,
        duration_ms=88,
    )

    reader = TimelineReader(config)
    # Two events in the listing; index 2 = the most recent (tool_call).
    detail = reader.format_detail(2)
    assert detail.startswith("Event #2 — tool_call")
    assert "name: git_status" in detail
    assert "duration_ms: 88" in detail


def test_timeline_reader_detail_rejects_out_of_range(tmp_path: Path) -> None:
    config = load_config(write_minimal_home(tmp_path))
    session = Session(config=config, memory=Memory(config), session_num=7)
    timeline = RunTimeline(config, session)
    timeline.record("user_message", chars=5)

    reader = TimelineReader(config)
    out_of_range = reader.format_detail(99)
    assert "out of range" in out_of_range


def test_timeline_reader_handles_missing_log_gracefully(tmp_path: Path) -> None:
    config = load_config(write_minimal_home(tmp_path))
    reader = TimelineReader(config)
    # No events recorded yet -> the log file should not exist.
    assert reader.list_recent() == []
    formatted = reader.format_recent()
    assert "No timeline events recorded yet" in formatted
