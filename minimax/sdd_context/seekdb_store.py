"""seekdb storage and retrieval for engineering materials.

The module keeps every spec item, code symbol and test in an embedded seekdb
instance and never answers from an in-memory cache: each ``sdd context`` run
issues SQL against seekdb, so a reviewer can replay the same queries by hand
(``sdd db query``).

Schema
------
``sdd_documents``          one row per source file with its content revision
``sdd_items``              one row per retrievable item (spec section / symbol / test)
``sdd_imports``            one row per successful import run
``sdd_document_history``   revision transitions, so spec drift stays visible

``sdd_items.searchable`` carries a FULLTEXT index parsed by seekdb's SPACE
parser; retrieval uses ``MATCH ... AGAINST`` plus exact symbol/path lookups.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from .chunking import Document, Item, digest

SCHEMA: tuple[tuple[str, str], ...] = (
    (
        "sdd_documents",
        """CREATE TABLE IF NOT EXISTS sdd_documents (
            doc_id VARCHAR(64) NOT NULL,
            kind VARCHAR(16) NOT NULL,
            path VARCHAR(512) NOT NULL,
            revision VARCHAR(64) NOT NULL,
            title VARCHAR(512) NOT NULL DEFAULT '',
            item_count INT NOT NULL DEFAULT 0,
            imported_at VARCHAR(40) NOT NULL DEFAULT '',
            PRIMARY KEY (doc_id),
            KEY ix_sdd_doc_path (path(191)),
            KEY ix_sdd_doc_kind (kind)
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    ),
    (
        "sdd_items",
        """CREATE TABLE IF NOT EXISTS sdd_items (
            item_id VARCHAR(64) NOT NULL,
            doc_id VARCHAR(64) NOT NULL,
            kind VARCHAR(16) NOT NULL,
            path VARCHAR(512) NOT NULL,
            item_ref VARCHAR(768) NOT NULL,
            symbol VARCHAR(255) NOT NULL DEFAULT '',
            title VARCHAR(512) NOT NULL DEFAULT '',
            start_line INT NOT NULL,
            end_line INT NOT NULL,
            revision VARCHAR(64) NOT NULL,
            text LONGTEXT NOT NULL,
            searchable LONGTEXT NOT NULL,
            PRIMARY KEY (item_id),
            KEY ix_sdd_item_doc (doc_id),
            KEY ix_sdd_item_kind (kind),
            KEY ix_sdd_item_path (path(191)),
            KEY ix_sdd_item_symbol (symbol)
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    ),
    (
        "sdd_imports",
        """CREATE TABLE IF NOT EXISTS sdd_imports (
            import_id VARCHAR(40) NOT NULL,
            started_at VARCHAR(40) NOT NULL,
            finished_at VARCHAR(40) NOT NULL DEFAULT '',
            repo_path VARCHAR(1024) NOT NULL,
            seekdb_target VARCHAR(512) NOT NULL DEFAULT '',
            spec_revision VARCHAR(64) NOT NULL DEFAULT '',
            code_revision VARCHAR(64) NOT NULL DEFAULT '',
            test_revision VARCHAR(64) NOT NULL DEFAULT '',
            document_count INT NOT NULL DEFAULT 0,
            item_count INT NOT NULL DEFAULT 0,
            notes LONGTEXT,
            PRIMARY KEY (import_id)
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    ),
    (
        "sdd_document_history",
        """CREATE TABLE IF NOT EXISTS sdd_document_history (
            history_id VARCHAR(80) NOT NULL,
            import_id VARCHAR(40) NOT NULL,
            doc_id VARCHAR(64) NOT NULL,
            kind VARCHAR(16) NOT NULL,
            path VARCHAR(512) NOT NULL,
            revision_before VARCHAR(64) NOT NULL DEFAULT '',
            revision_after VARCHAR(64) NOT NULL DEFAULT '',
            change_kind VARCHAR(16) NOT NULL,
            imported_at VARCHAR(40) NOT NULL,
            PRIMARY KEY (history_id),
            KEY ix_sdd_hist_doc (doc_id)
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    ),
)

FULLTEXT_INDEX = "ix_sdd_item_search"
FULLTEXT_DDL = f"CREATE FULLTEXT INDEX {FULLTEXT_INDEX} ON sdd_items (searchable) WITH PARSER SPACE"

ITEM_COLUMNS = (
    "item_id, doc_id, kind, path, item_ref, symbol, title, start_line, end_line, revision, text, searchable"
)
RESULT_COLUMNS = (
    "item_id, kind, path, item_ref, symbol, title, start_line, end_line, revision, text"
)


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class SeekDBUnavailable(RuntimeError):
    """The embedded seekdb binding or the configured server is unreachable."""


def _open_embedded(path: Path) -> Any:
    """Open a local seekdb instance, tolerating a sync or async binding."""
    try:
        import pylibseekdb  # noqa: PLC0415 - optional dependency by design
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        raise SeekDBUnavailable(
            "pylibseekdb is not installed; run scripts/bootstrap.sh or set SDD_SEEKDB_DSN"
        ) from exc

    opener = getattr(pylibseekdb, "open", None)
    if callable(opener):
        return opener(str(path))
    async_opener = getattr(pylibseekdb, "aopen", None)
    if not callable(async_opener):
        raise SeekDBUnavailable("pylibseekdb exposes neither open() nor aopen()")

    async def _run() -> Any:
        return await async_opener(str(path))

    return asyncio.run(_run())


@dataclass
class QueryTrace:
    """A SQL statement that participated in producing a context pack."""

    purpose: str
    sql: str
    params: list[Any]
    rows: int

    def as_dict(self) -> dict[str, Any]:
        return {"purpose": self.purpose, "sql": " ".join(self.sql.split()), "params": self.params, "rows": self.rows}


class SeekDBStore:
    """Thin synchronous wrapper over one seekdb instance or server."""

    def __init__(self, target: Any) -> None:
        self.target = target
        self._instance: Any = None
        self._conn: Any = None
        self.traces: list[QueryTrace] = []
        self.connection_options: dict[str, Any] = {}

    # -- lifecycle ---------------------------------------------------------
    def open(self) -> "SeekDBStore":
        import pymysql  # noqa: PLC0415 - keep import cost out of --help

        if self.target.kind == "embedded":
            path = Path(self.target.path)
            path.mkdir(parents=True, exist_ok=True)
            self._instance = _open_embedded(path)
            options = {k: v for k, v in dict(self._instance.connection_options()).items() if k != "database"}
            self.connection_options = options
            connect_kwargs = dict(options)
        else:
            connect_kwargs = {
                "host": self.target.host,
                "port": self.target.port,
                "user": self.target.user,
                "password": self.target.password,
            }
            self.connection_options = dict(connect_kwargs)
        self._conn = pymysql.connect(
            **connect_kwargs,
            database=self.target.database,
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=15,
            read_timeout=120,
            write_timeout=120,
            init_command="SET autocommit = 1",
        )
        return self

    def close(self) -> None:
        if self._conn is not None:
            with suppress(Exception):
                self._conn.close()
            self._conn = None
        if self._instance is not None:
            with suppress(Exception):
                self._instance.close()
            self._instance = None

    def __enter__(self) -> "SeekDBStore":
        return self.open()

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- SQL ---------------------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self._conn.cursor() as cursor:
            return cursor.execute(sql, tuple(params))

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> int:
        with self._conn.cursor() as cursor:
            return cursor.executemany(sql, list(rows))

    def query(self, sql: str, params: Sequence[Any] = (), *, purpose: str = "") -> list[tuple[Any, ...]]:
        started = time.perf_counter()
        with self._conn.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            rows = list(cursor.fetchall())
        if purpose:
            self.traces.append(QueryTrace(purpose, sql, list(params), len(rows)))
        elapsed = time.perf_counter() - started
        if elapsed > 30:  # pragma: no cover - operational warning
            print(f"slow seekdb query ({elapsed:.1f}s): {purpose or sql[:60]}")
        return rows

    def scalar(self, sql: str, params: Sequence[Any] = (), *, purpose: str = "") -> Any:
        rows = self.query(sql, params, purpose=purpose)
        return rows[0][0] if rows else None

    # -- schema ------------------------------------------------------------
    def initialize(self) -> dict[str, Any]:
        """Create tables and the FULLTEXT index; safe to call repeatedly."""
        for table, ddl in SCHEMA:
            self.execute(ddl)
        existing = {
            row[0]
            for row in self.query(
                "SELECT index_name FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = 'sdd_items'"
            )
        }
        created_index = False
        if FULLTEXT_INDEX not in existing:
            try:
                self.execute(FULLTEXT_DDL)
                created_index = True
            except Exception as exc:  # duplicate index from a concurrent run
                message = str(exc)
                if "1061" not in message and "Duplicate key name" not in message:
                    raise
        version = self.scalar("SELECT VERSION()") or ""
        return {"tables": [name for name, _ in SCHEMA], "fulltext_index": FULLTEXT_INDEX,
                "fulltext_created": created_index, "server_version": str(version)}

    # -- import ------------------------------------------------------------
    def apply_import(self, documents: Sequence[Document], *, repo_path: str, target_label: str,
                     notes: str = "") -> dict[str, Any]:
        """Replace the index content with ``documents`` and journal the diff."""
        import_id = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f")[:22]
        started = now_iso()
        previous = {doc_id: revision for doc_id, revision in self.query("SELECT doc_id, revision FROM sdd_documents")}

        self.execute("DELETE FROM sdd_items")
        self.execute("DELETE FROM sdd_documents")

        imported_at = now_iso()
        doc_rows = []
        item_rows = []
        for document in documents:
            doc_rows.append(
                (document.doc_id, document.kind, document.path, document.revision,
                 document.title, len(document.items), imported_at)
            )
            for item in document.items:
                item_rows.append(item.as_row())
        if doc_rows:
            self.executemany(
                f"INSERT INTO sdd_documents (doc_id, kind, path, revision, title, item_count, imported_at) "
                f"VALUES ({', '.join(['%s'] * 7)})",
                doc_rows,
            )
        if item_rows:
            self.executemany(f"INSERT INTO sdd_items ({ITEM_COLUMNS}) VALUES ({', '.join(['%s'] * 12)})", item_rows)

        history = []
        by_id = {document.doc_id: document for document in documents}
        for doc_id, document in by_id.items():
            before = previous.get(doc_id, "")
            change = "added" if not before else ("unchanged" if before == document.revision else "changed")
            history.append((
                f"{import_id}:{doc_id}",
                import_id,
                doc_id,
                document.kind,
                document.path,
                before,
                document.revision,
                change,
                imported_at,
            ))
        for doc_id, revision in previous.items():
            if doc_id in by_id:
                continue
            history.append((
                f"{import_id}:{doc_id}",
                import_id,
                doc_id,
                "",
                "",
                revision,
                "",
                "removed",
                imported_at,
            ))
        if history:
            self.executemany(
                "INSERT INTO sdd_document_history (history_id, import_id, doc_id, kind, path, "
                "revision_before, revision_after, change_kind, imported_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                history,
            )

        revisions = _family_revisions(documents)
        self.execute(
            "INSERT INTO sdd_imports (import_id, started_at, finished_at, repo_path, seekdb_target, "
            "spec_revision, code_revision, test_revision, document_count, item_count, notes) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (import_id, started, now_iso(), repo_path, target_label,
             revisions["spec"], revisions["code"], revisions["test"],
             len(documents), sum(len(d.items) for d in documents), notes),
        )
        return {
            "import_id": import_id,
            "documents": len(documents),
            "items": sum(len(d.items) for d in documents),
            "revisions": revisions,
            "changes": {kind: sum(1 for row in history if row[7] == kind)
                        for kind in ("added", "changed", "removed", "unchanged")},
            "imported_at": imported_at,
        }

    # -- retrieval ---------------------------------------------------------
    def fulltext_search(self, terms: Sequence[str], *, limit: int = 60) -> list[dict[str, Any]]:
        query = " ".join(terms)
        if not query.strip():
            return []
        sql = (
            f"SELECT {RESULT_COLUMNS}, "
            "MATCH(searchable) AGAINST (%s IN NATURAL LANGUAGE MODE) AS score "
            "FROM sdd_items WHERE MATCH(searchable) AGAINST (%s IN NATURAL LANGUAGE MODE) "
            "ORDER BY score DESC, item_ref LIMIT %s"
        )
        rows = self.query(sql, (query, query, limit), purpose=f"fulltext:{query}")
        return [self._hit(row, "fulltext match") for row in rows]

    def exact_search(self, terms: Sequence[str], *, limit: int = 40) -> list[dict[str, Any]]:
        """Exact identifier lookup, so ``apply_coupon`` or ``pricing.py`` is never missed."""
        if not terms:
            return []
        clauses = []
        params: list[Any] = []
        for term in terms:
            like = f"%{term.lower()}%"
            clauses.append("(LOWER(symbol) LIKE %s OR LOWER(path) LIKE %s OR LOWER(title) LIKE %s)")
            params.extend([like, like, like])
        sql = (
            f"SELECT {RESULT_COLUMNS} FROM sdd_items WHERE {' OR '.join(clauses)} "
            "ORDER BY kind, item_ref LIMIT %s"
        )
        params.append(limit)
        rows = self.query(sql, params, purpose=f"exact-symbol:{','.join(terms[:4])}")
        hits = []
        for row in rows:
            hit = self._hit(row, "exact symbol/path match")
            hit["exact"] = True
            hits.append(hit)
        return hits

    def tests_importing(self, modules: Sequence[str], *, limit: int = 20) -> list[dict[str, Any]]:
        """Tests whose imports reference one of the selected code modules."""
        if not modules:
            return []
        clauses = []
        params: list[Any] = []
        for module in modules:
            like = f"%{module.lower()}%"
            clauses.append("LOWER(text) LIKE %s")
            params.append(like)
        sql = (
            f"SELECT {RESULT_COLUMNS} FROM sdd_items WHERE kind = 'test' AND ({' OR '.join(clauses)}) "
            "ORDER BY item_ref LIMIT %s"
        )
        params.append(limit)
        rows = self.query(sql, params, purpose=f"tests-importing:{','.join(modules[:3])}")
        hits = []
        for row in rows:
            hit = self._hit(row, f"imports {', '.join(modules[:2])}")
            hit["linked"] = True
            hits.append(hit)
        return hits

    def items_by_ref(self, refs: Sequence[str]) -> dict[str, dict[str, Any]]:
        if not refs:
            return {}
        placeholders = ", ".join(["%s"] * len(refs))
        rows = self.query(
            f"SELECT {RESULT_COLUMNS} FROM sdd_items WHERE item_ref IN ({placeholders})",
            list(refs),
            purpose="lookup-by-ref",
        )
        return {row[3]: self._hit(row, "requested by reference") for row in rows}

    def latest_import(self) -> dict[str, Any] | None:
        rows = self.query(
            "SELECT import_id, started_at, finished_at, repo_path, seekdb_target, spec_revision, "
            "code_revision, test_revision, document_count, item_count FROM sdd_imports "
            "ORDER BY started_at DESC LIMIT 1",
            purpose="latest-import",
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "import_id": row[0], "started_at": row[1], "finished_at": row[2], "repo_path": row[3],
            "seekdb_target": row[4], "spec_revision": row[5], "code_revision": row[6],
            "test_revision": row[7], "document_count": row[8], "item_count": row[9],
        }

    def document_changes(self, import_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        clauses = "WHERE change_kind <> 'unchanged'"
        params: list[Any] = []
        if import_id:
            clauses += " AND import_id = %s"
            params.append(import_id)
        params.append(limit)
        rows = self.query(
            "SELECT import_id, path, kind, revision_before, revision_after, change_kind, imported_at "
            f"FROM sdd_document_history {clauses} "
            "ORDER BY imported_at DESC, path LIMIT %s",
            params,
            purpose="document-changes",
        )
        return [
            {"import_id": row[0], "path": row[1], "kind": row[2], "before": row[3][:12],
             "after": row[4][:12], "change": row[5], "imported_at": row[6]}
            for row in rows
        ]

    def stats(self) -> dict[str, Any]:
        def count(table: str) -> int:
            return int(self.scalar(f"SELECT COUNT(*) FROM {table}") or 0)

        by_kind = {
            row[0]: int(row[1])
            for row in self.query("SELECT kind, COUNT(*) FROM sdd_items GROUP BY kind")
        }
        indexes = list(
            dict.fromkeys(
                row[0]
                for row in self.query(
                    "SELECT index_name FROM information_schema.statistics "
                    "WHERE table_schema = DATABASE() AND table_name = 'sdd_items'"
                )
            )
        )
        return {
            "target": self.target.label,
            "server_version": str(self.scalar("SELECT VERSION()") or ""),
            "database": self.scalar("SELECT DATABASE()"),
            "documents": count("sdd_documents"),
            "items": count("sdd_items"),
            "items_by_kind": by_kind,
            "imports": count("sdd_imports"),
            "history_rows": count("sdd_document_history"),
            "indexes": indexes,
            "data_path": str(getattr(self.target, "path", "") or ""),
        }

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _hit(row: Sequence[Any], reason: str) -> dict[str, Any]:
        return {
            "item_id": row[0],
            "kind": row[1],
            "path": row[2],
            "item_ref": row[3],
            "symbol": row[4],
            "title": row[5],
            "start_line": int(row[6]),
            "end_line": int(row[7]),
            "revision": row[8],
            "text": row[9],
            "reasons": [reason],
        }


def _family_revisions(documents: Sequence[Document]) -> dict[str, str]:
    revisions: dict[str, str] = {}
    for family in ("spec", "code", "test"):
        parts = sorted(f"{d.path}:{d.revision}" for d in documents if d.kind == family)
        revisions[family] = digest("\n".join(parts))[:32] if parts else ""
    return revisions


def load_item_rows(rows: Sequence[Sequence[Any]]) -> list[Item]:
    """Rebuild ``Item`` objects from ``sdd_items`` rows (used by tests)."""
    items = []
    for row in rows:
        items.append(
            Item(
                item_id=row[0], kind=row[1], path=row[2], item_ref=row[3], symbol=row[4],
                title=row[5], start_line=int(row[6]), end_line=int(row[7]), revision=row[8],
                text=row[9], searchable="", doc_id="",
            )
        )
    return items
