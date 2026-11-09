"""混合检索：ngram 全文 + HNSW 向量，RRF 融合，逐条回查出处。"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from .embedding import embed_literal
from .store import Store
from .text import ascii_words, cjk_bigrams, make_snippet, query_terms

RRF_K = 60
VECTOR_SIM_FLOOR = 0.12
_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+\-.#]*")


@dataclass
class Hit:
    chunk_id: int
    document_id: int
    document_path: str
    document_title: str
    heading: str
    text: str
    snippet: str
    char_start: int
    char_end: int
    start_line: int
    end_line: int
    fts_score: float | None
    vector_similarity: float | None
    score: float
    channels: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["channels"] = list(self.channels)
        return payload


@dataclass
class SearchMeta:
    query: str
    expanded_query: str
    aliases: tuple[str, ...]
    fts_hits: int
    vector_hits: int
    latency_ms: float


def load_aliases(store: Store) -> dict[str, tuple[str, ...]]:
    rows = store.fetch_all("SELECT term, aliases FROM zx_aliases")
    aliases: dict[str, tuple[str, ...]] = {}
    for row in rows:
        parts = [part.strip() for part in str(row["aliases"]).split(",") if part.strip()]
        aliases[str(row["term"])] = tuple(parts)
    return aliases


def expand_query(query: str, aliases: dict[str, tuple[str, ...]]) -> tuple[str, tuple[str, ...]]:
    """按术语表补充同义词，提升中文改写与中英混写的召回。"""

    lowered = query.lower()
    extras: list[str] = []
    used: list[str] = []
    for term, values in aliases.items():
        term_lower = term.lower()
        present = term_lower in lowered or any(value.lower() in lowered for value in values if value)
        if not present:
            continue
        used.append(term)
        for candidate in (term, *values):
            if candidate and candidate.lower() not in lowered and candidate not in extras:
                extras.append(candidate)
    expanded = query if not extras else query + " " + " ".join(extras)
    return expanded, tuple(used)


def _base_columns() -> str:
    return (
        "c.id AS chunk_id, c.document_id, c.heading, c.body, c.char_start, c.char_end, "
        "c.start_line, c.end_line, d.path AS document_path, d.title AS document_title"
    )


def fts_search(store: Store, query: str, limit: int) -> list[dict[str, Any]]:
    sql = (
        f"SELECT {_base_columns()}, "
        "MATCH(c.body) AGAINST(%s IN NATURAL LANGUAGE MODE) AS fts_score "
        "FROM zx_chunks AS c JOIN zx_documents AS d ON d.id = c.document_id "
        "WHERE MATCH(c.body) AGAINST(%s IN NATURAL LANGUAGE MODE) "
        "ORDER BY fts_score DESC LIMIT %s"
    )
    try:
        return store.fetch_all(sql, (query, query, limit))
    except Exception:
        # 极短查询（单个字符/纯符号）可能被分词器丢弃，退化为子串匹配
        like = "%" + query.replace("%", r"\%").replace("_", r"\_") + "%"
        fallback = (
            f"SELECT {_base_columns()}, 1.0 AS fts_score "
            "FROM zx_chunks AS c JOIN zx_documents AS d ON d.id = c.document_id "
            "WHERE c.body LIKE %s ORDER BY c.id LIMIT %s"
        )
        return store.fetch_all(fallback, (like, limit))


def vector_search(store: Store, query: str, limit: int) -> list[dict[str, Any]]:
    literal = embed_literal(query)
    sql = (
        f"SELECT {_base_columns()}, "
        "cosine_distance(c.embedding, %s) AS distance "
        "FROM zx_chunks AS c JOIN zx_documents AS d ON d.id = c.document_id "
        "ORDER BY cosine_distance(c.embedding, %s) APPROXIMATE LIMIT %s"
    )
    try:
        rows = store.fetch_all(sql, (literal, literal, limit))
    except Exception:
        plain = (
            f"SELECT {_base_columns()}, cosine_distance(c.embedding, %s) AS distance "
            "FROM zx_chunks AS c JOIN zx_documents AS d ON d.id = c.document_id "
            "ORDER BY distance ASC LIMIT %s"
        )
        rows = store.fetch_all(plain, (literal, limit))
    for row in rows:
        row["vector_similarity"] = 1.0 - float(row["distance"])
    return rows


def _row_to_hit(row: dict[str, Any], query: str, score: float, channels: tuple[str, ...]) -> Hit:
    body = str(row["body"])
    return Hit(
        chunk_id=int(row["chunk_id"]),
        document_id=int(row["document_id"]),
        document_path=str(row["document_path"]),
        document_title=str(row["document_title"]),
        heading=str(row["heading"]),
        text=body,
        snippet=make_snippet(body, query),
        char_start=int(row["char_start"]),
        char_end=int(row["char_end"]),
        start_line=int(row["start_line"]),
        end_line=int(row["end_line"]),
        fts_score=float(row["fts_score"]) if row.get("fts_score") is not None else None,
        vector_similarity=(
            float(row["vector_similarity"]) if row.get("vector_similarity") is not None else None
        ),
        score=score,
        channels=channels,
    )


def search(
    store: Store,
    query: str,
    *,
    limit: int = 6,
    candidate_limit: int = 40,
    log: bool = True,
) -> tuple[list[Hit], SearchMeta]:
    started = time.perf_counter()
    query = query.strip()
    if not query:
        return [], SearchMeta("", "", (), 0, 0, 0.0)

    aliases = load_aliases(store)
    expanded, used_aliases = expand_query(query, aliases)

    fts_rows = fts_search(store, expanded, candidate_limit)
    vector_rows = vector_search(store, expanded, candidate_limit)

    if vector_rows:
        best = max(float(row["vector_similarity"]) for row in vector_rows)
        floor = max(VECTOR_SIM_FLOOR, best * 0.55)
        vector_rows = [row for row in vector_rows if float(row["vector_similarity"]) >= floor]

    fused: dict[int, dict[str, Any]] = {}
    for rank, row in enumerate(fts_rows):
        entry = fused.setdefault(
            int(row["chunk_id"]),
            {"row": row, "score": 0.0, "channels": set()},
        )
        entry["score"] += 1.0 / (RRF_K + rank + 1)
        entry["channels"].add("fts")
    for rank, row in enumerate(vector_rows):
        chunk_id = int(row["chunk_id"])
        entry = fused.setdefault(chunk_id, {"row": row, "score": 0.0, "channels": set()})
        entry["score"] += 1.0 / (RRF_K + rank + 1)
        entry["channels"].add("vector")
        entry["row"].update({"vector_similarity": row["vector_similarity"]})

    ranked = sorted(fused.values(), key=lambda item: item["score"], reverse=True)[:limit]
    hits: list[Hit] = []
    for entry in ranked:
        row = entry["row"]
        if "fts_score" not in row:
            row["fts_score"] = None
        channels = tuple(sorted(entry["channels"]))
        hits.append(_row_to_hit(row, query, round(float(entry["score"]), 6), channels))

    latency_ms = (time.perf_counter() - started) * 1000.0
    meta = SearchMeta(
        query=query,
        expanded_query=expanded,
        aliases=used_aliases,
        fts_hits=len(fts_rows),
        vector_hits=len(vector_rows),
        latency_ms=round(latency_ms, 2),
    )
    if log:
        store.execute(
            "INSERT INTO zx_searches (query, hits, top_path, created_at) VALUES (%s, %s, %s, %s)",
            (
                query[:500],
                len(hits),
                hits[0].document_path if hits else "",
                datetime.now().replace(microsecond=0),
            ),
        )
    return hits, meta


def _context_item(hit: Hit, query: str, snippet_width: int) -> dict[str, Any]:
    return {
        "citation": {
            "document_id": hit.document_id,
            "path": hit.document_path,
            "title": hit.document_title,
            "heading": hit.heading,
            "chunk_id": hit.chunk_id,
            "char_start": hit.char_start,
            "char_end": hit.char_end,
            "lines": [hit.start_line, hit.end_line],
        },
        "content": make_snippet(hit.text, query, width=snippet_width),
        "score": hit.score,
        "channels": list(hit.channels),
    }


def ask(
    store: Store,
    query: str,
    *,
    budget_bytes: int = 1800,
    limit: int = 8,
    snippet_width: int = 240,
) -> dict[str, Any]:
    """按 UTF-8 字节预算拼装证据包，预算包含信封、JSON、引用与正文。"""

    hits, meta = search(store, query, limit=limit)
    header = 'BEGIN_ZHIXU_CONTEXT_V1 trust="untrusted_local_corpus"\n'
    footer = "\nEND_ZHIXU_CONTEXT_V1"

    def render(selected: list[Hit], width: int) -> str:
        payload = {
            "schema": "zhixu.context.v1",
            "query": query,
            "items": [_context_item(hit, query, width) for hit in selected],
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return header + body + footer

    selected: list[Hit] = []
    content = render(selected, snippet_width)
    for hit in hits:
        rendered = render([*selected, hit], snippet_width)
        if len(rendered.encode("utf-8")) > budget_bytes:
            break
        selected.append(hit)
        content = rendered

    if not selected and hits:
        # 预算极小时至少保留一条最短证据，并压缩它的正文
        for width in (160, 120, 80, 40, 16):
            rendered = render([hits[0]], width)
            if len(rendered.encode("utf-8")) <= budget_bytes:
                selected = [hits[0]]
                content = rendered
                snippet_width = width
                break

    return {
        "query": query,
        "content": content,
        "content_bytes": len(content.encode("utf-8")),
        "budget_bytes": budget_bytes,
        "items": [_context_item(hit, query, snippet_width) for hit in selected],
        "meta": meta,
    }



def render_evidence_pack(result: dict[str, Any]) -> str:
    """把证据包渲染成可直接贴进笔记/交给模型的 Markdown。"""

    lines = [f"# 证据包 · {result['query']}", ""]
    for index, item in enumerate(result["items"], start=1):
        citation = item["citation"]
        lines.append(
            f"**[{index}] {citation['title']} · {citation['heading']}** "
            f"（{citation['path']} 第 {citation['lines'][0]}-{citation['lines'][1]} 行，"
            f"字符 {citation['char_start']}-{citation['char_end']}）"
        )
        lines.append("")
        lines.append(item["content"])
        lines.append("")
    lines.append(
        f"> 证据 {len(result['items'])} 条，输出 {result['content_bytes']} 字节 / 预算 {result['budget_bytes']} 字节；"
        f"检索耗时 {result['meta'].latency_ms:.1f} ms（fts {result['meta'].fts_hits} 条，vector {result['meta'].vector_hits} 条）。"
    )
    return "\n".join(lines)


def explain_plan(store: Store, query: str, *, max_lines: int = 12) -> list[str]:
    """取向量检索的查询计划，用于在站点上展示索引确实被使用。"""

    literal = embed_literal(query)
    sql = (
        "EXPLAIN SELECT c.id, cosine_distance(c.embedding, %s) AS distance "
        "FROM zx_chunks AS c ORDER BY cosine_distance(c.embedding, %s) APPROXIMATE LIMIT 6"
    )
    try:
        rows = store.fetch_all(sql, (literal, literal))
    except Exception:
        return []
    lines: list[str] = []
    for row in rows:
        value = str(row.get("Query Plan") or "")
        for raw_line in value.splitlines():
            line = raw_line.rstrip()
            if not line.strip():
                continue
            if line.strip().startswith("Outputs & filters"):
                return lines
            if len(line) > 160:
                line = line[:157] + "…"
            lines.append(line)
            if len(lines) >= max_lines:
                return lines
    return lines


_INDEX_KINDS = {
    "PRIMARY": ("主键", "btree"),
    "ix_doc_seq": ("文档+序号", "btree"),
    "ft_body": ("全文索引（ngram 解析器）", "fulltext"),
    "vk_embedding": ("向量索引（HNSW / COSINE）", "vector"),
}


def index_inventory(store: Store) -> list[dict[str, Any]]:
    rows = store.fetch_all(
        "SELECT index_name, column_name FROM information_schema.statistics "
        "WHERE table_schema = DATABASE() AND table_name = 'zx_chunks' "
        "AND index_name IN ('PRIMARY', 'ix_doc_seq', 'ft_body', 'vk_embedding') "
        "ORDER BY index_name, seq_in_index"
    )
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row["index_name"])
        kind, engine = _INDEX_KINDS.get(name, ("索引", "btree"))
        entry = grouped.setdefault(
            name,
            {"index": name, "kind": kind, "engine": engine, "columns": []},
        )
        entry["columns"].append(str(row["column_name"]))
    order = {"PRIMARY": 0, "ix_doc_seq": 1, "ft_body": 2, "vk_embedding": 3}
    return sorted(grouped.values(), key=lambda item: order.get(item["index"], 9))


def keywords(query: str) -> list[str]:
    terms = list(ascii_words(query))
    terms.extend(cjk_bigrams(query))
    return terms


def terms(query: str) -> list[str]:
    return query_terms(query)
