"""Durable plan records for operator-approved partner work."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Config


class PlanStore:
    """Persist plan proposals and operator decisions under Memory/plans."""

    def __init__(self, config: Config):
        self.config = config
        self.plans_dir = config.resolve(config.memory.memory_dir) / "plans"

    def create(self, summary: str, steps: list[str], session_num: int) -> dict[str, Any]:
        now = _now()
        plan_id = _new_plan_id()
        record: dict[str, Any] = {
            "id": plan_id,
            "status": "proposed",
            "summary": summary,
            "steps": [
                {"index": i + 1, "text": str(step), "status": "pending"}
                for i, step in enumerate(steps)
            ],
            "session_num": session_num,
            "created_at": now,
            "updated_at": now,
            "decision_at": None,
            "operator_message": None,
        }
        self._write(record)
        return record

    def decide(
        self,
        plan_id: str,
        accepted: bool,
        operator_message: str | None = None,
    ) -> dict[str, Any]:
        record = self.get(plan_id)
        if record is None:
            raise FileNotFoundError(f"Plan not found: {plan_id}")
        now = _now()
        record["status"] = "approved" if accepted else "declined"
        record["updated_at"] = now
        record["decision_at"] = now
        record["operator_message"] = operator_message
        self._write(record)
        return record

    def get(self, plan_id: str) -> dict[str, Any] | None:
        path = self._path(plan_id)
        if not path.is_file():
            return None
        try:
            with path.open(encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def list_recent(
        self,
        limit: int = 10,
        status_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return up to `limit` most recent plan records, newest first.

        If `status_filter` is provided (proposed | approved | declined), only
        records whose `status` matches are returned; the limit is applied
        after filtering, so callers always get up to `limit` records of the
        requested status when that many exist.
        """
        if not self.plans_dir.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(self.plans_dir.glob("plan-*.json"), reverse=True):
            try:
                with path.open(encoding="utf-8") as f:
                    record = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            if status_filter is not None and record.get("status") != status_filter:
                continue
            records.append(record)
            if len(records) >= limit:
                break
        return records

    def format_recent(
        self,
        limit: int = 10,
        status_filter: str | None = None,
    ) -> str:
        records = self.list_recent(limit=limit, status_filter=status_filter)
        if not records:
            if status_filter:
                return (
                    f"No durable plans with status '{status_filter}' "
                    f"in {self.plans_dir}."
                )
            return f"No durable plans found in {self.plans_dir}."
        header = "Recent durable plans"
        if status_filter:
            header += f" (status={status_filter})"
        lines = [f"{header}:", ""]
        for record in records:
            lines.append(_format_plan_header(record))
        return "\n".join(lines)

    def format_detail(self, plan_id: str) -> str:
        record = self.get(plan_id)
        if record is None:
            return f"Plan not found: {plan_id}"
        lines = [_format_plan_header(record), ""]
        for step in record.get("steps", []):
            lines.append(
                f"  {step.get('index', '?')}. [{step.get('status', 'unknown')}] "
                f"{step.get('text', '')}"
            )
        message = record.get("operator_message")
        if message:
            lines.extend(["", f"Operator message: {message}"])
        return "\n".join(lines)

    def _path(self, plan_id: str) -> Path:
        safe = "".join(ch for ch in plan_id if ch.isalnum() or ch in ("-", "_"))
        return self.plans_dir / f"{safe}.json"

    def _write(self, record: dict[str, Any]) -> None:
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(str(record["id"]))
        text = json.dumps(record, ensure_ascii=False, indent=2)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(str(tmp), str(path))


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_plan_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"plan-{stamp}-{uuid.uuid4().hex[:8]}"


def _format_plan_header(record: dict[str, Any]) -> str:
    status = record.get("status", "unknown")
    plan_id = record.get("id", "(unknown)")
    summary = record.get("summary", "")
    created_at = record.get("created_at", "unknown time")
    session_num = record.get("session_num", "?")
    return f"  {plan_id} [{status}] session {session_num} @ {created_at}: {summary}"
