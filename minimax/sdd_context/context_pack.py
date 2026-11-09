"""Assemble the development context pack.

The pack is the module's public output: a JSON document for tools and a concise
Markdown rendering for a coding assistant. Both carry the same three groups of
materials (specification items, code to change, tests) plus the persisted task
state, and every quote keeps its file, line range and content revision.

``verify_sources`` re-reads the repository and marks each item ``ok``,
``stale`` (file changed after the last import) or ``missing``. That is how a
reviewer can tell whether an earlier verification still applies to the current
code.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .retrieval import Selection, ScoredItem, Task, certify

DEFAULT_TEXT_LIMIT = 4200


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clip(text: str, limit: int = DEFAULT_TEXT_LIMIT) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip() + "\n... [truncated]", True


@dataclass
class SourceCheck:
    item_ref: str
    status: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"item_ref": self.item_ref, "status": self.status, "detail": self.detail}


@dataclass
class ContextPack:
    task: Task
    selection: Selection
    state: dict[str, Any] | None
    index: dict[str, Any] | None
    db: dict[str, Any]
    source_checks: list[SourceCheck]
    checks: dict[str, Any]
    generated_at: str = field(default_factory=_now)
    repo_root: str = ""

    # -- JSON -------------------------------------------------------------
    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "sdd-context-pack/v1",
            "generated_at": self.generated_at,
            "repo": self.repo_root,
            "task": self.task.as_dict(),
            "database": self.db,
            "index": self.index,
            "materials": {
                "specs": [item.as_dict() for item in self.selection.specs],
                "code": [item.as_dict() for item in self.selection.code],
                "tests": [item.as_dict() for item in self.selection.tests],
            },
            "task_state": self.state,
            "checks": self.checks,
            "source_checks": [check.as_dict() for check in self.source_checks],
            "retrieval": {
                "terms": self.selection.terms,
                "candidate_count": self.selection.candidate_count,
                "queries": self.selection.queries,
            },
        }

    # -- Markdown ---------------------------------------------------------
    def to_markdown(self) -> str:
        lines: list[str] = []
        add = lines.append
        add(f"# Development context: {self.task.title}")
        add("")
        add(f"- Task id: `{self.task.task_id}`")
        add(f"- Generated: {self.generated_at}")
        add(f"- Repository: `{self.repo_root}`")
        if self.index:
            add(
                f"- Index: import `{self.index['import_id']}` · spec revision `{self.index['spec_revision'][:12]}` "
                f"· code revision `{self.index['code_revision'][:12]}` · {self.index['item_count']} items"
            )
        add(f"- Storage: {self.db.get('target')} (seekdb {self.db.get('server_version')})")
        add("")
        add("## Task")
        add("")
        add(self.task.description.strip() or self.task.title)
        if self.task.keep_behavior:
            add("")
            add("Behavior that must be preserved:")
            for entry in self.task.keep_behavior:
                add(f"- {entry}")
        add("")

        add("## Applicable specification items")
        add("")
        if not self.selection.specs:
            add("_No specification item was selected._")
        for item in self.selection.specs:
            hit = item.hit
            add(f"### `{hit['item_ref']}` ({hit['title']})")
            add(f"- Revision: `{hit['revision'][:12]}` · score {item.score:.3f} · {'; '.join(item.reasons)}")
            add("")
            body, _ = _clip(hit["text"])
            add("```text")
            add(body)
            add("```")
            add("")

        add("## Code to change")
        add("")
        if not self.selection.code:
            add("_No code symbol was selected._")
        for item in self.selection.code:
            hit = item.hit
            add(f"### `{hit['item_ref']}` (lines {hit['start_line']}-{hit['end_line']})")
            add(f"- Revision: `{hit['revision'][:12]}` · score {item.score:.3f} · {'; '.join(item.reasons)}")
            add("")
            body, _ = _clip(hit["text"])
            add("```python")
            add(body)
            add("```")
            add("")

        add("## Tests for verification")
        add("")
        if not self.selection.tests:
            add("_No test was selected._")
        for item in self.selection.tests:
            hit = item.hit
            add(f"### `{hit['item_ref']}`")
            add(f"- Revision: `{hit['revision'][:12]}` · score {item.score:.3f} · {'; '.join(item.reasons)}")
            add("")
            body, _ = _clip(hit["text"], 2600)
            add("```python")
            add(body)
            add("```")
            add("")

        add("## Task state (persisted in PowerContext)")
        add("")
        if not self.state:
            add("_No task state saved yet._")
        else:
            add(f"- Status: `{self.state.get('status', 'unknown')}` · updated {self.state.get('updated_at', '')}")
            if self.state.get("done"):
                add("- Done:")
                for entry in self.state["done"]:
                    add(f"  - {entry}")
            if self.state.get("todo"):
                add("- Todo:")
                for entry in self.state["todo"]:
                    add(f"  - {entry}")
            if self.state.get("verifications"):
                add("- Verification results:")
                for entry in self.state["verifications"]:
                    if isinstance(entry, dict):
                        add(
                            f"  - {entry.get('command', '')} → exit {entry.get('exit_code')} "
                            f"({entry.get('summary', '')}) at {entry.get('at', '')}"
                        )
                    else:
                        add(f"  - {entry}")
            if self.state.get("next_step"):
                add(f"- Next step: {self.state['next_step']}")
        add("")

        add("## Source freshness")
        add("")
        if not self.source_checks:
            add("_No source checks recorded._")
        for check in self.source_checks:
            add(f"- `{check.item_ref}` → **{check.status}** {check.detail}".rstrip())
        add("")

        add("## Constraints for the change")
        add("")
        add("- Modify only the code shown under \"Code to change\" unless a specification item forces a new file.")
        add("- Keep every behavior that the specification marks as preserved; the regression tests enforce it.")
        add("- Do not edit tests, do not delete assertions and do not rename public symbols.")
        add("- Follow the specification items above over any habit that contradicts them.")
        return "\n".join(lines).rstrip() + "\n"

    def certify(self) -> dict[str, Any]:
        return certify(self.selection)


def verify_sources(selection: Selection, repo_root: Path) -> list[SourceCheck]:
    """Check every selected item against the repository on disk."""
    checks: list[SourceCheck] = []
    for item in selection.all_items():
        hit = item.hit
        path = repo_root / hit["path"]
        if not path.is_file():
            checks.append(SourceCheck(hit["item_ref"], "missing", f"{hit['path']} does not exist"))
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:  # pragma: no cover - permissions
            checks.append(SourceCheck(hit["item_ref"], "missing", str(exc)))
            continue
        revision = hashlib.sha256(text.encode("utf-8")).hexdigest()
        lines = text.splitlines()
        if hit["start_line"] > len(lines):
            checks.append(SourceCheck(hit["item_ref"], "stale", f"line {hit['start_line']} beyond EOF"))
            continue
        if revision != hit["revision"]:
            checks.append(
                SourceCheck(hit["item_ref"], "stale", f"file changed after import ({hit['revision'][:12]} → {revision[:12]})")
            )
            continue
        checks.append(SourceCheck(hit["item_ref"], "ok", f"lines {hit['start_line']}-{hit['end_line']}"))
    return checks


def build_pack(
    selection: Selection,
    *,
    repo_root: Path,
    state: dict[str, Any] | None,
    index: dict[str, Any] | None,
    db: dict[str, Any],
) -> ContextPack:
    checks = certify(selection)
    source_checks = verify_sources(selection, repo_root)
    status_counts: dict[str, int] = {}
    for check in source_checks:
        status_counts[check.status] = status_counts.get(check.status, 0) + 1
    checks = {
        **checks,
        "source_status": status_counts,
        "stale_sources": [check.item_ref for check in source_checks if check.status != "ok"],
    }
    return ContextPack(
        task=selection.task,
        selection=selection,
        state=state,
        index=index,
        db=db,
        source_checks=source_checks,
        checks=checks,
        repo_root=str(repo_root),
    )


def write_pack(pack: ContextPack, *, json_path: Path | None, markdown_path: Path | None) -> None:
    if json_path:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(pack.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    if markdown_path:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(pack.to_markdown(), encoding="utf-8")


def summarise(selection: Selection) -> dict[str, Any]:
    """Small helper for CLI output."""
    def rows(items: Sequence[ScoredItem]) -> list[dict[str, Any]]:
        return [
            {"ref": item.hit["item_ref"], "score": round(item.score, 3), "why": "; ".join(item.reasons[:2])}
            for item in items
        ]

    return {"specs": rows(selection.specs), "code": rows(selection.code), "tests": rows(selection.tests)}
