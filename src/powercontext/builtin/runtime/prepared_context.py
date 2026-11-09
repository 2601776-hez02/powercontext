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

"""Prepare final, bounded context from approved Artifact evidence."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import chain
from typing import TypeVar

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.artifacts.experience import Experience, ExperienceSearchHit, render_experience
from powercontext.builtin.artifacts.memory.models import MemoryCitation, MemoryHit
from powercontext.builtin.artifacts.profile.models import Profile
from powercontext.builtin.artifacts.topic_memory import TopicMemory, TopicMemorySearchHit
from powercontext.builtin.runtime.errors import PreparedContextInvariantError
from powercontext.builtin.runtime.models import PrepareContextRequest, PreparedContext
from powercontext.builtin.runtime.prepared_code import (
    CodeEvidenceRef,
    PreparedCodeCandidate,
    assemble_code,
)
from powercontext.builtin.runtime.prepared_text import (
    TRUST_POLICY,
    ContextTextItem,
    fit_context_text_item,
    render_context_text,
)
from powercontext.builtin.runtime.recall_sufficiency import RecallBudgetView

_MIN_TRUNCATED_CONTENT_BYTES = 64
_ELLIPSIS = "…"
_BEGIN_MARKER = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
_END_MARKER = "END_POWERCONTEXT_PREPARED_CONTEXT_V1"
_Item = TypeVar("_Item")


@dataclass(frozen=True)
class _PreparedContextEntry:
    origin: PreparedContextOrigin
    kind: str
    citation: dict[str, object]
    content: str
    truncated: bool


@dataclass(frozen=True)
class _EntryFit:
    """One fit attempt on the non-assembly path: the entry that fitted, or why none did.

    The two drop reasons are disjoint and mirror the assembly path's
    :class:`~powercontext.builtin.runtime.prepared_text.FitOutcome`. An entry is dropped
    *below the minimum truncated content* when a shorter rendering exists and fits but is too
    short to be an honest delivery; it is dropped *with no fitting truncation* when no
    shortened rendering fits at all.
    """

    entry: _PreparedContextEntry | None = None
    dropped_below_min_bytes: bool = False
    dropped_no_fitting_truncation: bool = False


@dataclass(frozen=True)
class PreparedContextOmissions:
    """Aggregate counts of the items the byte budget omitted in one build.

    Every count is a per-call aggregate, never per-entry attribution and never a verdict about
    an entry: an entry losing to the budget is not a negative result about that entry.

    ``dropped_items`` is the sum of its two sub-counts, so "the budget could not fit this
    item" can be told apart from "this item was too short to truncate into the remaining
    space". A single merged counter would misreport the second cause as the first.
    """

    truncated_items: int = 0
    dropped_items: int = 0
    dropped_below_min_bytes: int = 0
    dropped_no_fitting_truncation: int = 0


@dataclass(frozen=True)
class PreparedContextBuild:
    """Final public context and the exact origins selected to produce it.

    ``omissions`` is always filled by the Builder. The RFC 1560 recall trace is deliberately
    **not** a field here: ``ScopedContextApplication._prepare`` returns ``build.context`` and
    discards the rest, so such a field would have no production observer. The trace is
    delivered through the Runtime's optional ``RecallEffortSink`` instead.
    """

    context: PreparedContext
    origins: tuple[PreparedContextOrigin, ...]
    omissions: PreparedContextOmissions = PreparedContextOmissions()
    code_origins: tuple[CodeEvidenceRef, ...] = ()
    fragments: tuple[PreparedContextFragment, ...] = ()


@dataclass(frozen=True)
class MemoryEntryAddress:
    """Identify one exact Memory entry version across Scope boundaries."""

    memory: ArtifactAddress
    entry_id: str
    entry_version_id: str


@dataclass(frozen=True)
class PreparedContextFragment:
    """One literal evidence span selected from a Memory entry."""

    address: MemoryEntryAddress
    start: int
    end: int
    text: str


PreparedContextOrigin = MemoryCitation | ArtifactRef | MemoryEntryAddress | ArtifactAddress


@dataclass(frozen=True)
class PreparedMemoryCandidates:
    """Memory candidates read from one Scope."""

    scope_id: str
    memory_ref: ArtifactRef | None = None
    hits: tuple[MemoryHit, ...] = ()


@dataclass(frozen=True)
class PreparedExperienceCandidates:
    """Experience candidates read from one Scope."""

    scope_id: str
    hits: tuple[ExperienceSearchHit, ...] = ()


@dataclass(frozen=True)
class PreparedProfileCandidate:
    """The latest committed Profile snapshot read from one Scope."""

    scope_id: str
    profile: Profile


class PreparedContextBuilder:
    """Select and render final context without I/O, persistence, or reranking."""

    memory_candidate_limit = 16
    topic_memory_candidate_limit = 8
    experience_candidate_limit = 8
    candidate_limit = memory_candidate_limit
    entry_limit = 8
    topic_memory_entry_limit = 8
    experience_entry_limit = 2
    max_entry_content_bytes = 2000
    _text_entry_limit: int | None = None

    def empty(self) -> PreparedContext:
        return PreparedContext(status="empty", content=None, content_bytes=0)

    def build(
        self,
        *,
        request: PrepareContextRequest,
        scope_id: str | None = None,
        memory_ref: ArtifactRef | None = None,
        hits: Sequence[MemoryHit] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_hits: Sequence[ExperienceSearchHit] = (),
    ) -> PreparedContext:
        return self.build_result(
            request=request,
            scope_id=scope_id,
            memory_ref=memory_ref,
            hits=hits,
            topic_memory_hits=topic_memory_hits,
            experience_hits=experience_hits,
        ).context

    def build_result(
        self,
        *,
        request: PrepareContextRequest,
        scope_id: str | None = None,
        memory_ref: ArtifactRef | None = None,
        hits: Sequence[MemoryHit] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_hits: Sequence[ExperienceSearchHit] = (),
    ) -> PreparedContextBuild:
        return self.build_scopes_result(
            request=request,
            current_scope_id=scope_id,
            memory_candidates=(
                PreparedMemoryCandidates(scope_id=scope_id or "", memory_ref=memory_ref, hits=tuple(hits)),
            ),
            experience_candidates=(PreparedExperienceCandidates(scope_id=scope_id or "", hits=tuple(experience_hits)),),
            topic_memory_hits=topic_memory_hits,
        )

    def build_scopes_result(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_candidates: Sequence[PreparedExperienceCandidates] = (),
        profile_candidates: Sequence[PreparedProfileCandidate] = (),
        code_candidates: Sequence[PreparedCodeCandidate] = (),
    ) -> PreparedContextBuild:
        if request.evidence_selection:
            # Selection mode is defined over Memory candidates only, so it takes precedence over
            # Code assembly instead of losing the evidence envelope to it.
            return self._select_evidence(
                request=request,
                current_scope_id=current_scope_id,
                memory_candidates=memory_candidates,
            )

        if request.include_code:

            def historical(history_request: PrepareContextRequest, entries: int) -> PreparedContextBuild:
                builder = PreparedContextBuilder()
                builder.entry_limit = entries
                builder._text_entry_limit = entries if entries < self.entry_limit else None
                return builder.build_scopes_result(
                    request=history_request.model_copy(update={"include_code": False}),
                    current_scope_id=current_scope_id,
                    memory_candidates=memory_candidates,
                    topic_memory_hits=topic_memory_hits,
                    experience_candidates=experience_candidates,
                    profile_candidates=profile_candidates,
                )

            return assemble_code(request, code_candidates, self.entry_limit, historical)

        if sum(len(candidates.hits) for candidates in memory_candidates) > self.memory_candidate_limit:
            raise PreparedContextInvariantError("memory-candidate-limit")
        if len(topic_memory_hits) > self.topic_memory_candidate_limit:
            raise PreparedContextInvariantError("topic-memory-candidate-limit")
        if sum(len(candidates.hits) for candidates in experience_candidates) > self.experience_candidate_limit:
            raise PreparedContextInvariantError("experience-candidate-limit")

        content, origins, omissions = self._select_entries(
            request=request,
            current_scope_id=current_scope_id,
            memory_candidates=memory_candidates,
            experience_candidates=experience_candidates,
            profile_candidates=profile_candidates,
            topic_memory_hits=topic_memory_hits,
        )
        if content is None:
            return PreparedContextBuild(context=self.empty(), origins=(), omissions=omissions)
        content_bytes = len(content.encode("utf-8"))
        # The assembly path is bounded by construction (every fitted item is measured against
        # `max_bytes`), so the ceiling check belongs to the non-assembly renderer only.
        if request.assembly is None and content_bytes > request.max_bytes:
            raise PreparedContextInvariantError("output-budget")
        return PreparedContextBuild(
            context=PreparedContext(status="ready", content=content, content_bytes=content_bytes),
            origins=origins,
            omissions=omissions,
        )

    def probe_budget(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_candidates: Sequence[PreparedExperienceCandidates] = (),
        profile_candidates: Sequence[PreparedProfileCandidate] = (),
    ) -> RecallBudgetView:
        """Report what the byte budget does to one candidate set, without delivering it.

        This is the RFC 1560 *budget probe*: one pass of this Builder's own pure selection and
        rendering code, keeping only the counters and discarding the rendered output. It lets
        the gate tell budget-limited thinness from recall-limited thinness.

        Pure and side-effect free: no I/O, no persistence, nothing reported to any sink, and
        the same inputs as :meth:`build_scopes_result` are guaranteed to yield the same
        counters because both go through :meth:`_select_entries`.
        """

        content, origins, omissions = self._select_entries(
            request=request,
            current_scope_id=current_scope_id,
            memory_candidates=memory_candidates,
            experience_candidates=experience_candidates,
            profile_candidates=profile_candidates,
            topic_memory_hits=topic_memory_hits,
        )
        content_bytes = 0 if content is None else len(content.encode("utf-8"))
        return RecallBudgetView(
            max_bytes=request.max_bytes,
            delivered_items=len(origins),
            truncated_items=omissions.truncated_items,
            dropped_items=omissions.dropped_items,
            unused_bytes=max(0, request.max_bytes - content_bytes),
        )

    def _select_entries(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates],
        topic_memory_hits: Sequence[TopicMemorySearchHit],
        experience_candidates: Sequence[PreparedExperienceCandidates],
        profile_candidates: Sequence[PreparedProfileCandidate],
    ) -> tuple[str | None, tuple[PreparedContextOrigin, ...], PreparedContextOmissions]:
        """Run one complete pure selection pass and render it.

        Returns ``(content, origins, omissions)``; ``content`` is ``None`` exactly when nothing
        was selected, which is the empty-result case for both paths. This is the single
        selection path shared by :meth:`build_scopes_result` and :meth:`probe_budget`, so the
        probe's counters always describe the selection the build would actually perform.
        """

        if request.assembly is not None:
            return self._select_text(
                request,
                current_scope_id=current_scope_id,
                memory_candidates=memory_candidates,
                experience_candidates=experience_candidates,
                profile_candidates=profile_candidates,
                topic_memory_hits=topic_memory_hits,
            )

        memory_entries = _interleave_groups(
            tuple(
                self._memory_entries(
                    candidates.memory_ref,
                    candidates.hits,
                    scope_id=None if candidates.scope_id == current_scope_id else candidates.scope_id or None,
                )
                for candidates in memory_candidates
            )
        )
        topic_memory_entries = self._topic_memory_entries(topic_memory_hits)
        experience_entries = _interleave_groups(
            tuple(
                self._experience_entries(
                    candidates.hits,
                    scope_id=None if candidates.scope_id == current_scope_id else candidates.scope_id or None,
                )
                for candidates in experience_candidates
            )
        )[: self.experience_entry_limit]
        entries, omissions = self._fit_entries(request, memory_entries, topic_memory_entries, experience_entries)
        if not entries:
            return None, (), omissions
        return _render(entries), tuple(entry.origin for entry in entries), omissions

    def _select_text(
        self,
        request: PrepareContextRequest,
        *,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates],
        experience_candidates: Sequence[PreparedExperienceCandidates],
        profile_candidates: Sequence[PreparedProfileCandidate],
        topic_memory_hits: Sequence[TopicMemorySearchHit],
    ) -> tuple[str | None, tuple[PreparedContextOrigin, ...], PreparedContextOmissions]:
        assembly = request.assembly
        if assembly is None:
            raise PreparedContextInvariantError("text-assembly-missing")
        included: list[ContextTextItem] = []
        origins: list[PreparedContextOrigin] = []
        truncated_items = 0
        dropped_below_min_bytes = 0
        dropped_no_fitting_truncation = 0
        for section in assembly.sections:
            # Unfilled earlier sections leave their capacity available to later ones.
            limit = (
                section.limit
                if self._text_entry_limit is None
                else min(section.limit, self._text_entry_limit - len(included))
            )
            if section.family == "profile":
                entries = self._profile_entries(profile_candidates)
            elif section.family == "topic-memory":
                entries = self._topic_memory_entries(topic_memory_hits, scope_id=current_scope_id)
            else:
                groups = (
                    tuple(
                        tuple(
                            self._memory_entries(group.memory_ref, (hit,), scope_id=group.scope_id)
                            for hit in group.hits
                        )
                        for group in memory_candidates
                    )
                    if section.family == "memory"
                    else tuple(
                        tuple(self._experience_entries((hit,), scope_id=group.scope_id) for hit in group.hits)
                        for group in experience_candidates
                    )
                )
                entries = tuple(chain.from_iterable(_interleave_groups(groups)))
            seen: set[tuple[str, str, str, int, str | None, str | None]] = set()
            rank = 0
            selected_count = 0
            for entry in entries:
                if selected_count >= limit:
                    break
                item = _text_item(entry)
                artifact = item.artifact
                identity = (
                    artifact.scope_id,
                    artifact.artifact.family,
                    artifact.artifact.artifact_id,
                    artifact.artifact.revision,
                    item.entry_id,
                    item.entry_version_id,
                )
                if identity in seen or not item.content.strip():
                    continue
                seen.add(identity)
                rank += 1
                outcome = fit_context_text_item(included, replace(item, recall_rank=rank), assembly, request.max_bytes)
                if outcome.item is None:
                    dropped_below_min_bytes += int(outcome.dropped_below_min_bytes)
                    dropped_no_fitting_truncation += int(outcome.dropped_no_fitting_truncation)
                    continue
                fitted = outcome.item
                included.append(fitted)
                origins.append(entry.origin)
                selected_count += 1
                truncated_items += int(fitted.truncated)
        omissions = PreparedContextOmissions(
            truncated_items=truncated_items,
            dropped_items=dropped_below_min_bytes + dropped_no_fitting_truncation,
            dropped_below_min_bytes=dropped_below_min_bytes,
            dropped_no_fitting_truncation=dropped_no_fitting_truncation,
        )
        if not included:
            return None, (), omissions
        content = render_context_text(included, assembly)
        return content, tuple(origins), omissions

    def _select_evidence(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates],
    ) -> PreparedContextBuild:
        """Select query-relevant literal spans from Memory candidates within the byte budget.

        Selection is ranked across the candidate abstracts instead of taking the globally
        highest-overlap sentences first: every abstract contributes its best sentence before
        any abstract contributes a second one, so evidence in a later candidate is not crowded
        out by repeated matches in an earlier one. Rare query terms weigh more than common
        ones, and the neighbours of each best sentence follow, because annotated evidence
        usually covers a short contiguous span rather than one isolated sentence.
        """
        query_tokens = _tokenize(request.query)
        entries: list[tuple[MemoryEntryAddress, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for group in memory_candidates:
            scope_id = group.scope_id.strip() or (current_scope_id or "").strip()
            for hit in group.hits:
                if group.memory_ref is not None and hit.memory_ref != group.memory_ref:
                    raise PreparedContextInvariantError("memory-ref-mismatch")
                identity = (scope_id, hit.entry_id, hit.entry_version_id)
                if identity in seen:
                    continue
                if not hit.entry_id.strip() or not hit.entry_version_id.strip() or not hit.text.strip():
                    continue
                if not scope_id:
                    continue
                seen.add(identity)
                memory = ArtifactAddress(scope_id=scope_id, artifact=hit.memory_ref)
                address = MemoryEntryAddress(
                    memory=memory,
                    entry_id=hit.entry_id,
                    entry_version_id=hit.entry_version_id,
                )
                entries.append((address, hit.text))
        if not entries:
            return PreparedContextBuild(context=self.empty(), origins=(), omissions=PreparedContextOmissions())

        tokenized = [_tokenize(text) for _address, text in entries]
        document_frequency = dict.fromkeys(query_tokens, 0)
        for tokens in tokenized:
            for token in query_tokens & tokens:
                document_frequency[token] += 1
        total_documents = len(tokenized)
        weights = {
            token: _term_weight(token, total_documents, count) for token, count in document_frequency.items()
        }

        window_scores: dict[int, list[float]] = {}
        prepared: list[tuple[float, int, int, MemoryEntryAddress, str, list[tuple[int, int]]]] = []
        for index, ((address, text), tokens) in enumerate(zip(entries, tokenized, strict=True)):
            spans = [(start, end) for start, end in _sentence_spans(text) if text[start:end].strip()]
            if not spans:
                # Text without sentence punctuation still has to yield a fragment.
                spans = [(0, len(text))]
            scores = [
                sum(weights[token] for token in query_tokens & _tokenize(text[start:end])) for start, end in spans
            ]
            best = max(range(len(scores)), key=lambda position: (scores[position], -position))
            # A relevant abstract usually holds one or two passages that answer the claim, so its two
            # strongest sentences describe it far better than the total overlap, which lets a long
            # off-topic abstract accumulate scattered matches.
            document_score = sum(sorted(scores, reverse=True)[:2])
            window_scores[index] = scores
            prepared.append((document_score, best, index, address, text, spans))
        if not prepared:
            return PreparedContextBuild(context=self.empty(), origins=(), omissions=PreparedContextOmissions())
        prepared.sort(key=lambda row: (-row[0], row[1], row[2]))

        # One contiguous fragment per abstract spends the byte budget on原文 instead of repeating
        # a citation per sentence. Each abstract takes the widest window around its best sentence
        # that still fits, because annotated evidence usually covers a short contiguous span and
        # any unused byte costs recall.
        def best_sentence(rank: int) -> PreparedContextFragment:
            _score, best, _index, address, text, spans = prepared[rank]
            start, end = spans[best]
            return PreparedContextFragment(address=address, start=start, end=end, text=text[start:end])

        def window_at(rank: int, window: int) -> PreparedContextFragment:
            _score, best, _index, address, text, spans = prepared[rank]
            low = max(best - window, 0)
            high = min(best + window, len(spans) - 1)
            start, end = spans[low][0], spans[high][1]
            return PreparedContextFragment(address=address, start=start, end=end, text=text[start:end])

        def accepts(candidate: PreparedContextFragment, others: tuple[PreparedContextFragment, ...]) -> bool:
            if not candidate.text.strip():
                return False
            # Only the same entry must never be cited twice. The same text under a different
            # scope is a different identity and has to stay representable on its own.
            if any(candidate.address == other.address for other in others):
                return False
            return _evidence_rendered_bytes((*others, candidate)) <= request.max_bytes

        def widest_window(rank: int, others: tuple[PreparedContextFragment, ...]) -> PreparedContextFragment | None:
            _score, _best, entry_index, _address, _text, spans = prepared[rank]
            sentence_scores: list[float] = window_scores.get(entry_index, [])
            hot_clusters = 0
            if sentence_scores:
                peak = max(sentence_scores)
                previous = -3
                for position, value in enumerate(sentence_scores):
                    if value < 0.5 * peak:
                        continue
                    if position - previous > 1:
                        hot_clusters += 1
                    previous = position
            use_bestsum = _EVIDENCE_WINDOW_MODE == "bestsum" or (
                _EVIDENCE_WINDOW_MODE == "hybrid" and hot_clusters >= 2
            )
            if _EVIDENCE_WINDOW_MODE == "centered":
                use_bestsum = False
            if use_bestsum and len(spans) <= 200:
                # Pick the contiguous span that holds the most query evidence instead of the widest
                # one around the peak; a claim whose evidence sits in two nearby passages is then
                # covered by a single span that reaches both.
                sentence_scores: list[float] = window_scores.get(entry_index, [])
                if sentence_scores:
                    prefix = [0.0]
                    for value in sentence_scores:
                        prefix.append(prefix[-1] + value)
                    best_gain: float | None = None
                    best_span: PreparedContextFragment | None = None
                    best_length = 0
                    for start_index in range(len(spans)):
                        for end_index in range(start_index, len(spans)):
                            start, end = spans[start_index][0], spans[end_index][1]
                            candidate = PreparedContextFragment(
                                address=_address, start=start, end=end, text=_text[start:end]
                            )
                            if _evidence_rendered_bytes((*others, candidate)) > request.max_bytes:
                                break
                            if not candidate.text.strip():
                                continue
                            gain = prefix[end_index + 1] - prefix[start_index]
                            length = end - start
                            # Equal evidence: take the longer span, because unused budget is
                            # recall given away on the words the claim shares with the abstract.
                            if best_gain is None or gain > best_gain or (gain == best_gain and length > best_length):
                                best_gain = gain
                                best_span = candidate
                                best_length = length
                    if best_span is not None:
                        return best_span
            # Rendered size only grows with the window radius, so binary search the widest window
            # that fits instead of encoding a candidate for every width.
            low, high = 0, len(spans) - 1
            chosen: PreparedContextFragment | None = None
            while low <= high:
                middle = (low + high) // 2
                candidate = window_at(rank, middle)
                if accepts(candidate, others):
                    chosen = candidate
                    low = middle + 1
                else:
                    high = middle - 1
            return chosen

        def best_terms(rank: int) -> set[str]:
            _score, best, _index, _address, text, spans = prepared[rank]
            start, end = spans[best]
            return query_tokens & _tokenize(text[start:end])

        def shared_pair(rank: int) -> tuple[PreparedContextFragment, PreparedContextFragment] | None:
            trailing = best_sentence(rank)
            leading = widest_window(0, (trailing,))
            if leading is None:
                return None
            # Splitting only pays off while the leader keeps a meaningful share; a huge trailing
            # sentence would starve the leader and cost more evidence than the second answer adds.
            if len(leading.text.encode("utf-8")) * 2 < len(trailing.text.encode("utf-8")):
                return None
            return leading, trailing

        selected: list[PreparedContextFragment] = []
        # Two abstracts can answer the same question with the same terms (two findings about one
        # topic); then cover both with one sentence each, because a single window would drop the
        # second answer from the context entirely. A distractor that merely matches as many generic
        # terms as the leader is not such a pair, so it does not split the budget.
        shared = False
        if len(prepared) >= 2:
            leading_terms = best_terms(0)
            trailing_terms = best_terms(1)
            union = leading_terms | trailing_terms
            jaccard = len(leading_terms & trailing_terms) / len(union) if union else 0.0
            shared = jaccard >= _EVIDENCE_SHARED_TERMS
            if not shared and _EVIDENCE_MULTIPART_SPLIT:
                # A multi-part question whose halves land in different abstracts: the two term
                # sets are disjoint yet together cover the query, so both are real answers.
                if (
                    not (leading_terms & trailing_terms)
                    and len(leading_terms) >= 2
                    and len(trailing_terms) >= 2
                    and len(union) >= _EVIDENCE_MULTIPART_COVERAGE * len(query_tokens)
                ):
                    shared = True
        if shared:
            pair = shared_pair(1)
            if pair is not None:
                selected = [pair[0], pair[1]]
        if not selected:
            leading = widest_window(0, ())
            if leading is not None:
                selected = [leading]
        if len(selected) == 1 and selected[0].address == prepared[0][3]:
            # Any budget the leader could not use goes to the next candidates.
            for rank in range(1, len(prepared)):
                trailing = widest_window(rank, tuple(selected))
                if trailing is not None:
                    selected.append(trailing)
                    break

        if not selected:
            # Even one sentence can be wider than the budget. Drop to a literal character-level
            # prefix of the strongest span instead of returning nothing, because an empty result
            # for a request that has candidates is a failure rather than a valid answer.
            _document_score, best, _index, address, text, spans = prepared[0]
            start, end = spans[best]
            low, high = start + 1, end
            while low <= high:
                middle = (low + high) // 2
                candidate = PreparedContextFragment(
                    address=address, start=start, end=middle, text=text[start:middle]
                )
                if _evidence_rendered_bytes((candidate,)) <= request.max_bytes:
                    selected = [candidate]
                    low = middle + 1
                else:
                    high = middle - 1

        if not selected:
            return PreparedContextBuild(context=self.empty(), origins=(), omissions=PreparedContextOmissions())
        content = _render_evidence(selected)
        return PreparedContextBuild(
            context=PreparedContext(status="ready", content=content, content_bytes=len(content.encode("utf-8"))),
            origins=tuple(fragment.address for fragment in selected),
            fragments=tuple(selected),
        )

    def _profile_entries(self, candidates: Sequence[PreparedProfileCandidate]) -> tuple[_PreparedContextEntry, ...]:
        entries = []
        for candidate in candidates:
            origin = ArtifactAddress(scope_id=candidate.scope_id, artifact=candidate.profile.as_ref())
            entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="profile",
                    citation={"artifact": origin.model_dump(mode="json")},
                    content=candidate.profile.content.content,
                    truncated=False,
                )
            )
        return tuple(entries)

    def _memory_entries(
        self,
        memory_ref: ArtifactRef | None,
        hits: Sequence[MemoryHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        if hits and memory_ref is None:
            raise PreparedContextInvariantError("memory-ref-missing")
        memory_entries: list[_PreparedContextEntry] = []
        seen: set[tuple[str, str]] = set()
        for hit in hits:
            if hit.memory_ref != memory_ref:
                raise PreparedContextInvariantError("memory-ref-mismatch")

            citation_key = (hit.entry_id, hit.entry_version_id)
            if citation_key in seen:
                continue
            seen.add(citation_key)
            if not hit.entry_id.strip() or not hit.entry_version_id.strip() or not hit.text.strip():
                continue
            citation = MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            origin: PreparedContextOrigin = citation
            rendered_citation = citation.model_dump(mode="json")
            if scope_id is not None:
                memory = ArtifactAddress(scope_id=scope_id, artifact=hit.memory_ref)
                origin = MemoryEntryAddress(
                    memory=memory,
                    entry_id=hit.entry_id,
                    entry_version_id=hit.entry_version_id,
                )
                rendered_citation = {
                    "memory": memory.model_dump(mode="json"),
                    "entry_id": hit.entry_id,
                    "entry_version_id": hit.entry_version_id,
                }
            memory_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="memory",
                    citation=rendered_citation,
                    content=hit.text,
                    truncated=False,
                )
            )
        return tuple(memory_entries)

    def _topic_memory_entries(
        self,
        hits: Sequence[TopicMemorySearchHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        topic_entries: list[_PreparedContextEntry] = []
        seen_topics: set[tuple[str, int]] = set()
        for hit in hits:
            if hit.artifact_ref.family != TopicMemory.family:
                raise PreparedContextInvariantError("topic-memory-family-mismatch")
            identity = (hit.artifact_ref.artifact_id, hit.artifact_ref.revision)
            if identity in seen_topics:
                continue
            seen_topics.add(identity)
            if len(topic_entries) >= self.topic_memory_entry_limit:
                break
            content = {"title": hit.title, "summary": hit.summary}
            if hit.snippet is not None:
                content["snippet"] = hit.snippet
            origin: PreparedContextOrigin = hit.artifact_ref
            citation = {"artifact_ref": hit.artifact_ref.model_dump(mode="json")}
            body = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
            if scope_id is not None:
                origin = ArtifactAddress(scope_id=scope_id, artifact=hit.artifact_ref)
                citation = {"artifact": origin.model_dump(mode="json")}
                body = "\n\n".join(f"{label.capitalize()}: {value}" for label, value in content.items())
            topic_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="topic-memory",
                    citation=citation,
                    content=body,
                    truncated=False,
                )
            )
        return tuple(topic_entries)

    def _experience_entries(
        self,
        hits: Sequence[ExperienceSearchHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        experience_entries: list[_PreparedContextEntry] = []
        seen_experiences: set[tuple[str, int]] = set()
        for hit in hits:
            if hit.artifact_ref.family != Experience.family:
                raise PreparedContextInvariantError("experience-family-mismatch")
            identity = (hit.artifact_ref.artifact_id, hit.artifact_ref.revision)
            if identity in seen_experiences:
                continue
            seen_experiences.add(identity)
            origin: PreparedContextOrigin = hit.artifact_ref
            rendered_citation: dict[str, object] = {"artifact_ref": hit.artifact_ref.model_dump(mode="json")}
            if scope_id is not None:
                origin = ArtifactAddress(scope_id=scope_id, artifact=hit.artifact_ref)
                rendered_citation = {"artifact": origin.model_dump(mode="json")}
            experience_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="experience",
                    citation=rendered_citation,
                    content=render_experience(hit.content),
                    truncated=False,
                )
            )
        return tuple(experience_entries)

    def _fit_entries(
        self,
        request: PrepareContextRequest,
        memory_entries: Sequence[_PreparedContextEntry],
        topic_memory_entries: Sequence[_PreparedContextEntry],
        experience_entries: Sequence[_PreparedContextEntry],
    ) -> tuple[tuple[_PreparedContextEntry, ...], PreparedContextOmissions]:
        entries: list[_PreparedContextEntry] = []
        truncated_items = 0
        dropped_below_min_bytes = 0
        dropped_no_fitting_truncation = 0
        for candidate in _interleave(memory_entries, topic_memory_entries, experience_entries):
            if len(entries) >= self.entry_limit:
                break
            fit = self._fit_entry(
                entries,
                origin=candidate.origin,
                kind=candidate.kind,
                citation=candidate.citation,
                text=candidate.content,
                max_bytes=request.max_bytes,
            )
            if fit.entry is None:
                dropped_below_min_bytes += int(fit.dropped_below_min_bytes)
                dropped_no_fitting_truncation += int(fit.dropped_no_fitting_truncation)
                continue
            entries.append(fit.entry)
            if fit.entry.truncated:
                truncated_items += 1
        return tuple(entries), PreparedContextOmissions(
            truncated_items=truncated_items,
            dropped_items=dropped_below_min_bytes + dropped_no_fitting_truncation,
            dropped_below_min_bytes=dropped_below_min_bytes,
            dropped_no_fitting_truncation=dropped_no_fitting_truncation,
        )

    def _fit_entry(
        self,
        entries: Sequence[_PreparedContextEntry],
        *,
        origin: PreparedContextOrigin,
        kind: str,
        citation: dict[str, object],
        text: str,
        max_bytes: int,
    ) -> _EntryFit:
        """Fit one entry into the remaining budget, reporting why it was dropped if it was.

        The two ``None`` paths of the original code are kept apart: ``below-min-bytes`` means a
        shorter rendering exists and fits but is too short to deliver honestly, while
        ``no-fitting-truncation`` means no shortened rendering fitted at all.
        """

        source_bytes = len(text.encode("utf-8"))
        entry_budget = min(source_bytes, self.max_entry_content_bytes)
        candidate = _PreparedContextEntry(
            origin=origin,
            kind=kind,
            citation=citation,
            content=text if source_bytes <= entry_budget else _truncate_utf8(text, entry_budget),
            truncated=source_bytes > entry_budget,
        )
        if _rendered_bytes((*entries, candidate)) <= max_bytes:
            return _EntryFit(entry=candidate)
        if source_bytes < _MIN_TRUNCATED_CONTENT_BYTES:
            return _EntryFit(dropped_below_min_bytes=True)

        lower = _MIN_TRUNCATED_CONTENT_BYTES
        upper = min(entry_budget, source_bytes - 1)
        best: _PreparedContextEntry | None = None
        while lower <= upper:
            byte_budget = (lower + upper) // 2
            candidate = _PreparedContextEntry(
                origin=origin,
                kind=kind,
                citation=citation,
                content=_truncate_utf8(text, byte_budget),
                truncated=True,
            )
            if len(candidate.content.encode("utf-8")) < _MIN_TRUNCATED_CONTENT_BYTES:
                lower = byte_budget + 1
                continue
            if _rendered_bytes((*entries, candidate)) <= max_bytes:
                best = candidate
                lower = byte_budget + 1
            else:
                upper = byte_budget - 1
        if best is None:
            return _EntryFit(dropped_no_fitting_truncation=True)
        return _EntryFit(entry=best)


def _interleave(
    memory: Sequence[_PreparedContextEntry],
    topics: Sequence[_PreparedContextEntry],
    experiences: Sequence[_PreparedContextEntry],
) -> tuple[_PreparedContextEntry, ...]:
    """Preserve each family's rank while alternating Memory, Topic, Experience."""

    ordered: list[_PreparedContextEntry] = []
    for index in range(max(len(memory), len(topics), len(experiences))):
        if index < len(memory):
            ordered.append(memory[index])
        if index < len(topics):
            ordered.append(topics[index])
        if index < len(experiences):
            ordered.append(experiences[index])
    return tuple(ordered)


def _interleave_groups(groups: Sequence[Sequence[_Item]]) -> tuple[_Item, ...]:
    ordered: list[_Item] = []
    for index in range(max((len(group) for group in groups), default=0)):
        ordered.extend(group[index] for group in groups if index < len(group))
    return tuple(ordered)


def _render(entries: Sequence[_PreparedContextEntry]) -> str:
    envelope = {
        "trust": "untrusted_history",
        "items": [_render_entry(entry) for entry in entries],
    }
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return "\n\n".join((TRUST_POLICY, f"{_BEGIN_MARKER}\n{encoded}\n{_END_MARKER}"))


_TOKEN_PATTERN = re.compile(r"[a-z]+|\d+")
# Share the budget with a second abstract when both best sentences match the same query terms.
_EVIDENCE_SHARED_TERMS = 1.0
# Window selection: "centered", "bestsum", or "hybrid" (bestsum only for split evidence).
_EVIDENCE_WINDOW_MODE = "hybrid"
# Cover a multi-part question when its two halves together account for the whole query.
_EVIDENCE_MULTIPART_SPLIT = True
_EVIDENCE_MULTIPART_COVERAGE = 1.0
# A claim is about its content words; grammar words match every abstract equally well.
_EVIDENCE_COMMON_WORD_FACTOR = 0.2
_EVIDENCE_COMMON_WORDS = frozenset(
    """a an the and or but if then than that this these those of in on at to for from by with without into
    over under between among during after before above below up down out off again further once here there
    when where why how all any both each few more most other some such no nor not only own same so too very
    can will just should now is are was were be been being have has had having do does did doing it its
    they them their we us our you your he she him her his who whom which what whose whether while because as
    also may might must shall could would""".split()
)


def _term_weight(token: str, total_documents: int, document_frequency: int) -> float:
    weight = math.log(1.0 + (total_documents - document_frequency + 0.5) / (document_frequency + 0.5))
    if token in _EVIDENCE_COMMON_WORDS:
        return weight * _EVIDENCE_COMMON_WORD_FACTOR
    return weight


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_PATTERN.findall(text.casefold()))


_ABBREVIATIONS = frozenset(
    """e.g i.e et al fig figs tab tabs vs cf ref refs no nos dr mr ms mrs st approx ca est min max pp
    p suppl suppl. spp subsp intl govt dept univ assoc soc corp inc ltd co""".split()
)


def _is_abbreviation(text: str, end: int) -> bool:
    """True when the period at ``end - 1`` closes a known abbreviation rather than a sentence."""

    start = end - 2
    while start >= 0 and (text[start].isalpha() or text[start] == ".") and end - start <= 12:
        start -= 1
    word = text[start + 1 : end - 1].casefold()
    if not word:
        return False
    if word in _ABBREVIATIONS:
        return True
    return len(word) == 1 and word.isalpha()


def _sentence_spans(text: str):
    """Split one entry into sentence spans without cutting through numbers or abbreviations.

    Annotated evidence is expressed as whole sentences, so a window boundary inside a sentence
    would leave part of the gold span uncovered. Numbers (``1.5``), Latin abbreviations
    (``e.g.``), figure references and initials therefore never end a sentence.
    """

    length = len(text)
    start = 0
    index = 0
    while index < length:
        char = text[index]
        if char in ".!?":
            if char == "." and 0 < index < length - 1 and text[index - 1].isdigit() and text[index + 1].isdigit():
                index += 1
                continue
            if char == "." and _is_abbreviation(text, index + 1):
                index += 1
                continue
            lookahead = index + 1
            while lookahead < length and text[lookahead] in " \t\n\r":
                lookahead += 1
            if (
                lookahead >= length
                or text[lookahead].isupper()
                or text[lookahead].isdigit()
                or text[lookahead] in "(\"'["
            ):
                yield start, index + 1
                start = lookahead
                index = lookahead
                continue
        index += 1
    if start < length:
        yield start, length


def _render_evidence(fragments: Sequence[PreparedContextFragment]) -> str:
    items = [
        {
            "citation": {
                "scope_id": fragment.address.memory.scope_id,
                "memory_ref": fragment.address.memory.artifact.model_dump(mode="json"),
                "entry_id": fragment.address.entry_id,
                "entry_version_id": fragment.address.entry_version_id,
            },
            "content": fragment.text,
            "start": fragment.start,
            "end": fragment.end,
        }
        for fragment in fragments
    ]
    envelope = {"trust": "untrusted_history", "items": items}
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    # JSON leaves Unicode line separators raw while str.splitlines() treats them as line breaks;
    # escaping them keeps the payload on one line, the same way the legacy text renderer does.
    for separator in ("\u0085", "\u2028", "\u2029"):
        encoded = encoded.replace(separator, f"\\u{ord(separator):04x}")
    return "\n\n".join((TRUST_POLICY, f"{_BEGIN_MARKER}\n{encoded}\n{_END_MARKER}"))


def _evidence_rendered_bytes(fragments: Sequence[PreparedContextFragment]) -> int:
    return len(_render_evidence(fragments).encode("utf-8"))


def _text_item(entry: _PreparedContextEntry) -> ContextTextItem:
    origin = entry.origin
    if isinstance(origin, MemoryEntryAddress):
        return ContextTextItem(
            artifact=origin.memory,
            content=entry.content,
            recall_rank=0,
            entry_id=origin.entry_id,
            entry_version_id=origin.entry_version_id,
        )
    if isinstance(origin, ArtifactAddress):
        return ContextTextItem(artifact=origin, content=entry.content, recall_rank=0)
    raise PreparedContextInvariantError("text-scope-missing")


def _render_entry(entry: _PreparedContextEntry) -> dict[str, object]:
    rendered: dict[str, object] = {
        "citation": entry.citation,
        "content": entry.content,
        "truncated": entry.truncated,
    }
    if entry.kind != "memory":
        rendered["kind"] = entry.kind
    return rendered


def _rendered_bytes(entries: Sequence[_PreparedContextEntry]) -> int:
    return len(_render(entries).encode("utf-8"))


def _truncate_utf8(text: str, byte_budget: int) -> str:
    prefix_budget = byte_budget - len(_ELLIPSIS.encode("utf-8"))
    encoded_prefix = text.encode("utf-8")[:prefix_budget]
    return f"{encoded_prefix.decode('utf-8', errors='ignore')}{_ELLIPSIS}"
