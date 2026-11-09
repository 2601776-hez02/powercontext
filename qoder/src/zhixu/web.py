"""本地 Web 服务：REST API + 项目网站静态文件（仅标准库）。"""

from __future__ import annotations

import json
import mimetypes
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .corpus import list_documents
from .digest import generate as generate_digest
from .search import ask as ask_context
from .search import render_evidence_pack, search
from .store import Store
from .tasks import add_task, list_tasks, set_status, summary

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/json", ".json")


class ZhixuApp:
    """把所有请求处理收在一处，便于测试和复用。"""

    def __init__(self, store: Store, site_dir: Path):
        self.store = store
        self.site_dir = site_dir

    # ------------------------------------------------------------- helpers
    def stats(self) -> dict[str, object]:
        info = self.store.info()
        return {
            "version": __version__,
            "engine": "seekdb",
            "engine_version": info.server_version,
            "schema_version": info.schema_version,
            "documents": self.store.count("zx_documents"),
            "chunks": self.store.count("zx_chunks"),
            "tasks": summary(self.store),
        }

    def search_payload(self, query: str, limit: int) -> dict[str, object]:
        hits, meta = search(self.store, query, limit=limit)
        return {
            "query": query,
            "hits": [hit.as_dict() for hit in hits],
            "meta": {
                "expanded_query": meta.expanded_query,
                "aliases": list(meta.aliases),
                "fts_hits": meta.fts_hits,
                "vector_hits": meta.vector_hits,
                "latency_ms": meta.latency_ms,
            },
        }

    def ask_payload(self, query: str, budget: int) -> dict[str, object]:
        result = ask_context(self.store, query, budget_bytes=budget)
        return {
            "query": query,
            "content": result["content"],
            "content_bytes": result["content_bytes"],
            "budget_bytes": result["budget_bytes"],
            "items": result["items"],
            "markdown": render_evidence_pack(result),
            "meta": {
                "expanded_query": result["meta"].expanded_query,
                "aliases": list(result["meta"].aliases),
                "fts_hits": result["meta"].fts_hits,
                "vector_hits": result["meta"].vector_hits,
                "latency_ms": result["meta"].latency_ms,
            },
        }

    def tasks_payload(self) -> dict[str, object]:
        tasks = list_tasks(self.store)
        return {
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
                    "evidence_chunk_id": task.evidence_chunk_id,
                    "updated_at": task.updated_at,
                }
                for task in tasks
            ],
            "summary": summary(self.store),
        }

    def digest_payload(self, days: int) -> dict[str, object]:
        result = generate_digest(self.store, days=days, report_dir=None, save=False)
        return {
            "period_start": result.period_start.isoformat(),
            "period_end": result.period_end.isoformat(),
            "markdown": result.markdown,
            "stats": result.stats,
        }

    # -------------------------------------------------------------- router
    def route(self, method: str, path: str, query: dict[str, list[str]], body: bytes) -> tuple[int, bytes, str]:
        parsed = urlparse(path)
        clean = parsed.path.rstrip("/") or "/"

        if clean == "/api/health":
            info = self.store.info()
            return 200, _json({"ok": True, "engine": "seekdb", "engine_version": info.server_version}), "application/json"
        if clean == "/api/stats":
            return 200, _json(self.stats()), "application/json"
        if clean == "/api/docs":
            return 200, _json({"documents": list_documents(self.store)}), "application/json"
        if clean == "/api/search":
            q = _first(query, "q")
            limit = _int(_first(query, "k"), 6, 1, 30)
            if not q:
                return 400, _json({"error": "缺少参数 q"}), "application/json"
            return 200, _json(self.search_payload(q, limit)), "application/json"
        if clean == "/api/ask":
            q = _first(query, "q")
            budget = _int(_first(query, "budget"), 1800, 512, 32768)
            if not q:
                return 400, _json({"error": "缺少参数 q"}), "application/json"
            return 200, _json(self.ask_payload(q, budget)), "application/json"
        if clean == "/api/tasks":
            if method == "GET":
                return 200, _json(self.tasks_payload()), "application/json"
            if method == "POST":
                payload = _body_json(body)
                if not payload.get("title"):
                    return 400, _json({"error": "缺少 title"}), "application/json"
                task = add_task(
                    self.store,
                    str(payload["title"]),
                    status=str(payload.get("status", "todo")),
                    priority=str(payload.get("priority", "P2")),
                    owner=str(payload.get("owner", "")),
                    due=payload.get("due") or None,
                    detail=str(payload.get("detail", "")),
                )
                return 201, _json({"task": {"id": task.id, "title": task.title}}), "application/json"
        if clean.startswith("/api/tasks/") and clean.endswith("/status") and method == "POST":
            task_id = int(clean.split("/")[3])
            payload = _body_json(body)
            status = str(payload.get("status", ""))
            if status not in {"todo", "doing", "blocked", "done"}:
                return 400, _json({"error": "status 非法"}), "application/json"
            task = set_status(self.store, task_id, status, note=str(payload.get("note", "")))
            return 200, _json({"task": {"id": task.id, "status": task.status}}), "application/json"
        if clean == "/api/digest":
            days = _int(_first(query, "days"), 7, 1, 90)
            return 200, _json(self.digest_payload(days)), "application/json"

        if clean.startswith("/api/"):
            return 404, _json({"error": "未知接口", "path": clean}), "application/json"

        # 静态站点
        return self._static(clean)


    def _static(self, clean: str) -> tuple[int, bytes, str]:
        relative = clean.lstrip("/")
        candidate = (self.site_dir / relative).resolve() if relative else (self.site_dir / "index.html").resolve()
        site_root = self.site_dir.resolve()
        if not str(candidate).startswith(str(site_root)):
            return 403, b"forbidden", "text/plain; charset=utf-8"
        if candidate.is_dir():
            candidate = candidate / "index.html"
        if not candidate.is_file():
            if clean == "/":
                return 404, "项目网站尚未生成，请先运行 `python -m zhixu export-site`。".encode("utf-8"), "text/plain; charset=utf-8"
            return 404, b"not found", "text/plain; charset=utf-8"
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/json", "image/svg+xml"}:
            content_type += "; charset=utf-8"
        return 200, candidate.read_bytes(), content_type


def _json(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _first(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key) or [""]
    return values[0]


def _int(raw: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _body_json(body: bytes) -> dict[str, object]:
    if not body:
        return {}
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def make_handler(app: ZhixuApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "zhixu/0.1"

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            length = int(self.headers.get("Content-Length") or "0")
            body = self.rfile.read(length) if length else b""
            try:
                status, payload, content_type = app.route(method, self.path, query, body)
            except Exception as exc:  # pragma: no cover - 兜底
                status = 500
                payload = _json({"error": str(exc)})
                content_type = "application/json"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            # 保持终端干净，只记录错误
            if args and str(args[1]).startswith(("4", "5")):
                super().log_message(format, *args)

    return Handler


def serve(store: Store, site_dir: Path, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    app = ZhixuApp(store, site_dir)
    handler = make_handler(app)
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd  # type: ignore[return-value]


def run_forever(store: Store, site_dir: Path, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    app = ZhixuApp(store, site_dir)
    handler = make_handler(app)
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    print(f"知序服务已启动：http://{host}:{port}")
    print("按 Ctrl+C 停止。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
