from __future__ import annotations

import json
from pathlib import Path

from zhixu.corpus import ingest
from zhixu.digest import generate as generate_digest
from zhixu.store import Store
from zhixu.tasks import add_task, list_tasks, seed_tasks, set_status, summary, task_events


def test_task_status_transition_writes_event(store: Store) -> None:
    task = add_task(store, "写评测脚本", priority="P1", due="2026-11-12")
    assert task.status == "todo"
    updated = set_status(store, task.id, "doing", note="开始写")
    assert updated.status == "doing"
    done = set_status(store, task.id, "done", note="脚本跑通")
    assert done.status == "done"
    events = task_events(store, task.id)
    assert [event["to_status"] for event in events][:3] == ["done", "doing", "todo"]


def test_task_summary_counts_overdue(store: Store) -> None:
    add_task(store, "逾期任务", due="2020-01-01")
    add_task(store, "正常任务", status="done")
    data = summary(store)
    assert data["total"] == 2
    assert data["overdue"] == ["逾期任务"]
    assert data["counts"]["done"] == 1


def test_digest_contains_completed_tasks(store: Store, corpus_dir: Path) -> None:
    ingest(store, corpus_dir)
    task = add_task(store, "整理 HNSW 实验", priority="P0")
    set_status(store, task.id, "done", note="结论已归档")
    result = generate_digest(store, days=7, report_dir=None, save=True)
    assert "学研周报" in result.markdown
    assert "整理 HNSW 实验" in result.markdown
    assert result.stats["completed"] >= 1
    assert store.count("zx_digests") == 1


def test_seed_tasks_is_idempotent(store: Store, corpus_dir: Path) -> None:
    ingest(store, corpus_dir)
    seed_path = Path(__file__).resolve().parents[1] / "data" / "seed" / "tasks.json"
    if not seed_path.exists():
        return
    first = seed_tasks(store, seed_path)
    second = seed_tasks(store, seed_path)
    assert first > 0
    assert second == 0
    assert len(list_tasks(store)) == first


def test_task_seed_resolves_evidence(store: Store, corpus_dir: Path) -> None:
    ingest(store, corpus_dir)
    seed = [
        {
            "title": "核对出处",
            "status": "done",
            "priority": "P1",
            "due": "2026-11-05",
            "evidence_path": "01-hnsw.md",
        }
    ]
    path = Path(__file__).parent / "_seed.json"
    path.write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
    try:
        assert seed_tasks(store, path) == 1
    finally:
        path.unlink(missing_ok=True)
    task = list_tasks(store)[0]
    assert task.evidence_document_id is not None
    assert task.evidence_chunk_id is not None
