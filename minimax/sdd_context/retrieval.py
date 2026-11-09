"""Select the materials a task needs from the seekdb index.

Retrieval has three signals, all backed by SQL:

1. lexical relevance from seekdb's FULLTEXT index over ``sdd_items.searchable``;
2. exact identifier/path hits, so a symbol named in the task is never missed;
3. test linkage: a test file whose imports mention a selected code module is a
   verification candidate even when it scores poorly lexically.

The module re-ranks the union in Python with a small, explainable scoring
function and keeps the reason for every decision, because the context pack has
to justify why each item is there.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .chunking import tokens
from .seekdb_store import SeekDBStore

KIND_PRIOR = {"spec": 1.0, "code": 0.97, "test": 0.93}


@dataclass(frozen=True)
class Task:
    task_id: str
    title: str
    description: str
    keywords: tuple[str, ...]
    acceptance_tests: tuple[str, ...]
    regression_tests: tuple[str, ...]
    must_include: dict[str, tuple[str, ...]]
    keep_behavior: tuple[str, ...]
    source_path: str = ""

    @property
    def query_terms(self) -> list[str]:
        terms: list[str] = []
        for value in (*self.keywords, self.title, self.description):
            terms.extend(tokens(value))
        seen: dict[str, None] = {}
        for term in terms:
            seen.setdefault(term, None)
        return list(seen)

    @property
    def weighted_terms(self) -> list[str]:
        """Keywords first: they are the terms the task owner considers essential."""
        out: list[str] = []
        for keyword in self.keywords:
            for token in tokens(keyword):
                if token not in out:
                    out.append(token)
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "keywords": list(self.keywords),
            "acceptance_tests": list(self.acceptance_tests),
            "regression_tests": list(self.regression_tests),
            "must_include": {key: list(value) for key, value in self.must_include.items()},
            "keep_behavior": list(self.keep_behavior),
            "source": self.source_path,
        }


def load_task(path: Path) -> Task:
    raw = json.loads(path.read_text(encoding="utf-8"))
    must = raw.get("must_include", {})
    return Task(
        task_id=str(raw["task_id"]),
        title=str(raw.get("title", "")),
        description=str(raw.get("description", "")),
        keywords=tuple(raw.get("keywords", ())),
        acceptance_tests=tuple(raw.get("acceptance_tests", ())),
        regression_tests=tuple(raw.get("regression_tests", ())),
        must_include={key: tuple(value) for key, value in must.items()},
        keep_behavior=tuple(raw.get("keep_behavior", ())),
        source_path=str(path),
    )


@dataclass
class ScoredItem:
    hit: dict[str, Any]
    score: float
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        data = {key: value for key, value in self.hit.items() if key != "text"}
        data["text"] = self.hit["text"]
        data["score"] = round(self.score, 4)
        data["reasons"] = self.reasons
        return data


@dataclass
class Selection:
    task: Task
    specs: list[ScoredItem]
    code: list[ScoredItem]
    tests: list[ScoredItem]
    terms: list[str]
    candidate_count: int
    queries: list[dict[str, Any]]

    def all_items(self) -> Iterable[ScoredItem]:
        yield from self.specs
        yield from self.code
        yield from self.tests

    def refs(self) -> dict[str, list[str]]:
        return {
            "specs": [item.hit["item_ref"] for item in self.specs],
            "code": [item.hit["item_ref"] for item in self.code],
            "tests": [item.hit["item_ref"] for item in self.tests],
        }

    def files(self, family: str) -> list[str]:
        items = {"specs": self.specs, "code": self.code, "tests": self.tests}[family]
        return sorted({item.hit["path"] for item in items})


def _module_of(path: str) -> str | None:
    """Dotted module name for an importable source file, else ``None``."""
    if not path.endswith(".py"):
        return None
    parts = path.split("/")
    if "src" in parts:
        parts = parts[parts.index("src") + 1 :]
    elif parts[0] == "tests":
        return None
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][: -len(".py")]
    return ".".join(part for part in parts if part)


def _coverage(terms: Sequence[str], haystack: str) -> float:
    if not terms:
        return 0.0
    lowered = haystack.lower()
    hits = sum(1 for term in terms if term in lowered)
    return hits / len(terms)


def _score(hit: dict[str, Any], terms: Sequence[str], keyword_terms: Sequence[str], max_fts: float) -> ScoredItem:
    reasons = list(hit.get("reasons", []))
    score = 0.0
    raw_score = float(hit.get("score") or 0.0)
    if raw_score > 0 and max_fts > 0:
        score += 0.35 * (raw_score / max_fts)
        reasons.append("fulltext")
    if hit.get("exact"):
        score += 0.35
        reasons.append("exact symbol/path match")
    if hit.get("linked"):
        score += 0.40
        reasons.append("test imports a selected symbol")
    identifier_text = " ".join(
        [hit.get("symbol", ""), hit.get("title", ""), hit.get("item_ref", ""), hit.get("path", "")]
    )
    text = hit.get("text", "")
    identifier_coverage = _coverage(keyword_terms, identifier_text)
    if identifier_coverage:
        score += 0.20 * identifier_coverage
        reasons.append(f"identifier coverage {identifier_coverage:.2f}")
    keyword_coverage = _coverage(keyword_terms, f"{identifier_text} {text}")
    if keyword_coverage:
        score += 0.20 * keyword_coverage
        reasons.append(f"keyword coverage {keyword_coverage:.2f}")
    body_coverage = _coverage(terms, text)
    if body_coverage:
        score += 0.10 * body_coverage
        reasons.append(f"text coverage {body_coverage:.2f}")
    score *= KIND_PRIOR.get(hit.get("kind", ""), 0.9)
    if str(hit.get("item_ref", "")).endswith("#module"):
        # The whole-file item is a fallback: it duplicates every symbol below it.
        score *= 0.45
        reasons.append("whole-file fallback")
    return ScoredItem(hit=hit, score=score, reasons=list(dict.fromkeys(reasons)))


def select_materials(
    store: SeekDBStore,
    task: Task,
    *,
    spec_limit: int = 4,
    code_limit: int = 5,
    test_limit: int = 4,
) -> Selection:
    terms = task.query_terms
    keyword_terms = task.weighted_terms or terms
    candidate_hits: dict[str, dict[str, Any]] = {}

    def absorb(hits: Sequence[dict[str, Any]]) -> None:
        for hit in hits:
            existing = candidate_hits.get(hit["item_id"])
            if existing is None:
                candidate_hits[hit["item_id"]] = dict(hit)
                continue
            merged = list(dict.fromkeys(existing.get("reasons", []) + hit.get("reasons", [])))
            existing["reasons"] = merged
            existing["exact"] = existing.get("exact") or hit.get("exact")
            existing["linked"] = existing.get("linked") or hit.get("linked")
            existing["score"] = max(float(existing.get("score") or 0.0), float(hit.get("score") or 0.0))

    absorb(store.fulltext_search(terms[:24], limit=120))
    absorb(store.exact_search(keyword_terms, limit=40))

    max_fts = max((float(hit.get("score") or 0.0) for hit in candidate_hits.values()), default=0.0)
    scored = [_score(hit, terms, keyword_terms, max_fts) for hit in candidate_hits.values()]

    specs = _take(scored, "spec", spec_limit, per_file=2)
    code = _take(scored, "code", code_limit, per_file=2)

    modules = sorted({module for item in code if (module := _module_of(item.hit["path"]))})
    symbols = sorted(
        {
            item.hit["symbol"].split(".")[-1]
            for item in code
            if not str(item.hit["item_ref"]).endswith("#module") and item.hit["symbol"]
        }
    )
    if modules or symbols:
        absorb(store.tests_importing([*modules, *symbols], limit=30))
        max_fts = max((float(hit.get("score") or 0.0) for hit in candidate_hits.values()), default=0.0)
        scored = [_score(hit, terms, keyword_terms, max_fts) for hit in candidate_hits.values()]
        code = _take(scored, "code", code_limit, per_file=2)

    tests = _select_tests(scored, task, test_limit)

    return Selection(
        task=task,
        specs=specs,
        code=code,
        tests=tests,
        terms=terms,
        candidate_count=len(candidate_hits),
        queries=[trace.as_dict() for trace in store.traces],
    )


def _take(scored: Sequence[ScoredItem], kind: str, limit: int, *, per_file: int = 9) -> list[ScoredItem]:
    ordered = sorted(
        (item for item in scored if item.hit["kind"] == kind),
        key=lambda item: (-item.score, item.hit["item_ref"]),
    )
    chosen: list[ScoredItem] = []
    per_path: dict[str, int] = {}
    for item in ordered:
        if item.score <= 0:
            continue
        path = item.hit["path"]
        if per_path.get(path, 0) >= per_file:
            continue
        per_path[path] = per_path.get(path, 0) + 1
        chosen.append(item)
        if len(chosen) >= limit:
            break
    return chosen


def _select_tests(scored: Sequence[ScoredItem], task: Task, limit: int) -> list[ScoredItem]:
    """Prefer the task's acceptance tests, then tests linked to selected code."""
    by_path: dict[str, list[ScoredItem]] = {}
    for item in scored:
        if item.hit["kind"] != "test":
            continue
        by_path.setdefault(item.hit["path"], []).append(item)

    chosen: list[ScoredItem] = []
    used: set[str] = set()

    def best_per_file(path: str) -> list[ScoredItem]:
        items = sorted(by_path.get(path, []), key=lambda item: (-item.score, item.hit["item_ref"]))
        return items[:3]

    for path in task.acceptance_tests:
        for item in best_per_file(path):
            if item.hit["item_id"] in used:
                continue
            item.reasons.append("declared acceptance test for this task")
            chosen.append(item)
            used.add(item.hit["item_id"])

    linked = sorted(
        (item for item in scored if item.hit["kind"] == "test" and item.hit.get("linked")),
        key=lambda item: (-item.score, item.hit["item_ref"]),
    )
    for item in linked:
        if len(chosen) >= limit:
            break
        if item.hit["item_id"] in used:
            continue
        chosen.append(item)
        used.add(item.hit["item_id"])

    everything = [item for items in by_path.values() for item in items]
    for item in sorted(everything, key=lambda item: (-item.score, item.hit["item_ref"])):
        if len(chosen) >= limit:
            break
        if item.hit["item_id"] in used:
            continue
        chosen.append(item)
        used.add(item.hit["item_id"])
    return chosen


def certify(selection: Selection) -> dict[str, Any]:
    """Check the pack against the task's declared required sources."""
    required = selection.task.must_include
    available = {
        "specs": {item.hit["item_ref"] for item in selection.specs} | {item.hit["path"] for item in selection.specs},
        "code": {item.hit["item_ref"] for item in selection.code} | {item.hit["path"] for item in selection.code},
        "tests": {item.hit["item_ref"] for item in selection.tests} | {item.hit["path"] for item in selection.tests},
    }
    missing: dict[str, list[str]] = {}
    for family, refs in required.items():
        absent = [ref for ref in refs if ref not in available.get(family, set())]
        if absent:
            missing[family] = absent
    return {"ok": not missing, "missing": missing,
            "required": {key: list(value) for key, value in required.items()}}
