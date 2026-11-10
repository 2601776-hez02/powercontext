"""seekdb 访问层：建表、事务、查询。

只做一件事——把「酒馆」的全部状态放进 seekdb：
世界设定、玩家账本、NPC 好感、物品库存、事件流水，以及带全文索引和向量索引的
NPC 记忆。所有状态变更都跑在一个事务里，非法动作由数据库回滚兜底。
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator

from .config import Settings

DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS tw_meta (
        k VARCHAR(64) NOT NULL,
        v VARCHAR(1024) NOT NULL,
        PRIMARY KEY (k)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS tw_player (
        guest VARCHAR(64) NOT NULL,
        coins INT NOT NULL,
        reputation INT NOT NULL DEFAULT 0,
        wanted INT NOT NULL DEFAULT 0,
        turn INT NOT NULL DEFAULT 0,
        location VARCHAR(32) NOT NULL DEFAULT '大堂',
        status VARCHAR(16) NOT NULL DEFAULT 'open',
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        PRIMARY KEY (guest)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS tw_npc (
        npc_id VARCHAR(32) NOT NULL,
        name VARCHAR(32) NOT NULL,
        title VARCHAR(32) NOT NULL,
        voice VARCHAR(512) NOT NULL,
        secret_text VARCHAR(512) NOT NULL,
        secret_threshold INT NOT NULL,
        attention INT NOT NULL DEFAULT 50,
        location VARCHAR(32) NOT NULL DEFAULT '大堂',
        confide VARCHAR(512) NOT NULL DEFAULT '',
        PRIMARY KEY (npc_id)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS tw_affinity (
        guest VARCHAR(64) NOT NULL,
        npc_id VARCHAR(32) NOT NULL,
        value INT NOT NULL DEFAULT 40,
        updated_at DATETIME NOT NULL,
        PRIMARY KEY (guest, npc_id)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS tw_item (
        item_id VARCHAR(32) NOT NULL,
        name VARCHAR(64) NOT NULL,
        price INT NOT NULL,
        kind VARCHAR(16) NOT NULL,
        description VARCHAR(512) NOT NULL,
        owner_npc VARCHAR(32) NOT NULL DEFAULT '',
        lore VARCHAR(512) NOT NULL DEFAULT '',
        PRIMARY KEY (item_id)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS tw_inventory (
        guest VARCHAR(64) NOT NULL,
        item_id VARCHAR(32) NOT NULL,
        qty INT NOT NULL DEFAULT 1,
        PRIMARY KEY (guest, item_id)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS tw_event (
        id BIGINT NOT NULL AUTO_INCREMENT,
        guest VARCHAR(64) NOT NULL,
        turn INT NOT NULL,
        verb VARCHAR(32) NOT NULL,
        target_npc VARCHAR(32) NOT NULL DEFAULT '',
        item_id VARCHAR(32) NOT NULL DEFAULT '',
        verdict VARCHAR(16) NOT NULL,
        utterance VARCHAR(512) NOT NULL,
        detail VARCHAR(512) NOT NULL,
        created_at DATETIME NOT NULL,
        PRIMARY KEY (id),
        KEY ix_guest_turn (guest, turn)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS tw_memory (
        id BIGINT NOT NULL AUTO_INCREMENT,
        guest VARCHAR(64) NOT NULL,
        npc VARCHAR(32) NOT NULL,
        turn INT NOT NULL,
        kind VARCHAR(16) NOT NULL,
        body VARCHAR(1000) NOT NULL,
        emotion VARCHAR(16) NOT NULL,
        importance TINYINT NOT NULL DEFAULT 5,
        source VARCHAR(16) NOT NULL DEFAULT '亲历',
        embedding VECTOR(1024) NOT NULL,
        created_at DATETIME NOT NULL,
        PRIMARY KEY (id),
        KEY ix_guest_npc (guest, npc, id),
        FULLTEXT KEY ft_body (body) WITH PARSER ngram,
        VECTOR KEY vk_embedding (embedding) WITH (DISTANCE=COSINE, TYPE=HNSW, M=16, EF_CONSTRUCTION=200, EF_SEARCH=64)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS tw_legend (
        id BIGINT NOT NULL AUTO_INCREMENT,
        guest VARCHAR(64) NOT NULL,
        session_no INT NOT NULL,
        ending VARCHAR(16) NOT NULL,
        summary VARCHAR(2000) NOT NULL,
        created_at DATETIME NOT NULL,
        PRIMARY KEY (id),
        KEY ix_guest (guest, id)
    ) DEFAULT CHARSET = utf8mb4
    """,
)

SCHEMA_VERSION = "3"

# 老库升级：ADD COLUMN 在 MySQL/seekdb 里没有 IF NOT EXISTS，重复执行会报 1060，
# 这里逐条尝试并忽略「列已存在」。
MIGRATIONS: tuple[str, ...] = (
    "ALTER TABLE tw_npc ADD COLUMN confide VARCHAR(512) NOT NULL DEFAULT ''",
    # 被逐出时记下会话 id：下一个会话（新对话）视为「第二天」，可以再进门
    "ALTER TABLE tw_player ADD COLUMN ban_session VARCHAR(120) NOT NULL DEFAULT ''",
)


class SeekDBError(RuntimeError):
    """连接或 SQL 层的可读错误。"""


class SeekDB:
    """一个进程内的 seekdb 连接句柄（按线程复用连接）。"""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._local = threading.local()
        self._schema_ready = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ conn
    def _connect(self) -> Any:
        try:
            import pymysql
        except ImportError as exc:  # pragma: no cover - 环境问题
            raise SeekDBError("缺少依赖 PyMySQL，请先执行 pip install -r requirements.txt") from exc
        cfg = self._settings
        return pymysql.connect(
            host=cfg.seekdb_host,
            port=cfg.seekdb_port,
            user=cfg.seekdb_user,
            password=cfg.seekdb_password,
            database=cfg.seekdb_database,
            charset=cfg.seekdb_charset,
            autocommit=False,
            connect_timeout=10,
            read_timeout=30,
            init_command="SET autocommit = 0",
        )

    @property
    def conn(self) -> Any:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        else:
            try:
                conn.ping(reconnect=True)
            except Exception:  # pragma: no cover - 重连失败时重建
                conn = self._connect()
                self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._local.conn = None

    # ---------------------------------------------------------------- schema
    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._lock:
            if self._schema_ready:
                return
            cursor = self.conn.cursor()
            try:
                for statement in DDL:
                    cursor.execute(statement)
                self.conn.commit()
                for statement in MIGRATIONS:
                    try:
                        cursor.execute(statement)
                        self.conn.commit()
                    except Exception:
                        # 列已存在（1060）或该版本不支持，忽略即可
                        self.conn.rollback()
            finally:
                cursor.close()
            self._schema_ready = True

    # --------------------------------------------------------------- queries
    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, params)
            columns = [item[0] for item in cursor.description or ()]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        row = self.one(sql, params)
        return None if row is None else next(iter(row.values()))

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        cursor = self.conn.cursor()
        try:
            affected = cursor.execute(sql, params)
            self.conn.commit()
            return affected
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        """事务游标：正常退出提交，异常回滚（非法动作用它兜底）。"""

        cursor = self.conn.cursor()
        try:
            yield cursor
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    # ------------------------------------------------------------------ info
    def version(self) -> str:
        return str(self.scalar("SELECT version()"))

    def table_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for table in ("tw_memory", "tw_event", "tw_player", "tw_npc", "tw_item", "tw_legend"):
            try:
                out[table] = int(self.scalar(f"SELECT COUNT(*) FROM {table}"))
            except Exception:
                out[table] = -1
        self.conn.commit()
        return out

    def show_create(self, table: str) -> str:
        row = self.one(f"SHOW CREATE TABLE {table}")
        if not row:
            return ""
        return str(row.get("Create Table") or next(iter(row.values())))

    def explain(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        rows = self.query("EXPLAIN " + sql, params)
        self.conn.commit()
        return rows
