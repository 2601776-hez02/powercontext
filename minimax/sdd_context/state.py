"""Task state persisted in PowerContext.

PowerContext owns the durable record of *what the task is doing*: status, the
finished and pending checklist items, and the verification runs with their exit
codes. The module keeps no local copy, so the only way to see the state is to
ask PowerContext — which is exactly the restart scenario the task asks for.

Two PowerContext primitives are used:

* a Source per context pack (``source_id`` = ``sdd-context-<task>-<import>``),
  so the exact context a run used stays addressable and idempotent;
* a Memory entry of kind ``sdd-task-state`` holding the compact JSON state,
  read back with ``POST /v1/memory/search``.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass
from typing import Any, Iterable

import requests

STATE_KIND = "sdd-task-state"


class PowerContextError(RuntimeError):
    """PowerContext is unreachable or rejected the write."""


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class SaveResult:
    task_id: str
    scope_id: str
    memory_reference: dict[str, Any]
    entry_version: int | None
    bytes_written: int
    source_reference: dict[str, Any] | None = None


class TaskStateStore:
    """HTTP client for the parts of the PowerContext API this module needs."""

    def __init__(self, base_url: str, token: str | None = None, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._scope_id: str | None = None

    # -- plumbing ---------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _call(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            response = requests.request(
                method, url, json=payload, headers=self._headers(), timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise PowerContextError(f"PowerContext is unreachable at {self.base_url}: {exc}") from exc
        if response.status_code >= 400:
            detail = response.text[:500]
            raise PowerContextError(f"{method} {path} failed with HTTP {response.status_code}: {detail}")
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:  # pragma: no cover - defensive
            raise PowerContextError(f"{method} {path} returned non-JSON content") from exc

    def health(self) -> dict[str, Any]:
        return self._call("GET", "/v1/capabilities")

    def doctor(self) -> dict[str, Any]:
        try:
            ready = self._call("GET", "/health/ready")
            return {"base_url": self.base_url, "status": "ready", "detail": ready}
        except PowerContextError as exc:
            return {"base_url": self.base_url, "status": "unreachable", "detail": str(exc)}

    def scope_id(self) -> str:
        """Resolve the default Scope once per process."""
        if self._scope_id:
            return self._scope_id
        try:
            descriptor = self._call("POST", "/v1/scope-bindings/resolve", {"allow_default": True})
        except PowerContextError:
            descriptor = self._call("GET", "/v1/scopes/default")
        scope_id = descriptor.get("scope_id")
        if not scope_id:
            raise PowerContextError("PowerContext did not return a scope_id for the default binding")
        self._scope_id = str(scope_id)
        return self._scope_id

    # -- state ------------------------------------------------------------
    def empty_state(self, task_id: str, title: str) -> dict[str, Any]:
        return {
            "schema": "sdd-task-state/v1",
            "task_id": task_id,
            "title": title,
            "status": "not_started",
            "revision": 0,
            "created_at": _now(),
            "updated_at": _now(),
            "done": [],
            "todo": [],
            "verifications": [],
            "next_step": "",
        }

    def load(self, task_id: str) -> dict[str, Any] | None:
        """Read the newest state entry for ``task_id`` from PowerContext Memory."""
        payload = {
            "scope_id": self.scope_id(),
            "query": f"SDD task state {task_id}",
            "limit": 20,
        }
        response = self._call("POST", "/v1/memory/search", payload)
        best: dict[str, Any] | None = None
        for hit in response.get("hits", []):
            text = hit.get("text") or ""
            try:
                candidate = json.loads(text)
            except ValueError:
                continue
            if isinstance(candidate, dict) and candidate.get("task_id") == task_id:
                if best is None or int(candidate.get("revision", 0)) >= int(best.get("revision", 0)):
                    candidate["_citation"] = hit.get("citation")
                    candidate["_score"] = hit.get("score")
                    best = candidate
        return best

    def save(self, state: dict[str, Any], *, reason: str = "") -> SaveResult:
        """Write one compact state entry; returns the PowerContext references."""
        text = json.dumps(state, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if len(text.encode("utf-8")) > 8000:
            state = _shrink(state)
            text = json.dumps(state, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        payload = {
            "scope_id": self.scope_id(),
            "kind": STATE_KIND,
            "text": text,
            "reason": reason or f"SDD task {state.get('task_id')} state revision {state.get('revision')}",
        }
        response = self._call("POST", "/v1/memory/remember", payload)
        entry = response.get("entry") or {}
        return SaveResult(
            task_id=str(state.get("task_id", "")),
            scope_id=self._scope_id or "",
            memory_reference=response.get("memory") or {},
            entry_version=entry.get("version"),
            bytes_written=len(text.encode("utf-8")),
        )

    def remember_state(
        self,
        task_id: str,
        title: str,
        *,
        status: str | None = None,
        done: Iterable[str] | None = None,
        todo: Iterable[str] | None = None,
        next_step: str | None = None,
        verification: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], SaveResult]:
        """Merge ``updates`` into the stored state and persist a new revision."""
        state = self.load(task_id) or self.empty_state(task_id, title)
        state.pop("_citation", None)
        state.pop("_score", None)
        if status:
            state["status"] = status
        if done is not None:
            state["done"] = _unique([*state.get("done", []), *done])
            state["todo"] = [item for item in state.get("todo", []) if item not in state["done"]]
        if todo is not None:
            state["todo"] = _unique([*state.get("todo", []), *todo])
        if next_step is not None:
            state["next_step"] = next_step
        if verification is not None:
            verifications = list(state.get("verifications", []))
            verifications.append(verification)
            state["verifications"] = verifications[-12:]
        state["revision"] = int(state.get("revision", 0)) + 1
        state["updated_at"] = _now()
        result = self.save(state, reason=f"SDD task {task_id} update")
        state["_memory_reference"] = result.memory_reference
        state["_scope_id"] = result.scope_id
        return state, result

    # -- sources ----------------------------------------------------------
    def capture_context_source(self, *, task_id: str, import_id: str, content: str,
                               metadata: dict[str, Any]) -> dict[str, Any]:
        """Store the exact context pack as an idempotent PowerContext Source."""
        source_id = f"sdd-context-{task_id}-{import_id}"
        payload = {
            "scope_id": self.scope_id(),
            "source_id": source_id,
            "content": content,
            "metadata": metadata,
        }
        response = self._call("POST", "/v1/sources/content", payload)
        return {"source_id": source_id, "status": response.get("status"),
                "source": response.get("source"), "position": response.get("position")}


def _unique(items: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        if item and item not in seen:
            seen[item] = None
    return list(seen)


def _shrink(state: dict[str, Any]) -> dict[str, Any]:
    """Keep the state inside PowerContext's 8192 byte Memory limit."""
    state = dict(state)
    state["verifications"] = [
        {key: entry.get(key) for key in ("command", "exit_code", "summary", "at", "import_id")}
        for entry in state.get("verifications", [])[-4:]
        if isinstance(entry, dict)
    ]
    state["done"] = [item[:160] for item in state.get("done", [])[:12]]
    state["todo"] = [item[:160] for item in state.get("todo", [])[:12]]
    state["trimmed"] = True
    return state
