"""任务板：状态流转、进度事件、逾期统计。

每个状态变更都写一条 ``zx_task_events``，周报直接从这个事件流里折叠出
「本周完成了什么」，而不是依赖手工维护的进度表。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .store import Store

STATUS_LABELS = {
    "todo": "待开始",
    "doing": "进行中",
    "blocked": "阻塞",
    "done": "已完成",
}
STATUS_ORDER = ("blocked", "doing", "todo", "done")
PRIORITIES = ("P0", "P1", "P2", "P3")


@dataclass
class Task:
    id: int
    title: str
    status: str
    priority: str
    owner: str
    due_date: str | None
    detail: str
    evidence_document_id: int | None
    evidence_chunk_id: int | None
    created_at: str
    updated_at: str

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _row_to_task(row: dict[str, object]) -> Task:
    return Task(
        id=int(row["id"]),
        title=str(row["title"]),
        status=str(row["status"]),
        priority=str(row["priority"]),
        owner=str(row["owner"] or ""),
        due_date=str(row["due_date"]) if row.get("due_date") else None,
        detail=str(row["detail"] or ""),
        evidence_document_id=(
            int(row["evidence_document_id"]) if row.get("evidence_document_id") is not None else None
        ),
        evidence_chunk_id=(
            int(row["evidence_chunk_id"]) if row.get("evidence_chunk_id") is not None else None
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def list_tasks(store: Store, *, include_done: bool = True) -> list[Task]:
    sql = (
        "SELECT id, title, status, priority, owner, due_date, detail, evidence_document_id, "
        "evidence_chunk_id, created_at, updated_at FROM zx_tasks"
    )
    params: tuple[object, ...] = ()
    if not include_done:
        sql += " WHERE status <> %s"
        params = ("done",)
    sql += (
        " ORDER BY CASE status WHEN 'blocked' THEN 0 WHEN 'doing' THEN 1 WHEN 'todo' THEN 2 ELSE 3 END,"
        " CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,"
        " due_date IS NULL, due_date, id"
    )
    return [_row_to_task(row) for row in store.fetch_all(sql, params)]


def get_task(store: Store, task_id: int) -> Task | None:
    row = store.fetch_one(
        "SELECT id, title, status, priority, owner, due_date, detail, evidence_document_id, "
        "evidence_chunk_id, created_at, updated_at FROM zx_tasks WHERE id = %s",
        (task_id,),
    )
    return _row_to_task(row) if row else None


def add_task(
    store: Store,
    title: str,
    *,
    status: str = "todo",
    priority: str = "P2",
    owner: str = "",
    due: str | None = None,
    detail: str = "",
    evidence_document_id: int | None = None,
    evidence_chunk_id: int | None = None,
) -> Task:
    if status not in STATUS_LABELS:
        raise ValueError(f"未知状态：{status}")
    if priority not in PRIORITIES:
        raise ValueError(f"未知优先级：{priority}")
    now = _now()
    with store.transaction() as cursor:
        cursor.execute(
            "INSERT INTO zx_tasks (title, status, priority, owner, due_date, detail, "
            "evidence_document_id, evidence_chunk_id, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                title[:255],
                status,
                priority,
                owner[:64],
                due,
                detail,
                evidence_document_id,
                evidence_chunk_id,
                now,
                now,
            ),
        )
        task_id = int(cursor.lastrowid)
        cursor.execute(
            "INSERT INTO zx_task_events (task_id, from_status, to_status, note, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (task_id, "", status, "创建任务", now),
        )
    task = get_task(store, task_id)
    assert task is not None
    return task


def set_status(store: Store, task_id: int, status: str, *, note: str = "") -> Task:
    if status not in STATUS_LABELS:
        raise ValueError(f"未知状态：{status}")
    current = get_task(store, task_id)
    if current is None:
        raise ValueError(f"任务不存在：{task_id}")
    if current.status == status and not note:
        return current
    now = _now()
    with store.transaction() as cursor:
        cursor.execute(
            "UPDATE zx_tasks SET status = %s, updated_at = %s WHERE id = %s",
            (status, now, task_id),
        )
        cursor.execute(
            "INSERT INTO zx_task_events (task_id, from_status, to_status, note, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (task_id, current.status, status, note[:500], now),
        )
    updated = get_task(store, task_id)
    assert updated is not None
    return updated


def task_events(store: Store, task_id: int | None = None, *, limit: int = 50) -> list[dict[str, object]]:
    sql = (
        "SELECT e.id, e.task_id, e.from_status, e.to_status, e.note, e.created_at, t.title "
        "FROM zx_task_events AS e JOIN zx_tasks AS t ON t.id = e.task_id"
    )
    params: tuple[object, ...] = ()
    if task_id is not None:
        sql += " WHERE e.task_id = %s"
        params = (task_id,)
    sql += " ORDER BY e.id DESC LIMIT %s"
    return store.fetch_all(sql, (*params, limit))


def summary(store: Store) -> dict[str, object]:
    today = date.today()
    tasks = list_tasks(store)
    counts = {status: 0 for status in STATUS_LABELS}
    overdue: list[Task] = []
    due_soon: list[Task] = []
    for task in tasks:
        counts[task.status] = counts.get(task.status, 0) + 1
        if task.status != "done" and task.due_date:
            try:
                due = date.fromisoformat(task.due_date[:10])
            except ValueError:
                continue
            if due < today:
                overdue.append(task)
            elif due <= today + timedelta(days=7):
                due_soon.append(task)
    return {
        "total": len(tasks),
        "counts": counts,
        "counts_label": {STATUS_LABELS[key]: value for key, value in counts.items()},
        "overdue": [task.title for task in overdue],
        "due_soon": [task.title for task in due_soon],
        "completion_rate": round(counts.get("done", 0) / len(tasks), 4) if tasks else 0.0,
    }


def seed_tasks(store: Store, seed_path: Path) -> int:
    """从 JSON 播种任务；按标题去重，重复执行不会重复插入。

    播种数据用于开箱演示，时间戳按截止日期回填到过去一周，周报才有可看的完成项。
    """

    if not seed_path.exists():
        return 0
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    created = 0
    today = date.today()
    for index, entry in enumerate(payload):
        title = str(entry["title"]).strip()
        if not title:
            continue
        existing = store.fetch_one("SELECT id FROM zx_tasks WHERE title = %s", (title,))
        if existing:
            continue
        evidence_document_id = None
        evidence_chunk_id = None
        evidence_path = entry.get("evidence_path")
        if evidence_path:
            document = store.fetch_one("SELECT id FROM zx_documents WHERE path = %s", (evidence_path,))
            if document:
                evidence_document_id = int(document["id"])
                chunk = store.fetch_one(
                    "SELECT id FROM zx_chunks WHERE document_id = %s ORDER BY seq LIMIT 1",
                    (evidence_document_id,),
                )
                if chunk:
                    evidence_chunk_id = int(chunk["id"])
        status = str(entry.get("status", "todo"))
        task = add_task(
            store,
            title,
            status=status,
            priority=str(entry.get("priority", "P2")),
            owner=str(entry.get("owner", "")),
            due=entry.get("due"),
            detail=str(entry.get("detail", "")),
            evidence_document_id=evidence_document_id,
            evidence_chunk_id=evidence_chunk_id,
        )
        created_at = datetime.combine(today - timedelta(days=9), datetime.min.time()) + timedelta(
            hours=9, minutes=23 * index
        )
        event_at = created_at
        if status == "done":
            due_raw = entry.get("due")
            try:
                due_day = date.fromisoformat(str(due_raw)[:10])
            except (TypeError, ValueError):
                due_day = today - timedelta(days=2 + index % 4)
            if due_day > today:
                due_day = today
            event_at = datetime.combine(due_day, datetime.min.time()) + timedelta(hours=17, minutes=30)
        store.execute(
            "UPDATE zx_tasks SET created_at = %s, updated_at = %s WHERE id = %s",
            (created_at, event_at, task.id),
        )
        store.execute(
            "UPDATE zx_task_events SET created_at = %s WHERE task_id = %s",
            (event_at, task.id),
        )
        created += 1
    return created
