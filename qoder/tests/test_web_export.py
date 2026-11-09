from __future__ import annotations

import json
from pathlib import Path

from zhixu.corpus import ingest, seed_aliases
from zhixu.export_site import export_site
from zhixu.store import Store
from zhixu.web import ZhixuApp


def _prepare(store: Store, corpus_dir: Path) -> None:
    ingest(store, corpus_dir)
    seed_aliases(store, Path(__file__).resolve().parents[1] / "data" / "seed" / "aliases.json")


def test_api_health_and_stats(store: Store, corpus_dir: Path, tmp_path: Path) -> None:
    _prepare(store, corpus_dir)
    app = ZhixuApp(store, tmp_path)
    status, payload, content_type = app.route("GET", "/api/health", {}, b"")
    assert status == 200 and content_type.startswith("application/json")
    assert json.loads(payload)["engine"] == "seekdb"

    status, payload, _ = app.route("GET", "/api/stats", {}, b"")
    body = json.loads(payload)
    assert status == 200
    assert body["documents"] >= 2
    assert body["chunks"] >= 2


def test_api_search_and_ask(store: Store, corpus_dir: Path, tmp_path: Path) -> None:
    _prepare(store, corpus_dir)
    app = ZhixuApp(store, tmp_path)
    status, payload, _ = app.route("GET", "/api/search", {"q": ["efSearch 召回率"], "k": ["3"]}, b"")
    body = json.loads(payload)
    assert status == 200
    assert body["hits"]
    hit = body["hits"][0]
    assert hit["char_start"] < hit["char_end"]
    assert hit["document_path"]

    status, payload, _ = app.route("GET", "/api/ask", {"q": ["efSearch"], "budget": ["900"]}, b"")
    body = json.loads(payload)
    assert status == 200
    assert body["content_bytes"] <= 900
    assert body["markdown"].startswith("# 证据包")


def test_api_tasks_and_digest(store: Store, corpus_dir: Path, tmp_path: Path) -> None:
    _prepare(store, corpus_dir)
    app = ZhixuApp(store, tmp_path)
    status, payload, _ = app.route(
        "POST", "/api/tasks", {}, json.dumps({"title": "写周报", "priority": "P1"}).encode("utf-8")
    )
    assert status == 201
    task_id = json.loads(payload)["task"]["id"]

    status, payload, _ = app.route(
        "POST",
        f"/api/tasks/{task_id}/status",
        {},
        json.dumps({"status": "done", "note": "完成"}).encode("utf-8"),
    )
    assert status == 200

    status, payload, _ = app.route("GET", "/api/digest", {"days": ["7"]}, b"")
    body = json.loads(payload)
    assert status == 200
    assert "写周报" in body["markdown"]


def test_static_site_serving(store: Store, corpus_dir: Path, tmp_path: Path) -> None:
    _prepare(store, corpus_dir)
    (tmp_path / "index.html").write_text("<html>知序</html>", encoding="utf-8")
    app = ZhixuApp(store, tmp_path)
    status, payload, content_type = app.route("GET", "/", {}, b"")
    assert status == 200
    assert "text/html" in content_type
    assert "知序" in payload.decode("utf-8")


def test_export_site_writes_real_data(store: Store, corpus_dir: Path, tmp_path: Path) -> None:
    _prepare(store, corpus_dir)
    site_dir = tmp_path / "site"
    result = export_site(store, tmp_path, site_dir)
    assert Path(result["path"]).exists()
    data = json.loads((site_dir / "data" / "demo.json").read_text(encoding="utf-8"))
    assert data["schema"] == "zhixu.site.v1"
    assert data["stats"]["documents"] >= 2
    assert data["demos"]
    for demo in data["demos"]:
        assert demo["content_bytes"] <= demo["budget_bytes"]
        assert demo["items"] or demo["content_bytes"] > 0
    assert data["evidence"]["checks"]["fts_index"] is True
    assert data["evidence"]["checks"]["vector_index"] is True
    assert data["evidence"]["benchmark"]["samples"] > 0
