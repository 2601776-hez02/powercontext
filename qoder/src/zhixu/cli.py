"""命令行入口：``python -m zhixu <command>``。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

from . import __version__
from .corpus import ingest, iter_markdown, list_documents, seed_aliases
from .digest import generate as generate_digest
from .export_site import DEMO_QUERIES, export_site
from .search import ask as ask_context
from .search import explain_plan, index_inventory, render_evidence_pack, search
from .settings import PROJECT_ROOT, Settings, load_settings
from .store import Store, StoreError
from .tasks import add_task, get_task, list_tasks, seed_tasks, set_status, summary, task_events


def _ensure_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _open(settings: Settings) -> Store:
    store = Store(settings.db_path)
    return store.open()


@contextmanager
def _use_store(settings: Settings):
    store = _open(settings)
    try:
        yield store
    finally:
        store.close()


def _seed_files(settings: Settings) -> tuple[Path, Path]:
    seed_dir = settings.project_root / "data" / "seed"
    return seed_dir / "aliases.json", seed_dir / "tasks.json"


# --------------------------------------------------------------------- init
def cmd_init(args: argparse.Namespace, settings: Settings) -> int:
    if args.reset and settings.data_dir.exists():
        shutil.rmtree(settings.data_dir)
        print(f"已清空数据目录：{settings.data_dir}")

    with _use_store(settings) as store:
        info = store.info()
        print(f"✓ seekdb 已就绪：{info.server_version}")
        print(f"  数据目录：{settings.db_path}")
        print(f"  服务端口：{info.port}（嵌入式实例，随机端口）")

        stats = ingest(store, settings.corpus_dir, rebuild=args.rebuild)
        print(
            f"✓ 资料入库：扫描 {stats.scanned} 篇，新增 {stats.added}、更新 {stats.updated}、"
            f"跳过 {stats.skipped}，写入 {stats.chunks} 个片段"
        )
        alias_seed, task_seed = _seed_files(settings)
        alias_count = seed_aliases(store, alias_seed)
        task_count = seed_tasks(store, task_seed)
        if alias_count:
            print(f"✓ 术语表：{alias_count} 条")
        if task_count:
            print(f"✓ 任务：新增 {task_count} 个")
        docs = store.count("zx_documents")
        chunks = store.count("zx_chunks")
        tasks = summary(store)
        print(f"✓ 当前数据：{docs} 篇资料 / {chunks} 个片段 / {tasks['total']} 个任务")
        print()
        print("下一步：")
        print("  python -m zhixu ask \"HNSW 的 efSearch 怎么影响召回率\"")
        print("  python -m zhixu serve            # 打开本地工作台")
        print("  python -m zhixu export-site      # 生成项目网站数据")
    return 0


# ------------------------------------------------------------------- search
def cmd_search(args: argparse.Namespace, settings: Settings) -> int:
    with _use_store(settings) as store:
        hits, meta = search(store, args.query, limit=args.k)
        if args.json:
            print(
                json.dumps(
                    {
                        "query": args.query,
                        "hits": [hit.as_dict() for hit in hits],
                        "latency_ms": meta.latency_ms,
                        "fts_hits": meta.fts_hits,
                        "vector_hits": meta.vector_hits,
                        "expanded_query": meta.expanded_query,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if not hits:
            print("没有找到资料。可以换一个说法，或先运行 `python -m zhixu init`。")
            return 1
        print(
            f"检索「{args.query}」：{len(hits)} 条结果，{meta.latency_ms:.1f} ms "
            f"（fts {meta.fts_hits} / vector {meta.vector_hits}）"
        )
        if meta.aliases:
            print(f"术语扩展：{'、'.join(meta.aliases)}")
        print()
        for index, hit in enumerate(hits, start=1):
            channels = "+".join(hit.channels)
            print(
                f"[{index}] 相关度 {hit.score:.4f} · {channels} · "
                f"《{hit.document_title}》/ {hit.heading}"
            )
            print(f"    出处：{hit.document_path} 第 {hit.start_line}-{hit.end_line} 行"
                  f"（字符 {hit.char_start}-{hit.char_end}）")
            print(f"    {hit.snippet}")
            print()
    return 0


def cmd_ask(args: argparse.Namespace, settings: Settings) -> int:
    with _use_store(settings) as store:
        result = ask_context(store, args.query, budget_bytes=args.budget)
        if args.json:
            print(
                json.dumps(
                    {
                        "query": args.query,
                        "content": result["content"],
                        "content_bytes": result["content_bytes"],
                        "budget_bytes": result["budget_bytes"],
                        "items": result["items"],
                        "markdown": render_evidence_pack(result),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if not result["items"]:
            print("没有检索到可用证据。")
            return 1
        print(render_evidence_pack(result))
        if args.pack:
            print()
            print("—— 证据包原文（可直接交给模型）——")
            print(result["content"])
    return 0


# -------------------------------------------------------------------- tasks
def cmd_tasks(args: argparse.Namespace, settings: Settings) -> int:
    with _use_store(settings) as store:
        if args.action == "list":
            tasks = list_tasks(store)
            data = summary(store)
            print(
                f"任务 {data['total']} 个："
                + "，".join(f"{label} {count}" for label, count in data["counts_label"].items())
                + f"；完成率 {data['completion_rate'] * 100:.0f}%"
            )
            if data["overdue"]:
                print(f"⚠ 逾期：{'、'.join(data['overdue'])}")
            print()
            for task in tasks:
                due = f" 截止 {task.due_date}" if task.due_date else ""
                print(f"#{task.id:<3} [{task.status_label}] {task.priority} {task.title}{due}")
                if task.detail:
                    print(f"      {task.detail}")
            return 0
        if args.action == "add":
            task = add_task(
                store,
                args.title,
                status=args.status,
                priority=args.priority,
                owner=args.owner,
                due=args.due,
                detail=args.detail,
            )
            print(f"已创建任务 #{task.id}：{task.title}（{task.status_label}）")
            return 0
        if args.action == "status":
            task = set_status(store, args.task_id, args.status, note=args.note)
            print(f"任务 #{task.id} 已更新为「{task.status_label}」")
            print(task.title)
            return 0
        if args.action == "show":
            task = get_task(store, args.task_id)
            if not task:
                print("任务不存在。")
                return 1
            print(f"#{task.id} {task.title}")
            print(f"状态：{task.status_label}  优先级：{task.priority}  截止：{task.due_date or '未设置'}")
            if task.detail:
                print(f"说明：{task.detail}")
            print("事件：")
            for event in task_events(store, task.id, limit=20):
                print(f"  - {event['created_at']} {event['from_status'] or '∅'} → {event['to_status']} {event['note']}")
            return 0
    return 1


# ------------------------------------------------------------------- digest
def cmd_digest(args: argparse.Namespace, settings: Settings) -> int:
    with _use_store(settings) as store:
        result = generate_digest(store, days=args.days, report_dir=settings.report_dir)
        if args.json:
            print(
                json.dumps(
                    {
                        "period_start": result.period_start.isoformat(),
                        "period_end": result.period_end.isoformat(),
                        "markdown": result.markdown,
                        "stats": result.stats,
                        "path": str(result.path) if result.path else None,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        print(result.markdown)
        print()
        print(f"已归档：{result.path}")
    return 0


# -------------------------------------------------------------------- serve
def cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    from .web import run_forever

    store = _open(settings)
    try:
        run_forever(store, settings.site_dir, host=args.host, port=args.port)
    finally:
        store.close()
    return 0


# ------------------------------------------------------------------- doctor
def cmd_doctor(args: argparse.Namespace, settings: Settings) -> int:
    with _use_store(settings) as store:
        info = store.info()
        indexes = index_inventory(store)
        demo_query = "HNSW 的 efSearch 怎么影响召回率和延迟"
        hits, meta = search(store, demo_query, limit=6, log=False)
        plan = explain_plan(store, demo_query)
        report = {
            "version": __version__,
            "engine": "seekdb",
            "engine_version": info.server_version,
            "schema_version": info.schema_version,
            "db_path": str(settings.db_path),
            "counts": {
                "documents": store.count("zx_documents"),
                "chunks": store.count("zx_chunks"),
                "tasks": store.count("zx_tasks"),
                "events": store.count("zx_task_events"),
                "searches": store.count("zx_searches"),
            },
            "indexes": indexes,
            "demo_query": demo_query,
            "demo_hits": len(hits),
            "demo_channels": {channel: sum(1 for hit in hits if channel in hit.channels) for channel in ("fts", "vector")},
            "explain": plan,
            "checks": {
                "fts_index": any(item["index"] == "ft_body" for item in indexes),
                "vector_index": any(item["index"] == "vk_embedding" for item in indexes),
                "fts_hits": meta.fts_hits > 0,
                "vector_hits": meta.vector_hits > 0,
                "documents": store.count("zx_documents") > 0,
                "tasks": store.count("zx_tasks") > 0,
            },
        }
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        print(f"引擎：seekdb {info.server_version}（schema v{info.schema_version}）")
        print(f"数据：{settings.db_path}")
        print(
            f"规模：{report['counts']['documents']} 篇资料 / {report['counts']['chunks']} 个片段 / "
            f"{report['counts']['tasks']} 个任务"
        )
        print("索引：")
        for item in indexes:
            print(f"  - {item['index']}（{', '.join(item['columns'])}，{item['kind']}）")
        print(f"演示检索：{len(hits)} 条（fts {meta.fts_hits} / vector {meta.vector_hits}，{meta.latency_ms:.1f} ms）")
        print("查询计划（向量检索）：")
        for line in plan:
            print("  " + line)
        print("自检：")
        for name, ok in report["checks"].items():
            print(f"  - {name}: {'✓' if ok else '✗'}")
        return 0 if all(report["checks"].values()) else 1


# --------------------------------------------------------------- export-site
def cmd_export(args: argparse.Namespace, settings: Settings) -> int:
    with _use_store(settings) as store:
        if store.count("zx_documents") == 0:
            print("资料库为空，先运行 `python -m zhixu init`。")
            return 1
        stats = ingest(store, settings.corpus_dir)
        docs = list_documents(store)
        first_document = str(docs[0]["path"]) if docs else None
        result = export_site(
            store,
            settings.project_root,
            settings.site_dir,
            ingest_stats=stats.as_dict(),
            first_document=first_document,
        )
        print(f"✓ 站点数据已生成：{result['path']}（{result['bytes'] / 1024:.1f} KB）")
        print(
            f"  资料 {result['stats']['documents']} 篇 / 片段 {result['stats']['chunks']} 个 / "
            f"任务 {result['stats']['tasks']} 个"
        )
        bench = result["benchmark"]
        print(f"  检索基准：中位 {bench['median_ms']} ms，P95 {bench['p95_ms']} ms（{bench['samples']} 次）")
        print("  打开方式：`python -m zhixu serve` 后访问 http://127.0.0.1:8765",
              )
    return 0


# --------------------------------------------------------------------- demo
def cmd_demo(args: argparse.Namespace, settings: Settings) -> int:
    with _use_store(settings) as store:
        if store.count("zx_documents") == 0:
            stats = ingest(store, settings.corpus_dir)
            alias_seed, task_seed = _seed_files(settings)
            seed_aliases(store, alias_seed)
            seed_tasks(store, task_seed)
            print(f"首次初始化：入库 {stats.added} 篇资料，{stats.chunks} 个片段")
        print("=" * 72)
        print("1) 混合检索（全文 + 向量）")
        print("=" * 72)
        for entry in DEMO_QUERIES[:2]:
            result = ask_context(store, entry["query"], budget_bytes=1800)
            print(render_evidence_pack(result))
            print()
        print("=" * 72)
        print("2) 周报自动汇总")
        print("=" * 72)
        digest = generate_digest(store, report_dir=None, save=False)
        print(digest.markdown[:1200] + ("…" if len(digest.markdown) > 1200 else ""))
        print()
        print(f"任务概览：{summary(store)['counts_label']}")
    return 0


# --------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zhixu",
        description="知序 ZhiXu —— 基于嵌入式 seekdb 的学研工作台",
    )
    parser.add_argument("--version", action="version", version=f"zhixu {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="初始化数据库并入库资料")
    init.add_argument("--reset", action="store_true", help="先清空本地数据目录")
    init.add_argument("--rebuild", action="store_true", help="强制重建全部片段")
    init.set_defaults(func=cmd_init)

    ingest_cmd = sub.add_parser("ingest", help="把语料同步进 seekdb")
    ingest_cmd.add_argument("--rebuild", action="store_true", help="强制重建全部片段")

    def _ingest(args: argparse.Namespace, settings: Settings) -> int:
        with _use_store(settings) as store:
            stats = ingest(store, settings.corpus_dir, rebuild=args.rebuild)
            print(
                f"扫描 {stats.scanned} 篇：新增 {stats.added}，更新 {stats.updated}，"
                f"跳过 {stats.skipped}，写入 {stats.chunks} 个片段"
            )
        return 0

    ingest_cmd.set_defaults(func=_ingest)

    search_cmd = sub.add_parser("search", help="检索资料并显示出处")
    search_cmd.add_argument("query")
    search_cmd.add_argument("-k", type=int, default=6, help="返回条数（默认 6）")
    search_cmd.add_argument("--json", action="store_true")
    search_cmd.set_defaults(func=cmd_search)

    ask_cmd = sub.add_parser("ask", help="生成带出处和字节预算的证据包")
    ask_cmd.add_argument("query")
    ask_cmd.add_argument("--budget", type=int, default=1800, help="UTF-8 字节预算（默认 1800）")
    ask_cmd.add_argument("--json", action="store_true")
    ask_cmd.add_argument("--pack", action="store_true", help="额外打印证据包原文")
    ask_cmd.set_defaults(func=cmd_ask)

    tasks_cmd = sub.add_parser("tasks", help="任务板")
    tasks_sub = tasks_cmd.add_subparsers(dest="action", required=True)
    tasks_list = tasks_sub.add_parser("list", help="列出任务")
    tasks_list.set_defaults(func=cmd_tasks, action="list")
    tasks_add = tasks_sub.add_parser("add", help="新增任务")
    tasks_add.add_argument("title")
    tasks_add.add_argument("--status", default="todo", choices=["todo", "doing", "blocked", "done"])
    tasks_add.add_argument("--priority", default="P2", choices=["P0", "P1", "P2", "P3"])
    tasks_add.add_argument("--owner", default="")
    tasks_add.add_argument("--due", default=None, help="截止日期 YYYY-MM-DD")
    tasks_add.add_argument("--detail", default="")
    tasks_add.set_defaults(func=cmd_tasks, action="add")
    tasks_status = tasks_sub.add_parser("status", help="更新任务状态")
    tasks_status.add_argument("task_id", type=int)
    tasks_status.add_argument("status", choices=["todo", "doing", "blocked", "done"])
    tasks_status.add_argument("--note", default="")
    tasks_status.set_defaults(func=cmd_tasks, action="status")
    tasks_show = tasks_sub.add_parser("show", help="查看任务与事件")
    tasks_show.add_argument("task_id", type=int)
    tasks_show.set_defaults(func=cmd_tasks, action="show")

    digest_cmd = sub.add_parser("digest", help="生成周报")
    digest_cmd.add_argument("--days", type=int, default=7)
    digest_cmd.add_argument("--json", action="store_true")
    digest_cmd.set_defaults(func=cmd_digest)

    serve_cmd = sub.add_parser("serve", help="启动本地工作台")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8765)
    serve_cmd.set_defaults(func=cmd_serve)

    doctor_cmd = sub.add_parser("doctor", help="自检 seekdb、索引与检索")
    doctor_cmd.add_argument("--json", action="store_true")
    doctor_cmd.set_defaults(func=cmd_doctor)

    export_cmd = sub.add_parser("export-site", help="导出项目网站演示数据")
    export_cmd.set_defaults(func=cmd_export)

    demo_cmd = sub.add_parser("demo", help="一键演示检索与周报")
    demo_cmd.set_defaults(func=cmd_demo)

    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8()
    settings = load_settings()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args, settings))
    except StoreError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
