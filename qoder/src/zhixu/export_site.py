"""把真实运行结果导出成项目网站的演示数据。

站点上的每一条检索结果、每一个任务、每一份周报都来自本机 seekdb 的实际查询，
导出之后静态站点不再依赖后端，也不会出现「展示内容与代码不对应」的问题。
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import __version__
from .corpus import list_documents
from .digest import generate as generate_digest
from .search import ask as ask_context
from .search import explain_plan, index_inventory, search
from .store import Store
from .tasks import list_tasks, summary

DEMO_QUERIES: tuple[dict[str, str], ...] = (
    {
        "query": "HNSW 的 efSearch 怎么影响召回率和延迟",
        "intent": "调参时快速定位实验记录",
    },
    {
        "query": "中文资料检索为什么要用 ngram 分词",
        "intent": "写方案时找出处",
    },
    {
        "query": "证据召回率怎么评测，字节预算怎么算",
        "intent": "准备评测口径",
    },
    {
        "query": "周报里应该写哪些内容",
        "intent": "把重复整理报表变成一条命令",
    },
    {
        "query": "项目里程碑有哪些风险",
        "intent": "例会前确认进度",
    },
    {
        "query": "分块大小对检索效果的影响",
        "intent": "复盘实验结论",
    },
)


def _git(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return completed.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _sanitize_remote(url: str) -> str:
    url = re.sub(r"https://[^/@]+@", "https://", url)
    if url.endswith(".git"):
        url = url[:-4]
    return url


def repo_info(project_root: Path) -> dict[str, str]:
    branch = _git(project_root, "rev-parse", "--abbrev-ref", "HEAD")
    remote = _git(project_root, "remote", "get-url", "ghfork") or _git(project_root, "remote", "get-url", "origin")
    return {
        "branch": branch or "unknown",
        "remote": _sanitize_remote(remote) if remote else "https://github.com/2601776-hez02/powercontext",
    }


def benchmark(store: Store, queries: list[str], rounds: int = 5) -> dict[str, object]:
    timings: list[float] = []
    for _ in range(rounds):
        for query in queries:
            started = time.perf_counter()
            search(store, query, limit=6, log=False)
            timings.append((time.perf_counter() - started) * 1000.0)
    timings.sort()
    return {
        "rounds": rounds,
        "queries": len(queries),
        "samples": len(timings),
        "median_ms": round(statistics.median(timings), 2),
        "p95_ms": round(timings[min(len(timings) - 1, int(len(timings) * 0.95))], 2),
        "max_ms": round(timings[-1], 2),
    }


def export_site(
    store: Store,
    project_root: Path,
    site_dir: Path,
    *,
    ingest_stats: dict[str, object] | None = None,
    first_document: str | None = None,
) -> dict[str, object]:
    generated_at = datetime.now().replace(microsecond=0).isoformat(sep=" ")
    info = store.info()
    documents = list_documents(store)
    tasks = list_tasks(store)
    task_summary = summary(store)

    demos: list[dict[str, object]] = []
    for entry in DEMO_QUERIES:
        result = ask_context(store, entry["query"], budget_bytes=1800)
        demos.append(
            {
                "query": entry["query"],
                "intent": entry["intent"],
                "content": result["content"],
                "content_bytes": result["content_bytes"],
                "budget_bytes": result["budget_bytes"],
                "items": result["items"],
                "latency_ms": result["meta"].latency_ms,
                "fts_hits": result["meta"].fts_hits,
                "vector_hits": result["meta"].vector_hits,
                "aliases": list(result["meta"].aliases),
            }
        )

    digest = generate_digest(store, report_dir=None, save=False)
    stats = {
        "documents": store.count("zx_documents"),
        "chunks": store.count("zx_chunks"),
        "tasks": store.count("zx_tasks"),
        "events": store.count("zx_task_events"),
        "searches": store.count("zx_searches"),
    }

    perf = benchmark(store, [entry["query"] for entry in DEMO_QUERIES])
    indexes = index_inventory(store)
    index_details = {
        "PRIMARY": "id",
        "ix_doc_seq": "document_id, seq",
        "ft_body": "body · ngram 解析器",
        "vk_embedding": "embedding · 384 维 · COSINE",
    }
    for item in indexes:
        item["detail"] = index_details.get(item["index"], ", ".join(item["columns"]))
    evidence = {
        "engine": "seekdb",
        "engine_version": info.server_version,
        "schema_version": info.schema_version,
        "indexes": indexes,
        "explain": explain_plan(store, DEMO_QUERIES[0]["query"]),
        "benchmark": perf,
        "ingest": ingest_stats or {},
        "checks": {
            "fts_index": any(item["index"] == "ft_body" for item in indexes),
            "vector_index": any(item["index"] == "vk_embedding" for item in indexes),
            "documents": stats["documents"] > 0,
            "chunks": stats["chunks"] > 0,
            "tasks": stats["tasks"] > 0,
        },
    }

    payload = {
        "schema": "zhixu.site.v1",
        "generated_at": generated_at,
        "version": __version__,
        "repo": repo_info(project_root),
        "stats": stats,
        "store": {
            "engine": info.server_version,
            "path": str(info.path).replace(str(project_root), "."),
            "schema_version": info.schema_version,
        },
        "documents": documents,
        "tasks": [
            {
                "id": task.id,
                "title": task.title,
                "status": task.status,
                "status_label": task.status_label,
                "priority": task.priority,
                "owner": task.owner,
                "due_date": task.due_date,
                "detail": task.detail,
                "evidence_document_id": task.evidence_document_id,
                "updated_at": task.updated_at,
            }
            for task in tasks
        ],
        "task_summary": task_summary,
        "digest": {
            "period_start": digest.period_start.isoformat(),
            "period_end": digest.period_end.isoformat(),
            "markdown": digest.markdown,
            "stats": digest.stats,
        },
        "demos": demos,
        "evidence": evidence,
        "first_document": first_document,
    }

    target = site_dir / "data" / "demo.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "path": str(target),
        "bytes": target.stat().st_size,
        "generated_at": generated_at,
        "stats": stats,
        "benchmark": perf,
    }
