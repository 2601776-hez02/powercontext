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

"""Complete financial-report PDF parsing into page-level captured Sources."""

from __future__ import annotations

import hashlib
import io
import math
import re
from collections import Counter
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel
from pypdf import PdfReader

from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

PDF_PAGE_SOURCE_NAME = "pdf-page"


class PDFDocumentInput(BaseModel):
    """One complete offline PDF document."""

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    """Definition-native value for exactly one physical PDF page."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    """Captured PDF page keeping its document identity and physical page number."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def _pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    identity = f"pdf:{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(identity) <= 256:
        return identity
    # A long caller-supplied document id cannot enlarge the identity past the contract bound.
    digest = hashlib.sha256(document_id.encode("utf-8")).hexdigest()
    return f"pdf:h:{digest}:{document_sha256}:{page_number}"


def _pdf_page_key(source_id: str) -> tuple[str, int] | None:
    parts = source_id.split(":")
    if len(parts) == 5 and parts[0] == "pdf" and parts[1] == "h":
        try:
            return f"h:{parts[2]}", int(parts[4])
        except ValueError:
            return None
    if len(parts) != 4 or parts[0] != "pdf":
        return None
    try:
        return parts[2], int(parts[3])
    except ValueError:
        return None


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Parse every physical page of a PDF into ordered page inputs."""
    document_id = value.document_id
    if not isinstance(document_id, str) or not document_id or not document_id.strip():
        raise ValueError("document_id must be a non-empty non-blank string")
    document_sha256 = hashlib.sha256(value.content).hexdigest()

    try:
        reader = PdfReader(io.BytesIO(value.content))
        if reader.is_encrypted:
            reader.decrypt("")
        pages: list[PDFPageInput] = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            pages.append(
                PDFPageInput(
                    document_id=document_id,
                    document_sha256=document_sha256,
                    page_number=page_number,
                    text=text,
                )
            )
        return tuple(pages)
    except ValueError:
        raise
    except Exception as error:  # noqa: BLE001 - corrupt or unreadable PDFs fail atomically
        raise ValueError("PDF cannot be fully parsed") from error


class PDFPageAdapter:
    """Resolve and read captured PDF page Sources."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        source_id = _pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            source_id=source_id,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
            materialization=SourceMaterialization.CAPTURED,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose one PDF page as standard text evidence with page metadata."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDFPageAdapter(),
    projections=(PDFPageTextEvidenceProjection(),),
)


_EVIDENCE_STOPWORDS = frozenset(
    """
    a an and are as at be been being by did do does for from had has have how in into is it its
    of on or per the their them then there these they this those to was were what when where which
    who why will with would year years fy fiscal total company companies reported report
    """.split()
)


def _evidence_terms(text: str) -> list[str]:
    """Lowercase English word and number tokens used for offline evidence ranking."""

    return [term for term in re.findall(r"[a-z]+|\d+", text.casefold()) if term]


def _content_terms(text: str) -> list[str]:
    """Rank-eligible query terms: evidence tokens without generic question wording."""

    return [term for term in _evidence_terms(text) if term not in _EVIDENCE_STOPWORDS and len(term) > 1]


def _rank_entries(
    candidates: dict[tuple[str, str], tuple[Any, str]],
    question: str,
) -> list[tuple[str, str]]:
    """Rank candidate entries by weighted query coverage, then by aggregate term frequency."""

    query_terms = list(dict.fromkeys(_content_terms(question)))
    if not query_terms:
        query_terms = list(dict.fromkeys(_evidence_terms(question)))
    if not query_terms:
        return list(candidates)
    page_terms: dict[tuple[str, str], Counter[str]] = {}
    document_frequency = dict.fromkeys(query_terms, 0)
    for identity, (_citation, text) in candidates.items():
        frequencies = Counter(_evidence_terms(text))
        page_terms[identity] = frequencies
        for term in query_terms:
            if term in frequencies:
                document_frequency[term] += 1
    total = max(len(candidates), 1)
    weights = {
        term: math.log(1.0 + (total - count + 0.5) / (count + 0.5))
        for term, count in document_frequency.items()
    }
    scored: list[tuple[float, float, str, str]] = []
    for identity, frequencies in page_terms.items():
        coverage = sum(weights[term] for term in query_terms if term in frequencies)
        frequency = sum(min(frequencies.get(term, 0), 8) * weights[term] for term in query_terms)
        scored.append((coverage, frequency, identity[0], identity[1]))
    scored.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))
    return [(entry_id, version_id) for _coverage, _frequency, entry_id, version_id in scored]


async def search_pdf_pages(memory, question: str, limit: int = 10):
    """Search a scoped Memory for up to ``limit`` distinct evidence pages.

    One lexical search over a long natural-language question lets the admission floor drop
    pages that only share a single term, so evidence pages are easy to lose. This fans out
    the question into its own terms, unions the admitted candidates, and reranks the pool
    offline by weighted query coverage before resolving exact page citations.
    """

    from powercontext.builtin.artifacts.memory.models import MemoryCitation
    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    if limit < 1:
        return ()
    per_query_limit = max(limit * 20, 200)
    queries: list[str] = [question]
    queries.extend(list(dict.fromkeys(_content_terms(question)))[:10])
    candidates: dict[tuple[str, str], tuple[Any, str]] = {}
    for query in queries:
        page = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=per_query_limit))
        for hit in page.hits:
            identity = (hit.entry_id, hit.entry_version_id)
            if identity in candidates:
                continue
            candidates[identity] = (
                MemoryCitation(
                    memory_ref=hit.memory_ref,
                    entry_id=hit.entry_id,
                    entry_version_id=hit.entry_version_id,
                ),
                hit.text,
            )
    if not candidates:
        return ()
    ranked = _rank_entries(candidates, question)
    citations: list[Any] = []
    seen_pages: set[tuple[str, int]] = set()
    inspected = 0
    inspection_limit = max(limit * 8, 80)
    for identity in ranked:
        if len(citations) >= limit or inspected >= inspection_limit:
            break
        citation, _text = candidates[identity]
        inspected += 1
        entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        page_key: tuple[str, int] | None = None
        for reference in entry.sources:
            if reference.source_type == PDF_PAGE_SOURCE_NAME:
                page_key = _pdf_page_key(reference.source_id)
                if page_key is not None:
                    break
        if page_key is None or page_key in seen_pages:
            continue
        seen_pages.add(page_key)
        citations.append(citation)
    return tuple(citations)


__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "parse_pdf_document",
    "search_pdf_pages",
]
