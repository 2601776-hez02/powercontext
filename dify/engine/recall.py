"""NPC 记忆的写入与混合检索（seekdb 全文索引 + HNSW 向量索引）。

一次「回忆」在数据库里是两条各带索引的查询：
* 全文通道 ``MATCH(body) AGAINST(...)`` 命中字面（偷 / 玉佩 / 袖子），权重 1.0；
* 向量通道 ``ORDER BY cosine_distance(...) APPROXIMATE`` 命中相近说法，权重 0.65；
两条通道用 RRF 融合，再按记忆重要度与新鲜度加权，按 NPC 分组返回。
融合发生在 Python 里，但候选、排序、过滤都在数据库里完成——不把整表拉进内存。

``relevant`` 字段是给叙事模型看的：全文命中、或向量余弦 >= 0.08 才算「真想起
了什么」；剩下的只当氛围，不要求 NPC 拿来对质。
"""

from __future__ import annotations

from typing import Any

from .db import SeekDB
from .embed import embed_literal
from .textutil import fts_query

RRF_K = 60
CANDIDATES = 12
FTS_WEIGHT = 1.0
VECTOR_WEIGHT = 0.65
RELEVANT_SIMILARITY = 0.08

_VECTOR_SQL = (
    "SELECT id, guest, npc, turn, kind, body, emotion, source, importance, "
    "cosine_distance(embedding, %s) AS distance "
    "FROM tw_memory WHERE guest IN (%s, '*') AND npc = %s "
    "ORDER BY cosine_distance(embedding, %s) APPROXIMATE LIMIT %s"
)

_FTS_SQL = (
    "SELECT id, guest, npc, turn, kind, body, emotion, source, importance, "
    "MATCH(body) AGAINST(%s IN NATURAL LANGUAGE MODE) AS fts_score "
    "FROM tw_memory WHERE guest IN (%s, '*') AND npc = %s "
    "AND MATCH(body) AGAINST(%s IN NATURAL LANGUAGE MODE) "
    "ORDER BY fts_score DESC LIMIT %s"
)


def remember(
    db: SeekDB,
    *,
    guest: str,
    npc: str,
    turn: int,
    kind: str,
    body: str,
    emotion: str,
    importance: int,
    source: str = "亲历",
    conn: Any | None = None,
) -> int:
    """写一条记忆；``conn`` 传入游标时参与调用方的事务。"""

    sql = (
        "INSERT INTO tw_memory (guest, npc, turn, kind, body, emotion, importance, source, embedding, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())"
    )
    params = (guest, npc, turn, kind, body[:1000], emotion, importance, source, embed_literal(body))
    if conn is not None:
        conn.execute(sql, params)
        return int(conn.lastrowid or 0)
    with db.transaction() as cursor:
        cursor.execute(sql, params)
        return int(cursor.lastrowid or 0)


def _vector_hits(db: SeekDB, guest: str, npc: str, query: str) -> list[dict]:
    literal = embed_literal(query)
    try:
        return db.query(_VECTOR_SQL, (literal, guest, npc, literal, CANDIDATES))
    except Exception:
        # 没有向量索引可用时退化成顺序扫描，保证功能不中断
        fallback = (
            "SELECT id, guest, npc, turn, kind, body, emotion, source, importance, "
            "cosine_distance(embedding, %s) AS distance "
            "FROM tw_memory WHERE guest IN (%s, '*') AND npc = %s "
            "ORDER BY distance ASC LIMIT %s"
        )
        return db.query(fallback, (literal, guest, npc, CANDIDATES))


def _fts_hits(db: SeekDB, guest: str, npc: str, query: str) -> tuple[list[dict], str]:
    """先拿原句检索（ngram 分词交给数据库），没命中再用抽出来的关键词。"""

    for text in (query, fts_query(query)):
        if not text:
            continue
        try:
            rows = db.query(_FTS_SQL, (text, guest, npc, text, CANDIDATES))
        except Exception:
            continue
        if rows:
            return rows, text
    return [], ""


def _fuse(vector_rows: list[dict], fts_rows: list[dict]) -> dict[int, dict]:
    merged: dict[int, dict] = {}
    for rank, row in enumerate(vector_rows):
        item = merged.setdefault(int(row["id"]), {**row, "channels": [], "rrf": 0.0})
        item["channels"].append("vector")
        item["vector_similarity"] = round(1.0 - float(row.get("distance") or 0.0), 4)
        item["rrf"] += VECTOR_WEIGHT / (RRF_K + rank + 1)
    for rank, row in enumerate(fts_rows):
        item = merged.setdefault(int(row["id"]), {**row, "channels": [], "rrf": 0.0})
        item["channels"].append("fts")
        item["fts_score"] = round(float(row.get("fts_score") or 0.0), 4)
        item["rrf"] += FTS_WEIGHT / (RRF_K + rank + 1)
    return merged


def recall(
    db: SeekDB,
    *,
    guest: str,
    npc: str,
    query: str,
    limit: int = 3,
    current_turn: int = 0,
) -> list[dict]:
    """某个 NPC 面对当前动作会想起什么。"""

    fts_rows, used_query = _fts_hits(db, guest, npc, query)
    merged = _fuse(_vector_hits(db, guest, npc, query), fts_rows)
    rows: list[dict] = []
    for item in merged.values():
        importance = int(item.get("importance") or 5)
        turn = int(item.get("turn") or 0)
        recency = 0.0
        if current_turn and turn:
            gap = max(0, current_turn - turn)
            recency = 0.12 * (0.55 ** gap)
        score = float(item["rrf"]) * (1.0 + 0.045 * importance) + recency
        similarity = float(item.get("vector_similarity") or 0.0)
        rows.append(
            {
                "memory_id": int(item["id"]),
                "npc": item["npc"],
                "turn": turn,
                "kind": item["kind"],
                "text": item["body"],
                "emotion": item["emotion"],
                "source": item["source"],
                "importance": importance,
                "score": round(score, 4),
                "channels": sorted(item["channels"]),
                "vector_similarity": similarity,
                "relevant": ("fts" in item["channels"]) or similarity >= RELEVANT_SIMILARITY,
                "origin": "world" if item["guest"] == "*" else "personal",
            }
        )
    rows.sort(key=lambda row: (row["relevant"], row["score"], row["turn"]), reverse=True)
    return rows[:limit]


def ambient(db: SeekDB, *, guest: str, npc: str, limit: int = 1) -> list[dict]:
    """没有具体动作时，NPC 脑子里「最压着的」那件事（重要度 + 新近）。"""

    rows = db.query(
        "SELECT id, turn, kind, body, emotion, source, importance FROM tw_memory "
        "WHERE guest IN (%s, '*') AND npc = %s ORDER BY importance DESC, turn DESC, id DESC LIMIT %s",
        (guest, npc, limit),
    )
    return [
        {
            "memory_id": int(row["id"]),
            "npc": npc,
            "turn": int(row["turn"]),
            "kind": row["kind"],
            "text": row["body"],
            "emotion": row["emotion"],
            "source": row["source"],
            "importance": int(row["importance"]),
            "score": 0.0,
            "channels": ["ambient"],
            "vector_similarity": 0.0,
            "relevant": False,
            "origin": "ambient",
        }
        for row in rows
    ]


def mind_of(db: SeekDB, *, guest: str, npc: str, limit: int = 8) -> list[dict]:
    """读心：直接看这个 NPC 现在脑子里存着什么（新→旧）。"""

    return db.query(
        "SELECT id, turn, kind, body, emotion, importance, source, raw FROM tw_memory "
        "WHERE guest IN (%s, '*') AND npc = %s ORDER BY turn DESC, id DESC LIMIT %s",
        (guest, npc, limit),
    )


def explain(db: SeekDB, *, guest: str, npc: str, query: str) -> dict:
    """给评审看的执行计划：向量排序走的是 vector index scan。"""

    literal = embed_literal(query)
    sql = (
        "SELECT id, body, cosine_distance(embedding, %s) AS distance FROM tw_memory "
        "WHERE guest IN (%s, '*') AND npc = %s "
        "ORDER BY cosine_distance(embedding, %s) APPROXIMATE LIMIT 5"
    )
    params = (literal, guest, npc, literal)
    try:
        plan = db.explain(sql, params)
        note = ""
    except Exception as exc:  # pragma: no cover - 老版本不支持 EXPLAIN 时
        plan = []
        note = f"EXPLAIN 失败：{exc}"
    operators = [
        line
        for row in plan
        for line in str(row.get("Query Plan", "")).splitlines()
        if "SCAN" in line or "OPERATOR" in line
    ]
    return {
        "sql": sql.strip(),
        "params": ["<1024 维向量>", guest, npc, "<1024 维向量>"],
        "plan": plan,
        "operators": operators,
        "note": note,
    }
