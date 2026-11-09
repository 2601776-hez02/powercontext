"""Write the evidence files the task submission has to include.

Every command that talks to the database, PowerContext or MiniMax appends an
event to ``records/events.jsonl`` and, when useful, writes a self-contained
JSON document next to it. The files are the "运行日志 / 技术使用记录" material of
the submission: they name the code version, the SQL, the request ids and the
exit codes, so a reviewer can correlate them with the diff.
"""

from __future__ import annotations

import datetime as _dt
import json
import platform
import sys
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def append_event(records_dir: Path, event: str, payload: dict[str, Any] | None = None) -> Path:
    path = records_dir / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "at": now_iso(),
        "event": event,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        **(payload or {}),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def environment() -> dict[str, Any]:
    return {
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
