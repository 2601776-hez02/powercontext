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

# FinanceBench prompts append analysis instructions to the question. Those words match many
# pages and would dominate a lexical score, so they never rank evidence pages.
_QUERY_NOISE = frozenset(
    """
    answer answers approximately assume assuming based calculate compute consider details following
    give given investment analyst note only perspective please provide question rely relying respond
    response roughly shown state upon use using within
    """.split()
)

# A question that asks for a figure is answered by a primary financial statement far more often
# than by narrative text, so statement pages get a prior only for those questions.
_METRIC_TERMS = frozenset(
    """
    amount asset assets billion capital cash cost costs debt earnings equity expense expenses growth
    income liability liabilities liquidity million margin percent percentage profit profits quick
    ratio revenues revenue share shares spending thousand value
    """.split()
)
_STATEMENT_HEADERS = (
    "consolidated statement of income",
    "consolidated statements of income",
    "consolidated statement of operations",
    "consolidated statements of operations",
    "consolidated statement of earnings",
    "consolidated statements of earnings",
    "consolidated statement of cash flows",
    "consolidated statements of cash flows",
    "consolidated balance sheet",
    "consolidated balance sheets",
)
_STATEMENT_WEIGHT = 0.6
_NUMERIC_DENSITY_WEIGHT = 0.6
_HEADING_WEIGHT = 0.5
_SECTION_HINT_WEIGHT = 0.9
_SYNONYM_WEIGHT = 0.25

# Financial-report questions paraphrase the page they cite ("legal battles" for a Legal
# Proceedings note, "geographies" for a segment and region listing). A short, generic
# expansion keeps the lexical ranking from missing the section that actually answers them.
_QUERY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "geographies": ("geographic", "region", "segment", "markets"),
    "geography": ("geographic", "region", "segment", "markets"),
    "battles": ("proceedings", "litigation", "legal", "claims"),
    "legal": ("litigation", "proceedings", "claims"),
    "outlined": ("recorded", "reported", "disclosed"),
    "quantity": ("amount", "total"),
}

# A question about a figure is answered by one of the primary statements, and the running
# header of every 10-K page names its section. Match the question type against the header
# vocabulary to boost exactly the pages of that section.
_SECTION_HINTS: tuple[tuple[frozenset[str], tuple[str, ...]], ...] = (
    (
        frozenset(
            {
                "margin",
                "margins",
                "income",
                "profit",
                "profits",
                "earnings",
                "revenue",
                "revenues",
                "expense",
                "expenses",
                "cost",
                "costs",
                "sales",
            }
        ),
        (
            "statement of income",
            "statements of income",
            "statement of operations",
            "statements of operations",
            "statement of earnings",
            "statements of earnings",
            "results of operations",
            "discussion and analysis",
        ),
    ),
    (
        frozenset(
            {
                "ratio",
                "ratios",
                "liquidity",
                "liability",
                "liabilities",
                "asset",
                "assets",
                "debt",
                "balance",
            }
        ),
        ("balance sheet", "balance sheets"),
    ),
    (
        frozenset({"cash", "flow", "flows", "capex", "depreciation", "amortization"}),
        ("statement of cash flows", "statements of cash flows"),
    ),
    (
        frozenset(
            {
                "geograph",
                "geographies",
                "geography",
                "region",
                "regions",
                "segment",
                "segments",
                "market",
                "markets",
                "category",
                "categories",
            }
        ),
        ("segment", "geographic", "region", "market"),
    ),
    (
        frozenset({"legal", "litigation", "proceeding", "proceedings", "lawsuit", "battles"}),
        ("legal", "proceeding", "litigation"),
    ),
    (
        frozenset({"restructuring", "restructure", "impairment"}),
        ("restructuring", "impairment"),
    ),
    (
        frozenset({"customer", "customers", "concentration"}),
        ("customer", "concentration"),
    ),    (
        frozenset({"drove", "drive", "driven", "discussion"}),
        ("discussion and analysis", "results of operations"),
    ),
    (
        frozenset({"product", "products", "service", "services"}),
        ("item 1", "business"),
    ),
)

_NARRATIVE_HINTS = frozenset(
    {"customer", "customers", "product", "products", "service", "services", "geographies", "geography"}
)


def _evidence_terms(text: str) -> list[str]:
    """Lowercase English word and number tokens used for offline evidence ranking."""

    return [term for term in re.findall(r"[a-z]+|\d+", text.casefold()) if term]


def _content_terms(text: str) -> list[str]:
    """Rank-eligible query terms: evidence tokens without question wording or prompt noise.

    Fiscal-year shorthands are normalized to the calendar year printed on the statements
    ("FY22" -> "2022") so a question can still match the figure pages.
    """

    raw = _evidence_terms(text)
    selected: list[str] = []
    index = 0
    while index < len(raw):
        term = raw[index]
        if term == "fy" and index + 1 < len(raw) and re.fullmatch(r"\d{2,4}", raw[index + 1]):
            year = raw[index + 1]
            selected.append(f"20{year}" if len(year) == 2 else year)
            index += 2
            continue
        if term not in _EVIDENCE_STOPWORDS and term not in _QUERY_NOISE and len(term) > 1:
            selected.append(term)
        index += 1
    return selected


def _lookup_terms(text: str) -> list[str]:
    """Candidate-generation terms: every distinctive token, prompt noise included.

    Ranking drops that noise, but candidate generation must stay broad so a page that only
    shares one incidental word is still available to the reranker. Fiscal-year shorthands are
    expanded to the calendar year actually printed on the statements ("FY22" -> "2022").
    """

    raw = _evidence_terms(text)
    selected: list[str] = []
    index = 0
    while index < len(raw):
        term = raw[index]
        if term == "fy" and index + 1 < len(raw) and re.fullmatch(r"\d{2,4}", raw[index + 1]):
            year = raw[index + 1]
            selected.append(f"20{year}" if len(year) == 2 else year)
            index += 2
            continue
        if term not in _EVIDENCE_STOPWORDS and len(term) > 1:
            selected.append(term)
        index += 1
    return selected


def _rank_entries(
    candidates: dict[tuple[str, str], tuple[Any, str]],
    question: str,
) -> list[tuple[str, str]]:
    """Rank candidate pages by weighted query coverage plus report-structure priors.

    Coverage is normalized by the best candidate. A page dense in numbers is preferred once
    coverage is comparable, a primary financial statement gets an extra prior when the
    question asks for a figure, and a page whose running header names the section the question
    is about (balance sheets, legal proceedings, geographic segments) is boosted directly.
    """

    query_terms = list(dict.fromkeys(_content_terms(question)))
    if not query_terms:
        query_terms = list(dict.fromkeys(_evidence_terms(question)))
    if not query_terms:
        return list(candidates)
    expanded: list[tuple[str, float]] = []
    for term in query_terms:
        expanded.append((term, 1.0))
        expanded.extend((synonym, _SYNONYM_WEIGHT) for synonym in _QUERY_SYNONYMS.get(term, ()))
    statement_prior = _STATEMENT_WEIGHT if any(term in _METRIC_TERMS for term in query_terms) else 0.0
    wanted_sections = tuple(
        keyword
        for hints, keywords in _SECTION_HINTS
        if any(term in hints for term in query_terms)
        for keyword in keywords
    )
    # A customer or product question is answered by narrative business or risk-factor text,
    # not by the numeric tables, so those questions prefer narrative pages over dense tables.
    narrative_question = any(
        term in _NARRATIVE_HINTS for term in query_terms
    )
    page_terms: dict[tuple[str, str], Counter[str]] = {}
    numeric_density: dict[tuple[str, str], float] = {}
    statement_page: dict[tuple[str, str], float] = {}
    heading_match: dict[tuple[str, str], float] = {}
    section_match: dict[tuple[str, str], float] = {}
    for identity, (_citation, text) in candidates.items():
        terms = _evidence_terms(text)
        page_terms[identity] = Counter(terms)
        numeric_density[identity] = (
            sum(1 for term in terms if term[0].isdigit()) / len(terms) if terms else 0.0
        )
        folded = text.casefold()
        statement_page[identity] = (
            1.0 if statement_prior and any(marker in folded for marker in _STATEMENT_HEADERS) else 0.0
        )
        # A page heading names its section ("GEOGRAPHIC OPERATIONS", "NOTE 24 ..."), so a query
        # term there is much stronger evidence than the same term deep in the body text.
        heading = set(_evidence_terms(text[:240]))
        heading_match[identity] = (
            sum(1 for term in query_terms if term in heading) / len(query_terms) if query_terms else 0.0
        )
        heading_text = text[:240].casefold()
        section_match[identity] = (
            1.0 if wanted_sections and any(keyword in heading_text for keyword in wanted_sections) else 0.0
        )
    total = max(len(candidates), 1)
    weights: dict[str, float] = {}
    for term in {term for term, _weight in expanded}:
        document_frequency = sum(1 for counter in page_terms.values() if term in counter)
        weights[term] = math.log(1.0 + (total - document_frequency + 0.5) / (document_frequency + 0.5))
    coverage = {
        identity: sum(weights[term] * weight for term, weight in expanded if counter[term])
        for identity, counter in page_terms.items()
    }
    maximum = max(coverage.values(), default=1.0) or 1.0
    numeric_weight = -_NUMERIC_DENSITY_WEIGHT if narrative_question else _NUMERIC_DENSITY_WEIGHT
    ranked = sorted(
        (
            (
                coverage[identity] / maximum
                + numeric_weight * numeric_density[identity]
                + statement_prior * statement_page[identity]
                + _HEADING_WEIGHT * heading_match[identity]
                + _SECTION_HINT_WEIGHT * section_match[identity],
                identity[0],
                identity[1],
            )
            for identity in coverage
        ),
        key=lambda row: (-row[0], row[1], row[2]),
    )
    return [(entry_id, version_id) for _score, entry_id, version_id in ranked]


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
    queries.extend(list(dict.fromkeys(_lookup_terms(question)))[:14])
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
