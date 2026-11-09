"""Import a repository's specifications, code and tests into seekdb."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .chunking import discover, load_document
from .config import Settings
from .records import append_event, now_iso, write_json
from .seekdb_store import SeekDBStore

DEFAULT_SPEC_GLOBS = ("docs/specs/*.md", "docs/**/*.md", "specs/**/*.md")
DEFAULT_CODE_GLOBS = ("src/**/*.py",)
DEFAULT_TEST_GLOBS = ("tests/**/*.py",)


@dataclass
class ImportResult:
    repo: str
    import_id: str
    documents: int
    items: int
    by_kind: dict[str, int]
    revisions: dict[str, str]
    changes: dict[str, int] = field(default_factory=dict)
    changed_documents: list[dict[str, Any]] = field(default_factory=list)
    target: str = ""
    server_version: str = ""
    duration_s: float = 0.0
    imported_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "import_id": self.import_id,
            "documents": self.documents,
            "items": self.items,
            "items_by_kind": self.by_kind,
            "revisions": self.revisions,
            "changes": self.changes,
            "changed_documents": self.changed_documents,
            "seekdb_target": self.target,
            "seekdb_version": self.server_version,
            "duration_s": round(self.duration_s, 3),
            "imported_at": self.imported_at,
        }


def import_repository(
    settings: Settings,
    repo: Path,
    *,
    spec_globs: Sequence[str] = DEFAULT_SPEC_GLOBS,
    code_globs: Sequence[str] = DEFAULT_CODE_GLOBS,
    test_globs: Sequence[str] = DEFAULT_TEST_GLOBS,
    notes: str = "",
) -> ImportResult:
    """Scan ``repo``, write every item to seekdb and journal the revision diff."""
    started = time.time()
    repo = repo.resolve()
    if not repo.is_dir():
        raise FileNotFoundError(f"repository not found: {repo}")
    pairs = discover(repo, tuple(spec_globs), tuple(code_globs), tuple(test_globs))
    if not pairs:
        raise RuntimeError(
            f"no material matched under {repo}; checked specs={list(spec_globs)} "
            f"code={list(code_globs)} tests={list(test_globs)}"
        )
    documents = [load_document(repo, path, kind) for path, kind in pairs]
    by_kind: dict[str, int] = {}
    for document in documents:
        by_kind[document.kind] = by_kind.get(document.kind, 0) + len(document.items)

    with SeekDBStore(settings.seekdb) as store:
        schema = store.initialize()
        summary = store.apply_import(documents, repo_path=str(repo), target_label=settings.seekdb.label, notes=notes)
        changes = store.document_changes(summary["import_id"], limit=50)

    result = ImportResult(
        repo=str(repo),
        import_id=summary["import_id"],
        documents=summary["documents"],
        items=summary["items"],
        by_kind=by_kind,
        revisions=summary["revisions"],
        changes=summary["changes"],
        changed_documents=changes,
        target=settings.seekdb.label,
        server_version=schema["server_version"],
        duration_s=time.time() - started,
        imported_at=summary["imported_at"],
    )
    write_json(settings.records_dir / "imports" / f"{result.import_id}.json", result.as_dict())
    append_event(
        settings.records_dir,
        "import",
        {
            "import_id": result.import_id,
            "repo": result.repo,
            "documents": result.documents,
            "items": result.items,
            "revisions": result.revisions,
            "changed": [row["path"] for row in changes],
            "seekdb": result.target,
            "at": now_iso(),
        },
    )
    return result
