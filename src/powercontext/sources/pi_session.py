# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Pi session branch parsing into event-level captured Sources."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel

from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.connectors import ConnectorRunCompletion, ConnectorRunStatus
from powercontext.sources.models import Source, SourceMaterialization

PI_EVENT_SOURCE_NAME = "pi-event"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"


class PiSessionInput(BaseModel):
    """One offline Pi session archive and an optional selected leaf."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """Definition-native value for exactly one visible session event."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, Any]


class PiEventSource(Source):
    """Captured Pi event keeping its full original record."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, Any]


def _pi_event_source_id(session_id: str, event_id: str) -> str:
    return f"pi:{quote(session_id, safe='')}:{quote(event_id, safe='')}"


def _json_lines(content: bytes) -> list[dict[str, Any]]:
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("session content must be valid UTF-8") from error
    records: list[dict[str, Any]] = []
    for line in decoded.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("session content must be valid JSONL") from error
        if not isinstance(record, dict):
            raise ValueError("session records must be JSON objects")
        records.append(record)
    return records


def _visible_body(record: dict[str, Any]) -> tuple[str, str] | None:
    """Return ``(role, body)`` for a visible event, or ``None`` for control nodes."""
    event_type = record.get("type")
    if event_type == "message":
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        role = message.get("role")
        if not isinstance(role, str):
            return None
        body = _message_body(message)
        if body is None or not body.strip():
            return None
        return role, body
    if event_type == "compaction":
        summary = record.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return None
        return "compaction", summary
    if event_type == "branch_summary":
        summary = record.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return None
        return "branch_summary", summary
    if event_type == "custom_message":
        body = _text_blocks(record.get("content"))
        if body is None or not body.strip():
            return None
        return "custom", body
    return None


def _message_body(message: dict[str, Any]) -> str | None:
    role = message.get("role")
    content = message.get("content")
    if role == "toolResult":
        tool_name = message.get("toolName")
        tool_call_id = message.get("toolCallId")
        text = _text_blocks(content)
        if text is None or not text.strip():
            return None
        return f"[tool_result {tool_name} {tool_call_id}] {text}"
    if role == "bashExecution":
        command = message.get("command") or ""
        output = message.get("output") or ""
        body = f"$ {command}\n{output}"
        return body if body.strip() else None
    if isinstance(content, str):
        return content if content.strip() else None
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                parts.append(block.get("text", ""))
            elif block_type == "toolCall":
                name = block.get("name", "")
                call_id = block.get("id", "")
                arguments = block.get("arguments", {})
                compact = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                parts.append(f"[tool_call {name} {call_id}] {compact}")
        body = "\n".join(parts)
        return body if body.strip() else None
    return None


def _text_blocks(content: Any) -> str | None:
    if isinstance(content, str):
        return content if content.strip() else None
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
        ]
        body = "\n".join(parts)
        return body if body.strip() else None
    return None


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Parse a Pi v3 session and return the selected branch's visible events."""
    records = _json_lines(value.content)
    if not records:
        raise ValueError("session must not be empty")

    header = records[0]
    if header.get("type") != "session" or header.get("version") != 3:
        raise ValueError("first record must be a version-3 session header")
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session header id must be a non-empty string")

    nodes = records[1:]
    by_id: dict[str, dict[str, Any]] = {}
    for node in nodes:
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("every record must have a non-empty string id")
        if node_id in by_id:
            raise ValueError("record ids must be unique")
        by_id[node_id] = node

    seen: set[str] = set()
    for node in nodes:
        node_id = node["id"]
        parent = node.get("parentId")
        if parent is not None and parent != "":
            if not isinstance(parent, str) or parent not in seen:
                raise ValueError("parent must be empty or point to an earlier record")
        seen.add(node_id)

    if not nodes:
        raise ValueError("session must contain at least one node")

    leaf_id = value.leaf_id if value.leaf_id is not None else nodes[-1]["id"]
    if leaf_id not in by_id:
        raise ValueError("selected leaf id does not exist")

    chain: list[str] = []
    current: str | None = leaf_id
    while current is not None:
        chain.append(current)
        parent = by_id[current].get("parentId")
        current = parent if isinstance(parent, str) and parent else None
    chain.reverse()

    events: list[PiEventInput] = []
    for node_id in chain:
        record = by_id[node_id]
        visible = _visible_body(record)
        if visible is None:
            continue
        role, body = visible
        parent_id = record.get("parentId")
        parent_id = parent_id if isinstance(parent_id, str) and parent_id else None
        events.append(
            PiEventInput(
                session_id=session_id,
                event_id=node_id,
                parent_id=parent_id,
                event_type=record.get("type", ""),
                role=role,
                content=f"{role}: {body}",
                record=record,
            )
        )
    return tuple(events)


class PiEventAdapter:
    """Resolve and read captured Pi event Sources."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = _pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            source_id=source_id,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
            materialization=SourceMaterialization.CAPTURED,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Expose one Pi event as standard text evidence with identity metadata."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PiEventAdapter(),
    projections=(PiEventTextEvidenceProjection(),),
)


def _record_hash(record: dict[str, Any]) -> str:
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PiSessionConnector:
    """Incrementally import the selected branch of one Pi session archive."""

    name = PI_SESSION_CONNECTOR_NAME
    version = "1"
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: Path, leaf_id: str | None = None) -> None:
        self._path = path
        self._leaf_id = leaf_id

    async def run(self, session) -> ConnectorRunCompletion:
        raw = self._path.read_bytes()
        events = parse_pi_session(PiSessionInput(content=raw, leaf_id=self._leaf_id))

        previous = session.checkpoint or {}
        if not isinstance(previous, dict):
            raise ValueError("connector checkpoint must be a JSON object")
        imported = previous.get("events")
        imported = imported if isinstance(imported, dict) else {}

        new_state = dict(imported)
        new_events: list[PiEventInput] = []
        for event in events:
            digest = _record_hash(event.record)
            if event.event_id in new_state:
                if new_state[event.event_id] != digest:
                    raise ValueError("an already-imported event was rewritten")
            else:
                new_state[event.event_id] = digest
                new_events.append(event)

        for event in new_events:
            await session.submit(event.event_id, PI_EVENT_SOURCE_NAME, event)

        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={"events": new_state},
        )


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PiEventInput",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
]
