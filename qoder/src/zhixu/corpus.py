"""资料入库：扫描 Markdown、按内容哈希幂等重建片段。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .embedding import embed_literal
from .store import Store
from .text import chunk_content, line_span, parse_document, sha256_text


@dataclass
class IngestStats:
    scanned: int = 0
    added: int = 0
    updated: int = 0
    skipped: int = 0
    chunks: int = 0
    documents: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "scanned": self.scanned,
            "added": self.added,
            "updated": self.updated,
            "skipped": self.skipped,
            "chunks": self.chunks,
            "documents": self.documents,
        }


def iter_markdown(corpus_dir: Path) -> list[Path]:
    if not corpus_dir.exists():
        return []
    return sorted(path for path in corpus_dir.rglob("*.md") if path.is_file())


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def ingest(store: Store, corpus_dir: Path, *, rebuild: bool = False) -> IngestStats:
    """把语料同步进 seekdb；内容未变则跳过，改动则原子替换该文档的片段。"""

    stats = IngestStats()
    now = datetime.now().replace(microsecond=0)
    for path in iter_markdown(corpus_dir):
        stats.scanned += 1
        raw = _read_text(path)
        document = parse_document(raw)
        digest = sha256_text(raw)
        relative = path.relative_to(corpus_dir).as_posix()

        existing = store.fetch_one(
            "SELECT id, content_sha256 FROM zx_documents WHERE path = %s", (relative,)
        )
        if existing and existing["content_sha256"] == digest and not rebuild:
            stats.skipped += 1
            continue

        chunks = chunk_content(raw, body_start=document.body_start)
        rows: list[tuple[object, ...]] = []
        for seq, chunk in enumerate(chunks):
            start_line, end_line = line_span(raw, chunk.start, chunk.end)
            rows.append(
                (
                    seq,
                    chunk.heading,
                    chunk.text,
                    chunk.start,
                    chunk.end,
                    start_line,
                    end_line,
                    embed_literal(chunk.heading + "\n" + chunk.text),
                )
            )

        with store.transaction() as cursor:
            if existing:
                document_id = int(existing["id"])
                cursor.execute(
                    "UPDATE zx_documents SET title=%s, kind=%s, tags=%s, content_sha256=%s, "
                    "char_count=%s, updated_at=%s WHERE id=%s",
                    (
                        document.title,
                        document.kind,
                        ",".join(document.tags),
                        digest,
                        len(raw),
                        now,
                        document_id,
                    ),
                )
                cursor.execute("DELETE FROM zx_chunks WHERE document_id = %s", (document_id,))
                stats.updated += 1
            else:
                cursor.execute(
                    "INSERT INTO zx_documents (path, title, kind, tags, content_sha256, char_count, "
                    "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        relative,
                        document.title,
                        document.kind,
                        ",".join(document.tags),
                        digest,
                        len(raw),
                        now,
                        now,
                    ),
                )
                document_id = int(cursor.lastrowid)
                stats.added += 1
            if rows:
                cursor.executemany(
                    "INSERT INTO zx_chunks (document_id, seq, heading, body, char_start, char_end, "
                    "start_line, end_line, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [(document_id, *row) for row in rows],
                )
        stats.chunks += len(rows)
        stats.documents.append(relative)

    store.execute(
        "INSERT INTO zx_meta (k, v) VALUES (%s, %s) ON DUPLICATE KEY UPDATE v = VALUES(v)",
        ("last_ingest_at", now.isoformat(sep=" ")),
    )
    return stats


def seed_aliases(store: Store, seed_path: Path) -> int:
    """写入术语/别名表，供查询扩展使用（已存在则更新）。"""

    if not seed_path.exists():
        return 0
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    count = 0
    for entry in payload:
        term = str(entry["term"]).strip()
        aliases = entry.get("aliases", [])
        if isinstance(aliases, str):
            aliases = [aliases]
        joined = ",".join(str(alias).strip() for alias in aliases if str(alias).strip())
        if not term or not joined:
            continue
        store.execute(
            "INSERT INTO zx_aliases (term, aliases) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE aliases = VALUES(aliases)",
            (term, joined),
        )
        count += 1
    return count


def list_documents(store: Store) -> list[dict[str, object]]:
    rows = store.fetch_all(
        "SELECT d.id, d.path, d.title, d.kind, d.tags, d.char_count, d.updated_at, "
        "       COUNT(c.id) AS chunk_count "
        "FROM zx_documents AS d LEFT JOIN zx_chunks AS c ON c.document_id = d.id "
        "GROUP BY d.id, d.path, d.title, d.kind, d.tags, d.char_count, d.updated_at "
        "ORDER BY d.path"
    )
    # 统一成可直接 JSON 序列化的字符串
    for row in rows:
        row["updated_at"] = str(row["updated_at"])
        row["chunk_count"] = int(row["chunk_count"])
        row["char_count"] = int(row["char_count"])
        row["id"] = int(row["id"])
    return rows
