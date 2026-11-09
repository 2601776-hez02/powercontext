"""Command line entry point: ``python -m sdd_context <command>``.

The CLI is the module's explicit entry point. Every command prints a short
human summary and supports ``--json`` for machines; long-running commands write
their evidence under ``records/`` and ``artifacts/``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .agent import MiniMaxClient, MiniMaxError, run_agent
from .config import Settings, load_settings
from .context_pack import build_pack, summarise, write_pack
from .importer import (
    DEFAULT_CODE_GLOBS,
    DEFAULT_SPEC_GLOBS,
    DEFAULT_TEST_GLOBS,
    import_repository,
)
from .records import append_event, environment, write_json
from .retrieval import certify, load_task, select_materials
from .seekdb_store import SeekDBStore, SeekDBUnavailable
from .state import PowerContextError, TaskStateStore
from .testing import prepare_workdir, run_pytest, write_test_log


def _settings() -> Settings:
    return load_settings()


def _print(value: Any) -> None:
    print(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_init(args: argparse.Namespace, settings: Settings) -> int:
    settings.home.mkdir(parents=True, exist_ok=True)
    with SeekDBStore(settings.seekdb) as store:
        schema = store.initialize()
        stats = store.stats()
    payload = {"settings": _settings_view(settings), "seekdb": schema, "stats": stats}
    write_json(settings.records_dir / "init.json", payload)
    if args.json:
        _print(payload)
    else:
        print(f"home            {settings.home}")
        print(f"seekdb          {settings.seekdb.label}")
        print(f"server version  {schema['server_version']}")
        print(f"tables          {', '.join(schema['tables'])}")
        print(f"fulltext index  {schema['fulltext_index']} ({'created' if schema['fulltext_created'] else 'present'})")
    return 0


def cmd_import(args: argparse.Namespace, settings: Settings) -> int:
    repo = Path(args.repo).expanduser().resolve()
    result = import_repository(
        settings,
        repo,
        spec_globs=tuple(args.specs or DEFAULT_SPEC_GLOBS),
        code_globs=tuple(args.code or DEFAULT_CODE_GLOBS),
        test_globs=tuple(args.tests or DEFAULT_TEST_GLOBS),
        notes=args.notes or "",
    )
    payload = result.as_dict()
    if args.json:
        _print(payload)
        return 0
    print(f"repository      {result.repo}")
    print(f"seekdb          {result.target} (server {result.server_version})")
    print(f"documents       {result.documents} · items {result.items} {result.by_kind}")
    print(f"spec revision   {result.revisions['spec'][:16]}")
    print(f"code revision   {result.revisions['code'][:16]}")
    print(f"test revision   {result.revisions['test'][:16]}")
    print(f"change journal  {result.changes}")
    for row in result.changed_documents[:10]:
        if row["change"] == "unchanged":
            continue
        print(f"  {row['change']:8s} {row['path']}  {row['before'] or '-'} -> {row['after'] or '-'}")
    return 0


def cmd_context(args: argparse.Namespace, settings: Settings) -> int:
    task = load_task(Path(args.task))
    repo = Path(args.repo).expanduser().resolve() if args.repo else settings.sample_repo
    with SeekDBStore(settings.seekdb) as store:
        stats = store.stats()
        index = store.latest_import()
        selection = select_materials(store, task, spec_limit=args.spec_limit, code_limit=args.code_limit,
                                     test_limit=args.test_limit)

    state: dict[str, Any] | None = None
    state_error = ""
    if not args.no_state:
        try:
            state = TaskStateStore(settings.powercontext_url, settings.powercontext_token).load(task.task_id)
        except PowerContextError as exc:
            state_error = str(exc)
            if not args.allow_missing_state:
                print(f"error: PowerContext state unavailable: {exc}", file=sys.stderr)
                print("hint: start the Server (scripts/start-powercontext.sh) or pass --no-state", file=sys.stderr)
                return 3

    pack = build_pack(selection, repo_root=repo, state=state, index=index, db=stats)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dir = settings.records_dir / "runs" / f"{task.task_id}-{stamp}"
    json_path = Path(args.json_out) if args.json_out else run_dir / "context-pack.json"
    md_path = Path(args.markdown_out) if args.markdown_out else run_dir / "context-pack.md"
    write_pack(pack, json_path=json_path, markdown_path=md_path)

    capture: dict[str, Any] | None = None
    if args.capture and index and not args.no_state:
        try:
            capture = TaskStateStore(settings.powercontext_url, settings.powercontext_token).capture_context_source(
                task_id=task.task_id,
                import_id=index["import_id"],
                content=json.dumps(pack.as_dict(), ensure_ascii=False),
                metadata={
                    "title": task.title,
                    "spec_revision": index["spec_revision"],
                    "code_revision": index["code_revision"],
                    "items": len(pack.selection.specs) + len(pack.selection.code) + len(pack.selection.tests),
                },
            )
        except PowerContextError as exc:
            state_error = state_error or str(exc)

    summary = {
        "task": task.task_id,
        "pack": str(json_path),
        "markdown": str(md_path),
        "selected": summarise(selection),
        "checks": pack.checks,
        "state": state,
        "state_error": state_error,
        "capture": capture,
        "db": stats,
        "index": index,
    }
    write_json(run_dir / "context-summary.json", summary)
    append_event(
        settings.records_dir,
        "context",
        {
            "task": task.task_id,
            "pack": str(json_path),
            "specs": [item.hit["item_ref"] for item in selection.specs],
            "code": [item.hit["item_ref"] for item in selection.code],
            "tests": [item.hit["item_ref"] for item in selection.tests],
            "missing": pack.checks.get("missing"),
            "source_status": pack.checks.get("source_status"),
            "state_revision": (state or {}).get("revision"),
            "capture": capture,
        },
    )

    if args.json:
        _print(pack.as_dict())
        return 0 if pack.checks.get("ok") or not args.check else 2

    print(f"task            {task.task_id}  {task.title}")
    print(f"repository      {repo}")
    print(f"index           {index['import_id'] if index else 'none'} · spec {index['spec_revision'][:12] if index else ''}")
    for family, rows in (("specs", selection.specs), ("code", selection.code), ("tests", selection.tests)):
        print(f"{family:15s} {len(rows)}")
        for item in rows:
            print(f"  {item.score:5.2f}  {item.hit['item_ref']}")
    if state:
        print(f"task state      revision {state.get('revision')} · {state.get('status')} · "
              f"done {len(state.get('done', []))} · todo {len(state.get('todo', []))} · "
              f"verifications {len(state.get('verifications', []))}")
        if state.get("next_step"):
            print(f"next step       {state['next_step']}")
    elif state_error:
        print(f"task state      unavailable ({state_error})")
    else:
        print("task state      skipped (--no-state)")
    print(f"source checks   {pack.checks.get('source_status')}")
    if pack.checks.get("missing"):
        print(f"missing sources {pack.checks['missing']}")
    print(f"pack            {json_path}")
    print(f"markdown        {md_path}")
    if capture:
        print(f"pc source       {capture.get('source_id')} ({capture.get('status')})")
    if args.check and not pack.checks.get("ok"):
        print("check failed: the pack is missing sources declared by the task", file=sys.stderr)
        return 2
    return 0


def cmd_state(args: argparse.Namespace, settings: Settings) -> int:
    task = load_task(Path(args.task))
    store = TaskStateStore(settings.powercontext_url, settings.powercontext_token)
    if args.action == "show":
        state = store.load(task.task_id)
        if args.json:
            _print(state or {})
        elif state is None:
            print(f"no state stored for {task.task_id}")
        else:
            print(f"task            {state.get('task_id')}  {state.get('title')}")
            print(f"status          {state.get('status')}")
            print(f"revision        {state.get('revision')}")
            print(f"updated         {state.get('updated_at')}")
            print(f"scope           {store.scope_id()}")
            for label in ("done", "todo"):
                items = state.get(label) or []
                print(f"{label:15s} {len(items)}")
                for item in items:
                    print(f"  - {item}")
            verifications = state.get("verifications") or []
            print(f"{'verifications':15s} {len(verifications)}")
            for entry in verifications:
                if isinstance(entry, dict):
                    print(f"  - {entry.get('command')} -> exit {entry.get('exit_code')} ({entry.get('summary')})")
            if state.get("next_step"):
                print(f"next step       {state['next_step']}")
        return 0

    verification = None
    if args.verification:
        verification = json.loads(Path(args.verification).read_text(encoding="utf-8"))
    elif args.verification_status:
        verification = {
            "command": args.verification_command or "",
            "exit_code": args.verification_exit_code,
            "summary": args.verification_status,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    state, result = store.remember_state(
        task.task_id,
        task.title,
        status=args.status,
        done=args.done or None,
        todo=args.todo or None,
        next_step=args.next_step,
        verification=verification,
    )
    payload = {
        "state": state,
        "memory": result.memory_reference,
        "entry_version": result.entry_version,
        "scope_id": result.scope_id,
        "bytes": result.bytes_written,
    }
    write_json(settings.records_dir / "powercontext" / f"state-{task.task_id}-r{state['revision']}.json", payload)
    append_event(settings.records_dir, "state-save",
                 {"task": task.task_id, "revision": state["revision"], "status": state["status"],
                  "memory": result.memory_reference, "entry_version": result.entry_version})
    if args.json:
        _print(payload)
    else:
        print(f"saved           {task.task_id} revision {state['revision']} "
              f"({result.bytes_written} bytes, entry version {result.entry_version})")
        print(f"memory          {result.memory_reference.get('artifact_id')} "
              f"@{result.memory_reference.get('revision')}")
        print(f"scope           {result.scope_id}")
    return 0


def cmd_agent(args: argparse.Namespace, settings: Settings) -> int:
    task = load_task(Path(args.task))
    repo = settings.sample_repo
    with SeekDBStore(settings.seekdb) as store:
        stats = store.stats()
        index = store.latest_import()
        selection = select_materials(store, task)
    state = None
    try:
        state = TaskStateStore(settings.powercontext_url, settings.powercontext_token).load(task.task_id)
    except PowerContextError as exc:
        if not args.allow_missing_state:
            print(f"error: PowerContext state unavailable: {exc}", file=sys.stderr)
            return 3
    pack = build_pack(selection, repo_root=repo, state=state, index=index, db=stats)
    client = MiniMaxClient(
        api_key=settings.minimax_api_key or "",
        base_url=settings.minimax_base_url,
        model=settings.minimax_model,
        style=settings.minimax_api_style,
        group_id=settings.minimax_group_id,
    )
    run = run_agent(
        pack=pack,
        workdir=settings.workdir,
        client=client,
        records_dir=settings.records_dir,
        artifacts_dir=settings.artifacts_dir,
        max_attempts=args.attempts,
        apply_changes=not args.no_apply,
    )
    payload = run.as_dict()
    write_json(settings.records_dir / "minimax" / task.task_id / "summary.json", payload)
    append_event(settings.records_dir, "agent",
                 {"task": task.task_id, "success": run.success, "attempts": len(run.attempts),
                  "baseline_exit": run.baseline_acceptance.exit_code,
                  "acceptance_exit": run.acceptance_after.exit_code if run.acceptance_after else None,
                  "regression_exit": run.regression_after.exit_code if run.regression_after else None})
    if args.json:
        _print(payload)
    else:
        print(f"task            {task.task_id}")
        print(f"baseline        {run.baseline_acceptance.summary} (exit {run.baseline_acceptance.exit_code})")
        for attempt in run.attempts:
            state_text = "ok" if attempt.applied else attempt.error or "not applied"
            acceptance = attempt.acceptance.summary if attempt.acceptance else "-"
            print(f"attempt {attempt.number}     patch {attempt.patch_chars} chars · {state_text} · acceptance {acceptance}")
        if run.acceptance_after:
            print(f"acceptance      {run.acceptance_after.summary} (exit {run.acceptance_after.exit_code})")
        if run.regression_after:
            print(f"regression      {run.regression_after.summary} (exit {run.regression_after.exit_code})")
        print(f"result          {'SUCCESS' if run.success else 'FAILED'} in {run.elapsed_s:.1f}s")
    return 0 if run.success else 1


def cmd_verify(args: argparse.Namespace, settings: Settings) -> int:
    task = load_task(Path(args.task))
    workdir = Path(args.workdir) if args.workdir else settings.workdir
    if args.prepare or not workdir.exists():
        prepare_workdir(settings.sample_repo, workdir, reset=args.reset)
    targets = args.targets or task.acceptance_tests + task.regression_tests
    run = run_pytest(workdir, targets)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    log_path = settings.records_dir / "runs" / f"{task.task_id}-{stamp}" / "tests.log"
    write_test_log(log_path, run)
    store = TaskStateStore(settings.powercontext_url, settings.powercontext_token)
    state, result = store.remember_state(
        task.task_id,
        task.title,
        status="verified" if run.exit_code == 0 else "in_progress",
        verification={**run.as_dict(), "targets": list(targets), "log": str(log_path)},
    )
    record = {
        "state": {key: value for key, value in state.items() if not key.startswith("_")},
        "memory": result.memory_reference,
        "entry_version": result.entry_version,
        "scope_id": result.scope_id,
        "tests": run.as_dict(),
        "log": str(log_path),
    }
    write_json(
        settings.records_dir / "powercontext" / f"verification-{task.task_id}-r{state['revision']}.json",
        record,
    )
    append_event(settings.records_dir, "verify",
                 {"task": task.task_id, "exit_code": run.exit_code, "summary": run.summary,
                  "log": str(log_path), "state_revision": state["revision"]})
    payload = {"tests": run.as_dict(), "log": str(log_path), "state_revision": state["revision"],
               "memory": result.memory_reference}
    if args.json:
        _print(payload)
    else:
        print(f"tests           {run.summary} (exit {run.exit_code})")
        print(f"log             {log_path}")
        print(f"state           revision {state['revision']} · status {state['status']}")
    return run.exit_code


def cmd_db(args: argparse.Namespace, settings: Settings) -> int:
    with SeekDBStore(settings.seekdb) as store:
        if args.action == "stats":
            payload = store.stats()
            if args.json:
                _print(payload)
            else:
                for key, value in payload.items():
                    print(f"{key:16s} {value}")
            return 0
        store.initialize()
        rows = store.query(args.sql, tuple(args.param or ()), purpose="manual-query")
        payload = {"sql": args.sql, "params": args.param or [], "rows": rows, "row_count": len(rows)}
        if args.json:
            _print(payload)
        else:
            for row in rows:
                print(" | ".join(str(value) for value in row))
            print(f"({len(rows)} rows)")
        return 0


def cmd_doctor(args: argparse.Namespace, settings: Settings) -> int:
    report: dict[str, Any] = {"version": __version__, "settings": _settings_view(settings),
                              "environment": environment()}
    try:
        with SeekDBStore(settings.seekdb) as store:
            report["seekdb"] = store.initialize()
            report["seekdb"]["stats"] = store.stats()
    except (SeekDBUnavailable, Exception) as exc:  # noqa: BLE001 - doctor reports instead of raising
        report["seekdb"] = {"error": str(exc)}
    try:
        report["powercontext"] = TaskStateStore(settings.powercontext_url, settings.powercontext_token).doctor()
    except PowerContextError as exc:
        report["powercontext"] = {"error": str(exc)}
    report["minimax"] = {"api_key": bool(settings.minimax_api_key), "base_url": settings.minimax_base_url,
                         "model": settings.minimax_model, "style": settings.minimax_api_style,
                         "group_id": settings.minimax_group_id}
    report["sample_repo"] = {"path": str(settings.sample_repo), "exists": settings.sample_repo.is_dir()}
    if args.json:
        _print(report)
        return 0
    print(f"sdd context     {__version__}")
    print(f"home            {settings.home}")
    seekdb = report["seekdb"]
    if "error" in seekdb:
        print(f"seekdb          ERROR {seekdb['error']}")
    else:
        print(f"seekdb          {settings.seekdb.label} · server {seekdb.get('server_version')} · ready")
    pc = report["powercontext"]
    print(f"powercontext    {settings.powercontext_url} · {pc.get('status')}"
          + ("" if pc.get("status") == "ready" else f" ({pc.get('detail')})"))
    print(f"minimax         {'key present' if settings.minimax_api_key else 'NO API KEY'} · "
          f"{settings.minimax_model} @ {settings.minimax_base_url}"
          + (f" · GroupId {settings.minimax_group_id}" if settings.minimax_group_id else ""))
    print(f"sample          {settings.sample_repo} ({'ok' if settings.sample_repo.is_dir() else 'missing'})")
    return 0


def _settings_view(settings: Settings) -> dict[str, Any]:
    return {
        "home": str(settings.home),
        "records": str(settings.records_dir),
        "artifacts": str(settings.artifacts_dir),
        "sample_repo": str(settings.sample_repo),
        "workdir": str(settings.workdir),
        "seekdb": {"kind": settings.seekdb.kind, "path": str(settings.seekdb.path or ""),
                   "host": settings.seekdb.host or "", "port": settings.seekdb.port,
                   "database": settings.seekdb.database},
        "powercontext_url": settings.powercontext_url,
        "minimax_model": settings.minimax_model,
        "minimax_base_url": settings.minimax_base_url,
    }


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sdd_context",
        description="Spec-driven development context module: select specs, code and tests for a task.",
    )
    parser.add_argument("--version", action="version", version=f"sdd_context {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create the seekdb schema")
    init.add_argument("--json", action="store_true")

    imp = sub.add_parser("import", help="import specs, code and tests into seekdb")
    imp.add_argument("--repo", required=True)
    imp.add_argument("--specs", action="append", help="glob relative to --repo (repeatable)")
    imp.add_argument("--code", action="append", help="glob relative to --repo (repeatable)")
    imp.add_argument("--tests", action="append", help="glob relative to --repo (repeatable)")
    imp.add_argument("--notes", default="")
    imp.add_argument("--json", action="store_true")

    ctx = sub.add_parser("context", help="build the context pack for a task")
    ctx.add_argument("--task", required=True)
    ctx.add_argument("--repo", default=None)
    ctx.add_argument("--spec-limit", type=int, default=4)
    ctx.add_argument("--code-limit", type=int, default=5)
    ctx.add_argument("--test-limit", type=int, default=4)
    ctx.add_argument("--json-out", default=None)
    ctx.add_argument("--markdown-out", default=None)
    ctx.add_argument("--no-state", action="store_true", help="skip reading PowerContext state")
    ctx.add_argument("--allow-missing-state", action="store_true")
    ctx.add_argument("--no-capture", dest="capture", action="store_false", help="do not store the pack in PowerContext")
    ctx.add_argument("--check", action="store_true", help="exit 2 when a declared source is missing")
    ctx.add_argument("--json", action="store_true")

    state = sub.add_parser("state", help="read or update the PowerContext task state")
    state.add_argument("action", choices=["show", "save"])
    state.add_argument("--task", required=True)
    state.add_argument("--status", default=None,
                       choices=None)
    state.add_argument("--done", action="append", help="finished checklist item (repeatable)")
    state.add_argument("--todo", action="append", help="pending checklist item (repeatable)")
    state.add_argument("--next-step", default=None)
    state.add_argument("--verification", default=None, help="JSON file with a verification record")
    state.add_argument("--verification-status", default=None)
    state.add_argument("--verification-command", default=None)
    state.add_argument("--verification-exit-code", type=int, default=0)
    state.add_argument("--json", action="store_true")

    agent = sub.add_parser("agent", help="let MiniMax patch the sample project and run the tests")
    agent.add_argument("--task", required=True)
    agent.add_argument("--attempts", type=int, default=2)
    agent.add_argument("--no-apply", action="store_true")
    agent.add_argument("--allow-missing-state", action="store_true")
    agent.add_argument("--json", action="store_true")

    verify = sub.add_parser("verify", help="run tests and record the result in PowerContext")
    verify.add_argument("--task", required=True)
    verify.add_argument("--workdir", default=None)
    verify.add_argument("--targets", nargs="*", default=None)
    verify.add_argument("--prepare", action="store_true", help="recreate the worktree from the sample")
    verify.add_argument("--reset", action="store_true")
    verify.add_argument("--json", action="store_true")

    db = sub.add_parser("db", help="inspect the seekdb index")
    db.add_argument("action", choices=["stats", "query"])
    db.add_argument("--sql", default="")
    db.add_argument("--param", action="append")
    db.add_argument("--json", action="store_true")

    doctor = sub.add_parser("doctor", help="check seekdb, PowerContext, MiniMax and the sample")
    doctor.add_argument("--json", action="store_true")
    return parser


COMMANDS = {
    "init": cmd_init,
    "import": cmd_import,
    "context": cmd_context,
    "state": cmd_state,
    "agent": cmd_agent,
    "verify": cmd_verify,
    "db": cmd_db,
    "doctor": cmd_doctor,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = _settings()
    handler = COMMANDS[args.command]
    try:
        return handler(args, settings)
    except (PowerContextError, MiniMaxError, SeekDBUnavailable) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 5
