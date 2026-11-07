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

"""HTTP scoring-service adapter for listwise Memory reranking."""

from __future__ import annotations

import math

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage


class HTTPMemoryReranker:
    """Score Memory candidates through a bounded offline HTTP protocol."""

    policy_id = "http-cross-encoder-v1"

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Request scores in bounded batches and select the global top ranks."""
        candidate_count = len(candidates)
        if candidate_count < 1:
            raise ValueError("rerank requires at least one candidate")
        if limit < 1 or limit > candidate_count:
            raise ValueError("rerank limit must be between one and the candidate count")

        scores: dict[int, float] = {}
        request_count = 0
        for start in range(0, candidate_count, self._batch_size):
            batch = candidates[start : start + self._batch_size]
            batch_ranks = set(range(start + 1, start + len(batch) + 1))
            request_payload = {
                "query": query,
                "candidates": [
                    {"rank": start + index + 1, "text": candidate.text}
                    for index, candidate in enumerate(batch)
                ],
            }
            response = await self._client.post(self._endpoint, json=request_payload)
            request_count += 1
            response.raise_for_status()
            try:
                body = response.json()
            except ValueError as error:
                raise ValueError("rerank response must be valid JSON") from error

            if not isinstance(body, dict) or set(body) != {"scores"}:
                raise ValueError("rerank response must contain exactly a scores field")
            score_list = body["scores"]
            if not isinstance(score_list, list):
                raise ValueError("rerank scores must be a list")

            batch_scores: dict[int, float] = {}
            observed: set[int] = set()
            for record in score_list:
                if not isinstance(record, dict) or set(record) != {"rank", "score"}:
                    raise ValueError("each score record must contain exactly rank and score")
                rank = record["rank"]
                score = record["score"]
                if type(rank) is not int or rank not in batch_ranks or rank in observed:
                    raise ValueError("response ranks must cover the batch exactly once")
                if not isinstance(score, (int, float)) or not math.isfinite(score):
                    raise ValueError("response scores must be finite numbers")
                observed.add(rank)
                batch_scores[rank] = score
            if observed != batch_ranks:
                raise ValueError("response ranks must cover the batch exactly once")
            scores.update(batch_scores)

        ordered = sorted(range(1, candidate_count + 1), key=lambda rank: (-scores[rank], rank))
        return MemoryRerankDecision(
            selected_ranks=tuple(ordered[:limit]),
            usage=InferenceUsage(requests=request_count),
        )


__all__ = ["HTTPMemoryReranker"]
