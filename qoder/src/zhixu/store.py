"""seekdb 存储层：单进程嵌入式实例 + 幂等建表 + 事务。

选 seekdb 作为唯一事实源：文档、片段、任务、任务事件、检索日志、周报都存在
同一个嵌入式库里，重启后按原样读回；片段表上同时有 ngram 全文索引和 HNSW
向量索引，混合检索直接走数据库，而不是把数据搬到内存里再过滤。
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = "1"

DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS zx_meta (
        k VARCHAR(64) NOT NULL,
        v VARCHAR(1024) NOT NULL,
        PRIMARY KEY (k)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS zx_documents (
        id BIGINT NOT NULL AUTO_INCREMENT,
        path VARCHAR(512) NOT NULL,
        title VARCHAR(255) NOT NULL,
        kind VARCHAR(32) NOT NULL,
        tags VARCHAR(255) NOT NULL DEFAULT '',
        content_sha256 CHAR(64) NOT NULL,
        char_count INT NOT NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        PRIMARY KEY (id),
        UNIQUE KEY uk_path (path)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS zx_chunks (
        id BIGINT NOT NULL AUTO_INCREMENT,
        document_id BIGINT NOT NULL,
        seq INT NOT NULL,
        heading VARCHAR(255) NOT NULL,
        body TEXT NOT NULL,
        char_start INT NOT NULL,
        char_end INT NOT NULL,
        start_line INT NOT NULL,
        end_line INT NOT NULL,
        embedding VECTOR(384) NOT NULL,
        PRIMARY KEY (id),
        KEY ix_doc_seq (document_id, seq),
        FULLTEXT KEY ft_body (body) WITH PARSER ngram,
        VECTOR KEY vk_embedding (embedding) WITH (DISTANCE=COSINE, TYPE=HNSW, M=16, EF_CONSTRUCTION=200, EF_SEARCH=64)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS zx_tasks (
        id BIGINT NOT NULL AUTO_INCREMENT,
        title VARCHAR(255) NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'todo',
        priority VARCHAR(8) NOT NULL DEFAULT 'P2',
        owner VARCHAR(64) NOT NULL DEFAULT '',
        due_date DATE NULL,
        detail TEXT NOT NULL,
        evidence_document_id BIGINT NULL,
        evidence_chunk_id BIGINT NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        PRIMARY KEY (id),
        KEY ix_status_due (status, due_date)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS zx_task_events (
        id BIGINT NOT NULL AUTO_INCREMENT,
        task_id BIGINT NOT NULL,
        from_status VARCHAR(16) NOT NULL,
        to_status VARCHAR(16) NOT NULL,
        note VARCHAR(500) NOT NULL DEFAULT '',
        created_at DATETIME NOT NULL,
        PRIMARY KEY (id),
        KEY ix_task_event (task_id, id)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS zx_aliases (
        id BIGINT NOT NULL AUTO_INCREMENT,
        term VARCHAR(64) NOT NULL,
        aliases VARCHAR(512) NOT NULL,
        PRIMARY KEY (id),
        UNIQUE KEY uk_term (term)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS zx_searches (
        id BIGINT NOT NULL AUTO_INCREMENT,
        query VARCHAR(500) NOT NULL,
        hits INT NOT NULL,
        top_path VARCHAR(512) NOT NULL DEFAULT '',
        created_at DATETIME NOT NULL,
        PRIMARY KEY (id),
        KEY ix_search_time (created_at)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS zx_digests (
        id BIGINT NOT NULL AUTO_INCREMENT,
        period_start DATE NOT NULL,
        period_end DATE NOT NULL,
        markdown MEDIUMTEXT NOT NULL,
        created_at DATETIME NOT NULL,
        PRIMARY KEY (id)
    ) DEFAULT CHARSET = utf8mb4
    """,
)


class StoreError(RuntimeError):
    """存储层的可读错误。"""


@dataclass(frozen=True)
class StoreInfo:
    path: str
    server_version: str
    port: int
    schema_version: str
    transport: str = ""


class Store:
    """一个 seekdb 嵌入式实例的句柄。

    同一数据目录在一个进程内只允许打开一次；跨命令行的做法是每次命令
    打开、用完关闭，``serve`` 则在进程生命周期内独占。
    """

    def __init__(self, path: Path):
        self._path = Path(path)
        self._instance: Any | None = None
        self._conn: Any | None = None
        self._lock = threading.RLock()
        self._closed = False

    # ---------------------------------------------------------------- open
    def open(self) -> "Store":
        if self._conn is not None:
            return self
        try:
            import pymysql
            import pylibseekdb
        except ImportError as exc:  # pragma: no cover - 环境问题
            raise StoreError(
                "缺少依赖：请先执行 `pip install -r requirements.txt`（需要 pylibseekdb 与 PyMySQL）"
            ) from exc

        self._path.mkdir(parents=True, exist_ok=True)
        try:
            self._instance = pylibseekdb.open(str(self._path))
        except Exception as exc:  # pragma: no cover - 实例启动失败
            raise StoreError(f"seekdb 实例启动失败：{self._path}：{exc}") from exc

        options = dict(self._instance.connection_options())
        try:
            self._conn = pymysql.connect(
                database="test",
                charset="utf8mb4",
                autocommit=False,
                connect_timeout=15,
                init_command="SET autocommit = 0",
                **options,
            )
        except Exception as exc:  # pragma: no cover
            self.close()
            raise StoreError(f"连接 seekdb 失败：{exc}") from exc
        self._ensure_schema()
        return self

    def _ensure_schema(self) -> None:
        with self._lock:
            cursor = self._conn.cursor()
            try:
                for statement in DDL:
                    cursor.execute(statement)
                self._conn.commit()
                cursor.execute("SELECT version()")
                version = cursor.fetchone()[0]
                self._set_meta("schema_version", SCHEMA_VERSION, commit=True)
                self._set_meta("engine", "seekdb", commit=True)
                self._server_version = version
            finally:
                cursor.close()

    def _set_meta(self, key: str, value: str, *, commit: bool = False) -> None:
        cursor = self._conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO zx_meta (k, v) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE v = VALUES(v)",
                (key, value),
            )
            if commit:
                self._conn.commit()
        finally:
            cursor.close()

    # ------------------------------------------------------------- queries
    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            cursor = self._conn.cursor()
            try:
                cursor.execute(sql, params)
                columns = [item[0] for item in cursor.description or ()]
                return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
            finally:
                cursor.close()

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.fetch_all(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self._lock:
            cursor = self._conn.cursor()
            try:
                affected = cursor.execute(sql, params)
                self._conn.commit()
                return affected
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cursor.close()

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self._lock:
            cursor = self._conn.cursor()
            try:
                yield cursor
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cursor.close()

    def info(self) -> StoreInfo:
        meta = self.fetch_one("SELECT v FROM zx_meta WHERE k = %s", ("schema_version",))
        options = dict(self._instance.connection_options())
        port = int(options.get("port") or 0)
        socket = str(options.get("unix_socket") or "")
        transport = socket if socket else f"127.0.0.1:{port}"
        return StoreInfo(
            path=str(self._path),
            server_version=getattr(self, "_server_version", "unknown"),
            port=port,
            schema_version=(meta or {}).get("v", "unknown"),
            transport=transport,
        )

    def count(self, table: str) -> int:
        row = self.fetch_one(f"SELECT COUNT(*) AS n FROM {table}")
        return int(row["n"]) if row else 0

    # --------------------------------------------------------------- close
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
            if self._instance is not None:
                try:
                    self._instance.close()
                except Exception:
                    pass
                self._instance = None

    def __enter__(self) -> "Store":
        return self.open()

    def __exit__(self, *exc_info: object) -> None:
        self.close()
