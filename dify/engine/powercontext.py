"""PowerContext 记忆层客户端（HTTP 接入，可降级）。

分工：seekdb 记「这一局里谁看见了什么」，PowerContext 记「来过这间酒馆的人
最后成了什么传说」——跨会话、跨进程，下次进门时被读回来，变成开场白。

用到的接口（PowerContext 1.5.0，均需 Bearer token）：
* ``POST /v1/scopes``                    建 scope（用 idempotency_key 保证幂等）
* ``POST /v1/memory/remember``           写一条 memory（kind=tavern-legend）
* ``POST /v1/memory/entries/list``       按时间倒序读回记忆（本作品的召回方式）
* ``POST /v1/memory/search``             语义/文本检索（配了 Embedding 时更好用，失败则退回 list）

服务没起来、或没有 token 时不阻塞游戏，只在返回体里标记 ``available: false``。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .config import Settings


class PowerContext:
    def __init__(self, settings: Settings):
        self._cfg = settings
        self._base = settings.powercontext_url.rstrip("/")
        self._enabled = settings.powercontext_enabled

    # ------------------------------------------------------------------ http
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._cfg.powercontext_token:
            headers["Authorization"] = f"Bearer {self._cfg.powercontext_token}"
        return headers

    def _post(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
        if not self._enabled:
            return 0, None
        request = urllib.request.Request(
            self._base + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(request, timeout=self._cfg.powercontext_timeout) as response:
                body = response.read().decode("utf-8", "replace")
                return response.status, (json.loads(body) if body else {})
        except urllib.error.HTTPError as exc:
            return exc.code, None
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return 0, None

    def _get_json(self, path: str) -> tuple[int, dict[str, Any] | None]:
        if not self._enabled:
            return 0, None
        request = urllib.request.Request(self._base + path, headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=self._cfg.powercontext_timeout) as response:
                body = response.read().decode("utf-8", "replace")
                return response.status, (json.loads(body) if body else {})
        except urllib.error.HTTPError as exc:
            return exc.code, None
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return 0, None

    def find_scope(self, title: str) -> str:
        """按标题在已有 scope 里找（幂等键冲突时的兜底）。"""

        status, response = self._get_json("/v1/scopes?limit=100")
        if not response:
            return ""
        for scope in response.get("scopes") or response.get("items") or []:
            if str(scope.get("title") or "") == title:
                return str(scope.get("scope_id") or "")
        return ""

    def _get(self, path: str) -> int:
        if not self._enabled:
            return 0
        request = urllib.request.Request(self._base + path, headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=self._cfg.powercontext_timeout) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code
        except (urllib.error.URLError, TimeoutError, OSError):
            return 0

    # ------------------------------------------------------------------ api
    def scope_name(self, guest: str) -> str:
        return f"{self._cfg.powercontext_scope_prefix}/{guest}"

    def health(self) -> dict[str, Any]:
        if not self._enabled:
            return {"enabled": False, "available": False}
        status = self._get("/health/ready")
        return {"enabled": True, "available": status == 200, "url": self._base, "status": status}

    def create_scope(self, guest: str) -> dict[str, Any]:
        """建（或取回）这个酒客的 scope；idempotency_key 让重复调用返回同一个。"""

        name = self.scope_name(guest)
        payload = {
            "title": f"忘川酒馆 · {guest}",
            "summary": f"{guest} 在渡口忘川酒馆里的记录：做过什么、被谁记住、最后成了什么传说。",
            "idempotency_key": name,
        }
        status, response = self._post("/v1/scopes", payload)
        if response is not None:
            return {"available": True, "scope_id": response.get("scope_id", ""), "status": status, "name": name}
        if status == 409:
            # 同一个 idempotency_key 但内容不同（例如改过 summary）→ 找回已有 scope
            existing = self.find_scope(payload["title"])
            if existing:
                return {"available": True, "scope_id": existing, "status": status, "name": name, "recovered": True}
        return {"available": False, "scope_id": "", "status": status}

    def remember_legend(self, scope_id: str, text: str, reason: str = "打烊后的账本") -> dict[str, Any]:
        if not scope_id:
            return {"available": False, "written": False}
        status, response = self._post(
            "/v1/memory/remember",
            {"scope_id": scope_id, "kind": "tavern-legend", "text": text[:8100], "reason": reason[:200]},
        )
        if response is None:
            return {"available": False, "written": False, "status": status}
        records = response.get("records") or []
        first = records[0] if records else {}
        artifact = first.get("artifact") or {}
        return {
            "available": True,
            "written": bool(response.get("changed")),
            "status": status,
            "artifact_id": artifact.get("artifact_id"),
            "revision": artifact.get("revision"),
            "kind": first.get("kind"),
        }

    def recall(self, scope_id: str, query: str = "上次来酒馆做了什么", limit: int = 3) -> dict[str, Any]:
        """进门时把「酒馆对你的印象」读回来：先试语义检索，空则按时间倒序取最近的。"""

        if not scope_id:
            return {"available": False, "hits": [], "mode": "none"}
        hits: list[dict[str, Any]] = []
        mode = "search"
        status, response = self._post(
            "/v1/memory/search", {"scope_id": scope_id, "query": query, "limit": limit, "mode": "auto"}
        )
        if response:
            mode = str(response.get("mode") or "search")
            for hit in response.get("hits") or []:
                memory = hit.get("memory") or {}
                text = str(memory.get("text") or hit.get("text") or "")
                if not text:
                    continue
                artifact = memory.get("artifact") or {}
                hits.append(
                    {
                        "text": text,
                        "kind": memory.get("kind") or hit.get("kind"),
                        "artifact_id": artifact.get("artifact_id"),
                        "revision": artifact.get("revision"),
                        "score": round(float(hit.get("score") or 0.0), 4),
                        "matched_by": hit.get("matched_by", []),
                    }
                )
        if not hits:
            status, response = self._post("/v1/memory/entries/list", {"scope_id": scope_id, "limit": limit})
            mode = "list" if response else "unavailable"
            for entry in (response or {}).get("entries") or []:
                artifact = entry.get("artifact") or {}
                hits.append(
                    {
                        "text": entry.get("text", ""),
                        "kind": entry.get("kind"),
                        "score": 1.0,
                        "matched_by": ["recent"],
                        "artifact_id": artifact.get("artifact_id"),
                        "revision": artifact.get("revision"),
                    }
                )
        return {
            "available": bool(hits),
            "mode": mode,
            "scope_id": scope_id,
            "hits": hits[:limit],
            "status": status,
        }

    # 兼容旧名字
    def search_legend(self, guest: str, query: str = "上次来酒馆做了什么", limit: int = 3) -> dict[str, Any]:
        created = self.create_scope(guest)
        return self.recall(created.get("scope_id", ""), query=query, limit=limit)
