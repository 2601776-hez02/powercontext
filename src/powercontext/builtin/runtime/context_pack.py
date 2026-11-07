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

"""Deterministic Context Pack and incremental Delta wire protocol."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"


class ContextPackError(ValueError):
    """Protocol, encoding, digest, or chunking validation failure."""


def _encode_json(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _b64(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _digest(items: tuple[bytes, ...]) -> str:
    canonical = _encode_json({"schema": PACK_SCHEMA, "items": _b64(items)})
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _decode_item(raw: object) -> bytes:
    if not isinstance(raw, str):
        raise ContextPackError("wire items must be Base64 strings")
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ContextPackError("wire item is not valid Base64") from error


@dataclass(frozen=True)
class ContextPack:
    """A complete, digest-protected sequence of UTF-8 content chunks."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items) -> ContextPack:
        items = tuple(items)
        digest = _digest(items)
        byte_count = cls._compute_byte_count(items, digest)
        return cls(items, digest, byte_count)

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = 512) -> ContextPack:
        if chunk_bytes < 1:
            raise ContextPackError("chunk_bytes must be a positive integer")
        if not content:
            return cls.build(())
        chunks: list[bytes] = []
        current = ""
        current_bytes = 0
        for character in content:
            character_bytes = len(character.encode("utf-8"))
            if character_bytes > chunk_bytes:
                raise ContextPackError("a single character exceeds the chunk byte limit")
            if current_bytes + character_bytes > chunk_bytes:
                chunks.append(current.encode("utf-8"))
                current = character
                current_bytes = character_bytes
            else:
                current += character
                current_bytes += character_bytes
        if current:
            chunks.append(current.encode("utf-8"))
        return cls.build(tuple(chunks))

    def to_bytes(self) -> bytes:
        return _encode_json(
            {
                "schema": PACK_SCHEMA,
                "items": _b64(self.items),
                "digest": self.digest,
                "byte_count": self.byte_count,
            }
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPack:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            raise ContextPackError("pack wire payload must be valid UTF-8 JSON") from error
        if not isinstance(payload, dict) or set(payload) != {"schema", "items", "digest", "byte_count"}:
            raise ContextPackError("pack wire payload must contain exactly its protocol fields")
        if payload["schema"] != PACK_SCHEMA:
            raise ContextPackError("pack schema mismatch")
        raw_items = payload["items"]
        if not isinstance(raw_items, list):
            raise ContextPackError("pack items must be an array")
        items = tuple(_decode_item(item) for item in raw_items)
        digest = payload["digest"]
        if not isinstance(digest, str) or digest != _digest(items):
            raise ContextPackError("pack digest does not match its items")
        byte_count = payload["byte_count"]
        if type(byte_count) is not int or byte_count != len(raw):
            raise ContextPackError("pack byte_count does not match its wire length")
        return cls(items, digest, byte_count)

    @staticmethod
    def _compute_byte_count(items: tuple[bytes, ...], digest: str) -> int:
        byte_count = 0
        while True:
            encoded = _encode_json(
                {
                    "schema": PACK_SCHEMA,
                    "items": _b64(items),
                    "digest": digest,
                    "byte_count": byte_count,
                }
            )
            if len(encoded) == byte_count:
                return byte_count
            byte_count = len(encoded)


@dataclass(frozen=True)
class ContextPackDelta:
    """A prefix-preserving incremental update from one pack to another."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        prefix = 0
        limit = min(len(base.items), len(target.items))
        while prefix < limit and base.items[prefix] == target.items[prefix]:
            prefix += 1
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    def to_bytes(self) -> bytes:
        return _encode_json(
            {
                "schema": DELTA_SCHEMA,
                "base_digest": self.base_digest,
                "target_digest": self.target_digest,
                "prefix_items": self.prefix_items,
                "appended": _b64(self.appended),
            }
        )

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPackDelta:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            raise ContextPackError("delta wire payload must be valid UTF-8 JSON") from error
        if not isinstance(payload, dict) or set(payload) != {
            "schema",
            "base_digest",
            "target_digest",
            "prefix_items",
            "appended",
        }:
            raise ContextPackError("delta wire payload must contain exactly its protocol fields")
        if payload["schema"] != DELTA_SCHEMA:
            raise ContextPackError("delta schema mismatch")
        prefix = payload["prefix_items"]
        if type(prefix) is not int or prefix < 0:
            raise ContextPackError("delta prefix_items must be a non-negative integer")
        base_digest = payload["base_digest"]
        target_digest = payload["target_digest"]
        if not isinstance(base_digest, str) or not isinstance(target_digest, str):
            raise ContextPackError("delta digests must be strings")
        raw_appended = payload["appended"]
        if not isinstance(raw_appended, list):
            raise ContextPackError("delta appended must be an array")
        appended = tuple(_decode_item(item) for item in raw_appended)
        return cls(base_digest, target_digest, prefix, appended)


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Validate a delta against a base pack and rebuild the target pack."""
    if base.digest != delta.base_digest:
        raise ContextPackError("delta base_digest does not match the base pack")
    if type(delta.prefix_items) is not int or delta.prefix_items < 0 or delta.prefix_items > len(base.items):
        raise ContextPackError("delta prefix_items is out of bounds")
    items = base.items[: delta.prefix_items] + delta.appended
    if _digest(items) != delta.target_digest:
        raise ContextPackError("delta target_digest does not match the rebuilt items")
    return ContextPack.build(items)


__all__ = [
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
