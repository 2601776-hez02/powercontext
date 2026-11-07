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

"""Cross-call embedding request coalescing with a bounded result cache."""

from __future__ import annotations

import asyncio
from collections import OrderedDict

from powercontext.builtin.inference.models import EmbeddingResult, InferenceUsage


class CoalescingEmbeddingModel:
    """Coalesce concurrent embedding calls and cache successful vectors."""

    def __init__(
        self,
        model,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")
        if coalesce_ms < 0:
            raise ValueError("coalesce_ms must be non-negative")
        if cache_size < 0:
            raise ValueError("cache_size must be non-negative")
        self._model = model
        self._max_batch_size = max_batch_size
        self._coalesce_ms = coalesce_ms
        self._cache_size = cache_size
        self._cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._inflight: dict[str, asyncio.Future] = {}
        self._pending: list[str] = []
        self._flush_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._future_batch: dict[asyncio.Future, int] = {}
        self._batch_usage: dict[int, InferenceUsage] = {}
        self._next_batch_id = 0

    @property
    def profile(self):
        return self._model.profile

    async def embed(self, texts: tuple[str, ...]) -> EmbeddingResult:
        ordered = list(texts)
        unique = list(dict.fromkeys(ordered))
        if not unique:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))

        loop = asyncio.get_running_loop()
        futures: dict[str, asyncio.Future | None] = {}
        async with self._lock:
            for text in unique:
                if text in self._cache:
                    self._cache.move_to_end(text)
                    futures[text] = None
                elif text in self._inflight:
                    futures[text] = self._inflight[text]
                else:
                    future = loop.create_future()
                    self._inflight[text] = future
                    self._pending.append(text)
                    futures[text] = future
            if self._pending and self._flush_task is None:
                self._flush_task = loop.create_task(self._flush())

        awaited: set[int] = set()
        resolved: dict[str, tuple[float, ...]] = {}
        for text in unique:
            future = futures[text]
            if future is None:
                resolved[text] = self._cache[text]
            else:
                vector = await asyncio.shield(future)
                resolved[text] = vector
                batch_id = self._future_batch.get(future)
                if batch_id is not None:
                    awaited.add(batch_id)

        vectors = tuple(resolved[text] for text in ordered)
        return EmbeddingResult(vectors=vectors, usage=self._merge_usage(awaited))

    async def _flush(self) -> None:
        if self._coalesce_ms > 0:
            await asyncio.sleep(self._coalesce_ms / 1000.0)
        async with self._lock:
            pending = list(self._pending)
            self._pending.clear()
            self._flush_task = None
        for start in range(0, len(pending), self._max_batch_size):
            await self._dispatch(pending[start : start + self._max_batch_size])

    async def _dispatch(self, batch: list[str]) -> None:
        try:
            result = await self._model.embed(tuple(batch))
        except Exception as error:  # noqa: BLE001 - propagate per-batch failures to waiters
            self._fail(batch, error)
            return
        if len(result.vectors) != len(batch):
            self._fail(batch, ValueError("embedding returned the wrong number of vectors"))
            return

        self._next_batch_id += 1
        batch_id = self._next_batch_id
        self._batch_usage[batch_id] = result.usage
        for text, vector in zip(batch, result.vectors):
            future = self._inflight.pop(text, None)
            if future is not None and not future.done():
                self._future_batch[future] = batch_id
                future.set_result(vector)
            self._cache_put(text, vector)

    def _fail(self, batch: list[str], error: Exception) -> None:
        for text in batch:
            future = self._inflight.pop(text, None)
            if future is not None and not future.done():
                future.set_exception(error)

    def _cache_put(self, text: str, vector: tuple[float, ...]) -> None:
        if self._cache_size == 0:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def _merge_usage(self, batch_ids: set[int]) -> InferenceUsage:
        requests = 0
        input_tokens = 0
        output_tokens = 0
        has_input = False
        has_output = False
        for batch_id in batch_ids:
            usage = self._batch_usage.get(batch_id)
            if usage is None:
                continue
            requests += usage.requests
            if usage.input_tokens is not None:
                input_tokens += usage.input_tokens
                has_input = True
            if usage.output_tokens is not None:
                output_tokens += usage.output_tokens
                has_output = True
        return InferenceUsage(
            requests=requests,
            input_tokens=input_tokens if has_input else None,
            output_tokens=output_tokens if has_output else None,
        )


__all__ = ["CoalescingEmbeddingModel"]
