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

"""Source dependency lifecycle: withdraw and restore derived Memory and Topics."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory.models import MemoryCitation
from powercontext.builtin.artifacts.topic_memory import TopicMemory
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_TABLE
from powercontext.sources import SourceRef

Policy = Literal["all", "any"]
Purpose = Literal["generation", "audit"]


class SourceLifecycleError(ValueError):
    """A dependency graph, event, or reference validation failure."""


class SourceLifecycleEvent(BaseModel):
    """One validated Source availability transition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    reason: str
    revision: int
    sequence: int
    available: bool

    @field_validator("node_id", "reason")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be a non-blank string")
        return value

    @field_validator("revision", "sequence")
    @classmethod
    def validate_positive_integer(cls, value: int) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("must be a positive integer")
        return value


_NodeType = Literal["source", "memory", "topic"]


class _Node(BaseModel):
    node_type: _NodeType
    parents: tuple[str, ...]
    policy: Policy | None
    reference: dict[str, Any]
    revision: int
    sequence: int
    ordinal: int
    available: bool


class SourceLifecycleService:
    """Scope-bound dependency DAG with native Memory and Topic propagation."""

    def __init__(self, runtime, scope_id: str) -> None:
        self._runtime = runtime
        self._scope_id = scope_id

    async def register_source(self, node_id: str, source_ref: SourceRef, *, revision: int) -> None:
        await self._register(node_id, "source", (), None, source_ref.model_dump(mode="json"), revision, 0, True)

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        *,
        parents: tuple[str, ...],
        policy: Policy,
    ) -> None:
        await self._register(node_id, "memory", parents, policy, citation.model_dump(mode="json"), 0, 0, False)

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        *,
        parents: tuple[str, ...],
        policy: Policy,
    ) -> None:
        record = await self._runtime.records.for_scope(self._scope_id).get_artifact_revision(
            artifact_ref.family,
            artifact_ref.artifact_id,
            artifact_ref.revision,
        )
        reference = {**artifact_ref.model_dump(mode="json"), "content": record.content}
        await self._register(node_id, "topic", parents, policy, reference, 0, 0, False)

    async def _register(
        self,
        node_id: str,
        node_type: _NodeType,
        parents: tuple[str, ...],
        policy: Policy | None,
        reference: dict[str, Any],
        revision: int,
        sequence: int,
        available: bool,
    ) -> None:
        if not isinstance(node_id, str) or not node_id.strip():
            raise SourceLifecycleError("node_id must be a non-blank string")
        nodes = await self._load()
        if node_id in nodes:
            raise SourceLifecycleError("node_id is already registered in this scope")
        for parent in parents:
            if parent not in nodes:
                raise SourceLifecycleError("parent must be registered first")
        if node_type != "source":
            if not parents:
                raise SourceLifecycleError("derived nodes require at least one parent")
            if policy not in ("all", "any"):
                raise SourceLifecycleError("policy must be 'all' or 'any'")
            if len(set(parents)) != len(parents):
                raise SourceLifecycleError("parents must be unique")
            if node_id in parents:
                raise SourceLifecycleError("a node cannot depend on itself")
            if node_type == "memory" and node_id in _ancestors(nodes, parents):
                raise SourceLifecycleError("dependency cycle detected")
            if node_type == "topic" and node_id in _ancestors(nodes, parents):
                raise SourceLifecycleError("dependency cycle detected")
        if node_type == "source":
            available = True
        else:
            available = _derived_available(nodes, parents, policy)
        node = _Node(
            node_type=node_type,
            parents=parents,
            policy=policy,
            reference=reference,
            revision=revision,
            sequence=sequence,
            ordinal=len(nodes),
            available=available,
        )
        await self._store(node, node_id)
        nodes[node_id] = node
        if node_type == "topic" or (node_type == "memory" and not available):
            await self._propagate_native(node_id, nodes, available=available)

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        nodes = await self._load()
        if event.node_id not in nodes:
            raise SourceLifecycleError("event targets an unknown Source node")
        node = nodes[event.node_id]
        if node.node_type != "source":
            raise SourceLifecycleError("events may only target Source nodes")
        if event.revision != node.revision:
            raise SourceLifecycleError("event revision does not match the Source lifecycle version")
        if event.sequence < node.sequence:
            raise SourceLifecycleError("event sequence is not monotonically increasing")
        if event.sequence == node.sequence and event.available != node.available:
            raise SourceLifecycleError("same-sequence event with different content")
        if event.sequence == node.sequence and event.available == node.available:
            return sorted(self._available_ids(nodes))
        previous_available = self._available_ids(nodes)
        updated = node.model_copy(update={"sequence": event.sequence, "available": event.available})
        nodes[event.node_id] = updated
        await self._store(updated, event.node_id)
        active = self._recompute(nodes)
        for node_id in _registration_order(nodes):
            was = node_id in previous_available
            now = node_id in active
            if was != now:
                await self._propagate_native(node_id, nodes, available=now)
        await self._store_all(nodes)
        return sorted(active)

    async def resolve(self, node_id: str, *, purpose: Purpose) -> Any:
        if purpose not in ("generation", "audit"):
            raise SourceLifecycleError("unknown resolve purpose")
        nodes = await self._load()
        if node_id not in nodes:
            raise SourceLifecycleError("unknown node")
        node = nodes[node_id]
        if purpose == "generation" and not node.available:
            raise SourceLifecycleError("node is not available for generation")
        reference = node.reference
        if node.node_type == "source":
            return SourceRef.model_validate(reference)
        if node.node_type == "memory":
            return MemoryCitation.model_validate(reference)
        return ArtifactRef.model_validate(reference)

    def _recompute(self, nodes: dict[str, _Node]) -> set[str]:
        active: set[str] = set()
        for node_id in _registration_order(nodes):
            node = nodes[node_id]
            if node.node_type == "source":
                available = node.available
            else:
                available = _derived_available(nodes, node.parents, node.policy)
            nodes[node_id] = node.model_copy(update={"available": available})
            if available:
                active.add(node_id)
        return active

    def _available_ids(self, nodes: dict[str, _Node]) -> set[str]:
        return {node_id for node_id, node in nodes.items() if node.available}

    async def _propagate_native(self, node_id: str, nodes: dict[str, _Node], *, available: bool) -> None:
        node = nodes[node_id]
        if node.node_type == "memory":
            await self._set_memory_state(node_id, node.reference, available)
        elif node.node_type == "topic":
            await self._set_topic_state(node_id, node.reference, available)

    async def _set_memory_state(self, node_id: str, reference: dict[str, Any], available: bool) -> None:
        citation = MemoryCitation.model_validate(reference)
        from powercontext.builtin.statistics import ModelUsagePurpose

        async with self._runtime._context(
            self._scope_id,
            embedding_purpose=ModelUsagePurpose.MEMORY_INDEXING,
        ) as context:
            async with self._runtime._locked(self._scope_id):
                service = context.artifacts.memory
                current = await service.head(context.artifacts.memory_artifact_id)
                if current is None:
                    return
                entries = await service.entries(current)
                matching = [entry for entry in entries if entry.entry_id == citation.entry_id]
                if not matching:
                    return
                if available:
                    await service.reactivate(current, entries=matching, reason="source-lifecycle")
                else:
                    await service.forget(current, entries=matching, reason="source-lifecycle")

    async def _set_topic_state(self, node_id: str, reference: dict[str, Any], available: bool) -> None:
        ref = ArtifactRef.model_validate({k: v for k, v in reference.items() if k != "content"})
        content = reference.get("content")
        async with self._runtime.database.transaction() as connection:
            if available:
                await _reactivate_topic(connection, self._scope_id, ref, content)
            else:
                await _deactivate_topic(connection, self._scope_id, ref)

    async def _load(self) -> dict[str, _Node]:
        async with self._runtime.database.transaction() as connection:
            rows = (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_TABLE).where(SOURCE_LIFECYCLE_TABLE.c.scope_id == self._scope_id)
                )
            ).mappings()
            return {
                row["node_id"]: _Node(
                    node_type=row["node_type"],
                    parents=tuple(json.loads(row["parents"])),
                    policy=row["policy"],
                    reference=json.loads(row["reference"]),
                    revision=row["revision"] or 0,
                    sequence=row["sequence"] or 0,
                    ordinal=int(row["ordinal"]),
                    available=bool(row["available"]),
                )
                for row in rows
            }

    async def _store(self, node: _Node, node_id: str) -> None:
        async with self._runtime.database.transaction() as connection:
            await self._upsert(connection, node_id, node)

    async def _store_all(self, nodes: dict[str, _Node]) -> None:
        async with self._runtime.database.transaction() as connection:
            for node_id, node in nodes.items():
                await self._upsert(connection, node_id, node)

    async def _upsert(self, connection: AsyncConnection, node_id: str, node: _Node) -> None:
        await connection.execute(
            delete(SOURCE_LIFECYCLE_TABLE).where(
                SOURCE_LIFECYCLE_TABLE.c.scope_id == self._scope_id,
                SOURCE_LIFECYCLE_TABLE.c.node_id == node_id,
            )
        )
        from sqlalchemy import insert

        await connection.execute(
            insert(SOURCE_LIFECYCLE_TABLE).values(
                scope_id=self._scope_id,
                node_id=node_id,
                node_type=node.node_type,
                parents=json.dumps(node.parents),
                policy=node.policy,
                reference=json.dumps(node.reference),
                revision=node.revision if node.node_type == "source" else None,
                sequence=node.sequence if node.node_type == "source" else None,
                ordinal=node.ordinal,
                available=node.available,
            )
        )


def _registration_order(nodes: dict[str, _Node]) -> list[str]:
    return [node_id for node_id, _ in sorted(nodes.items(), key=lambda item: item[1].ordinal)]


def _ancestors(nodes: dict[str, _Node], parents: tuple[str, ...]) -> set[str]:
    seen: set[str] = set()
    stack = list(parents)
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        node = nodes.get(current)
        if node is not None:
            stack.extend(node.parents)
    return seen


def _derived_available(nodes: dict[str, _Node], parents: tuple[str, ...], policy: Policy | None) -> bool:
    support = [nodes[parent].available for parent in parents]
    if policy == "all":
        return all(support)
    return any(support)


async def _deactivate_topic(connection: AsyncConnection, scope_id: str, ref: ArtifactRef) -> None:
    from sqlalchemy import text as sql_text

    if connection.dialect.name == "sqlite":
        await connection.execute(
            sql_text(
                "DELETE FROM pc_topic_memory_topic_fts WHERE scope_id = :scope_id AND artifact_id = :artifact_id"
            ),
            {"scope_id": scope_id, "artifact_id": ref.artifact_id},
        )
        await connection.execute(
            sql_text(
                "DELETE FROM pc_topic_memory_chunk_fts WHERE scope_id = :scope_id AND artifact_id = :artifact_id"
            ),
            {"scope_id": scope_id, "artifact_id": ref.artifact_id},
        )


async def _reactivate_topic(
    connection: AsyncConnection,
    scope_id: str,
    ref: ArtifactRef,
    content: dict[str, Any] | None,
) -> None:
    from sqlalchemy import text as sql_text

    from powercontext.builtin.artifacts.search import analyze_text
    from powercontext.builtin.artifacts.topic_memory.chunking import chunk_topic_memory_detail

    if not content:
        return
    title = content.get("title", "")
    summary = content.get("summary", "")
    detail = content.get("detail", "")
    searchable = analyze_text(f"{title}\n{summary}")
    chunks = chunk_topic_memory_detail(detail)

    if connection.dialect.name == "sqlite":
        await connection.execute(
            sql_text(
                "DELETE FROM pc_topic_memory_topic_fts WHERE scope_id = :scope_id AND artifact_id = :artifact_id"
            ),
            {"scope_id": scope_id, "artifact_id": ref.artifact_id},
        )
        await connection.execute(
            sql_text(
                "DELETE FROM pc_topic_memory_chunk_fts WHERE scope_id = :scope_id AND artifact_id = :artifact_id"
            ),
            {"scope_id": scope_id, "artifact_id": ref.artifact_id},
        )
        await connection.execute(
            sql_text(
                "INSERT INTO pc_topic_memory_topic_fts "
                "(scope_id, artifact_id, revision, title, summary, searchable_text) "
                "VALUES (:scope_id, :artifact_id, :revision, :title, :summary, :searchable_text)"
            ),
            {
                "scope_id": scope_id,
                "artifact_id": ref.artifact_id,
                "revision": ref.revision,
                "title": title,
                "summary": summary,
                "searchable_text": searchable,
            },
        )
        for chunk in chunks:
            await connection.execute(
                sql_text(
                    "INSERT INTO pc_topic_memory_chunk_fts "
                    "(scope_id, artifact_id, revision, chunk_ordinal, start_offset, title, summary, "
                    "chunk_text, searchable_text) "
                    "VALUES (:scope_id, :artifact_id, :revision, :chunk_ordinal, :start_offset, :title, :summary, "
                    ":chunk_text, :searchable_text)"
                ),
                {
                    "scope_id": scope_id,
                    "artifact_id": ref.artifact_id,
                    "revision": ref.revision,
                    "chunk_ordinal": chunk.ordinal,
                    "start_offset": chunk.start_offset,
                    "title": title,
                    "summary": summary,
                    "chunk_text": chunk.text,
                    "searchable_text": analyze_text(chunk.text),
                },
            )


__all__ = ["SourceLifecycleError", "SourceLifecycleEvent", "SourceLifecycleService"]
