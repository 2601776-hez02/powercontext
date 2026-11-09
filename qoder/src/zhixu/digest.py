"""周报自动汇总：从任务事件、资料入库和检索日志里折叠出一份可交付的周报。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .store import Store
from .tasks import STATUS_LABELS, list_tasks

PERIOD_DAYS = 7


@dataclass
class DigestResult:
    markdown: str
    period_start: date
    period_end: date
    path: Path | None
    stats: dict[str, object]


def _fmt_time(value: object) -> str:
    if isinstance(value, datetime):
        return value.strftime("%m-%d %H:%M")
    text = str(value)
    return text[5:16] if len(text) >= 16 else text


def _fmt_date(value: object) -> str:
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def generate(
    store: Store,
    *,
    days: int = PERIOD_DAYS,
    report_dir: Path | None = None,
    save: bool = True,
) -> DigestResult:
    today = date.today()
    start_date = today - timedelta(days=days - 1)
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(today, datetime.max.time()).replace(microsecond=0)
    start_text = start_dt.isoformat(sep=" ")
    end_text = end_dt.isoformat(sep=" ")

    doc_total = store.count("zx_documents")
    chunk_total = store.count("zx_chunks")
    new_documents = store.fetch_all(
        "SELECT path, title, kind, created_at FROM zx_documents "
        "WHERE created_at >= %s AND created_at <= %s ORDER BY created_at, id",
        (start_text, end_text),
    )
    completed = store.fetch_all(
        "SELECT t.id, t.title, t.priority, e.created_at AS done_at, d.title AS doc_title, "
        "       c.start_line, c.end_line, c.document_id "
        "FROM zx_task_events AS e JOIN zx_tasks AS t ON t.id = e.task_id "
        "LEFT JOIN zx_chunks AS c ON c.id = t.evidence_chunk_id "
        "LEFT JOIN zx_documents AS d ON d.id = c.document_id "
        "WHERE e.to_status = 'done' AND e.created_at >= %s AND e.created_at <= %s "
        "ORDER BY e.created_at, t.id",
        (start_text, end_text),
    )
    created_tasks = store.fetch_all(
        "SELECT id, title, status, priority FROM zx_tasks "
        "WHERE created_at >= %s AND created_at <= %s ORDER BY id",
        (start_text, end_text),
    )
    tasks = list_tasks(store)
    blocked = [task for task in tasks if task.status == "blocked"]
    doing = [task for task in tasks if task.status == "doing"]
    search_stats = store.fetch_one(
        "SELECT COUNT(*) AS n FROM zx_searches WHERE created_at >= %s AND created_at <= %s",
        (start_text, end_text),
    )
    hot_queries = store.fetch_all(
        "SELECT query, COUNT(*) AS n FROM zx_searches "
        "WHERE created_at >= %s AND created_at <= %s GROUP BY query ORDER BY n DESC, query LIMIT 5",
        (start_text, end_text),
    )
    counts = {status: sum(1 for task in tasks if task.status == status) for status in STATUS_LABELS}

    lines: list[str] = []
    lines.append(f"# 学研周报 · {start_date:%Y-%m-%d} ~ {today:%Y-%m-%d}")
    lines.append("")
    lines.append("> 由知序自动汇总；数据源为本地 seekdb 库（zx_documents / zx_chunks / zx_tasks / zx_task_events / zx_searches）。")
    lines.append("")
    lines.append("## 一、概览")
    lines.append("")
    lines.append(f"- 资料：本周期新增 **{len(new_documents)}** 篇，累计 {doc_total} 篇 / {chunk_total} 个片段。")
    lines.append(
        f"- 任务：完成 **{len(completed)}** 个，进行中 {len(doing)} 个，阻塞 {len(blocked)} 个，"
        f"累计 {len(tasks)} 个（完成率 {round(counts['done'] / len(tasks) * 100, 1) if tasks else 0}%）。"
    )
    lines.append(f"- 检索：本周期 **{(search_stats or {}).get('n', 0)}** 次，平均每次都会带回原文出处。")
    lines.append("")
    lines.append("## 二、本周完成")
    lines.append("")
    if completed:
        for row in completed:
            evidence = ""
            if row.get("doc_title"):
                evidence = f" — 证据：《{row['doc_title']}》第 {row['start_line']}-{row['end_line']} 行"
            lines.append(f"- [x] {row['title']}（{row['priority']}，完成于 {_fmt_time(row['done_at'])}）{evidence}")
    else:
        lines.append("- 本周期还没有标记完成的任务。")
    lines.append("")
    lines.append("## 三、进行中")
    lines.append("")
    if doing:
        for task in doing:
            due = f"，截止 {task.due_date}" if task.due_date else ""
            lines.append(f"- [ ] {task.title}（{task.priority}{due}）{('：' + task.detail) if task.detail else ''}")
    else:
        lines.append("- 没有进行中的任务。")
    lines.append("")
    lines.append("## 四、风险与阻塞")
    lines.append("")
    if blocked:
        for task in blocked:
            lines.append(f"- ⚠ {task.title}（{task.priority}）{('：' + task.detail) if task.detail else ''}")
    else:
        lines.append("- 暂无阻塞项。")
    lines.append("")
    lines.append("## 五、资料沉淀")
    lines.append("")
    if new_documents:
        for row in new_documents:
            lines.append(f"- {row['title']}（{row['kind']}，{row['path']}）")
    else:
        lines.append("- 本周期没有新增资料。")
    lines.append("")
    lines.append("## 六、下周建议")
    lines.append("")
    pending = sorted(
        [task for task in tasks if task.status != "done"],
        key=lambda task: (task.due_date or "9999-99-99", task.priority),
    )
    if pending:
        for task in pending[:5]:
            due = f"（截止 {task.due_date}）" if task.due_date else ""
            lines.append(f"- {task.title}{due}")
    else:
        lines.append("- 所有任务已完成，可以整理归档或规划下一阶段。")
    if hot_queries:
        lines.append("")
        lines.append("## 附：热门检索")
        lines.append("")
        for row in hot_queries:
            lines.append(f"- {row['query']}（{row['n']} 次）")
    lines.append("")
    lines.append(f"_生成时间：{datetime.now():%Y-%m-%d %H:%M}；本周期新增任务 {len(created_tasks)} 个。_")

    markdown = "\n".join(lines)
    output_path: Path | None = None
    if save:
        store.execute(
            "INSERT INTO zx_digests (period_start, period_end, markdown, created_at) "
            "VALUES (%s, %s, %s, %s)",
            (start_date.isoformat(), today.isoformat(), markdown, datetime.now().replace(microsecond=0)),
        )
    if report_dir is not None:
        report_dir.mkdir(parents=True, exist_ok=True)
        output_path = report_dir / f"weekly-{today.isoformat()}.md"
        output_path.write_text(markdown, encoding="utf-8")

    stats = {
        "completed": len(completed),
        "doing": len(doing),
        "blocked": len(blocked),
        "new_documents": len(new_documents),
        "searches": int((search_stats or {}).get("n", 0)),
        "documents_total": doc_total,
        "chunks_total": chunk_total,
        "tasks_total": len(tasks),
    }
    return DigestResult(
        markdown=markdown,
        period_start=start_date,
        period_end=today,
        path=output_path,
        stats=stats,
    )
