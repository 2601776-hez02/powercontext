"""Turn repository files into addressable engineering items.

Three material families are supported, and each item keeps a stable reference
that a reviewer can open by hand:

* ``spec`` - one ``## <ID> <title>`` section of a Markdown specification;
* ``code`` - one module, class, function or method of a Python source file;
* ``test`` - one ``test_*`` function of a Python test file.

Items are the unit that is stored in seekdb, retrieved for a task and quoted in
the context pack. Nothing downstream re-reads the repository: the index is the
single source of truth for the run.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

SPEC_SUFFIXES = {".md", ".markdown"}
CODE_SUFFIXES = {".py"}
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}

_SPEC_HEADING = re.compile(r"^##\s+(?P<ident>[A-Z][A-Z0-9]{1,7}-\d{1,3})?\s*(?P<title>.*)$")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*|\d+")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

STOPWORDS = frozenset(
    """
    a an the and or but if then else when while of for to in on at by with from as is are was were be been
    being it its this that these those must should shall may can will would could not no into over under
    each every any all some only also than then there here which who whom whose what when where how why
    do does did done has have had using use used via per ie eg etc
    """.split()
)


def tokens(text: str) -> list[str]:
    """Lowercase identifier-ish tokens, splitting camelCase and snake_case.

    seekdb's FULLTEXT index here uses the SPACE parser, which does not segment
    Chinese text. CJK runs are therefore indexed as overlapping bigrams (plus
    the single characters of short runs), so a Chinese query and the stored
    ``searchable`` column use the same vocabulary.
    """
    out: list[str] = []
    for raw in _WORD.findall(text):
        for part in _CAMEL.split(raw):
            for piece in part.split("_"):
                low = piece.lower()
                if len(low) < 2 or low in STOPWORDS:
                    continue
                out.append(low)
    for run in _CJK.findall(text):
        if len(run) == 1:
            out.append(run)
            continue
        out.extend(run[index : index + 2] for index in range(len(run) - 1))
    return out


def searchable_text(*parts: str) -> str:
    """Space separated token bag for the seekdb FULLTEXT index."""
    seen: dict[str, None] = {}
    for part in parts:
        for token in tokens(part):
            seen.setdefault(token, None)
    return " ".join(seen)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Item:
    """One retrievable engineering item."""

    item_id: str
    kind: str
    path: str
    item_ref: str
    symbol: str
    title: str
    start_line: int
    end_line: int
    text: str
    searchable: str
    revision: str
    doc_id: str
    imports: tuple[str, ...] = field(default_factory=tuple)

    def as_row(self) -> tuple[object, ...]:
        return (
            self.item_id,
            self.doc_id,
            self.kind,
            self.path,
            self.item_ref,
            self.symbol,
            self.title,
            self.start_line,
            self.end_line,
            self.revision,
            self.text,
            self.searchable,
        )


@dataclass
class Document:
    """One source file and the items extracted from it."""

    doc_id: str
    kind: str
    path: str
    revision: str
    title: str
    items: list[Item]


def _doc_id(rel_path: str) -> str:
    return hashlib.sha1(f"doc:{rel_path}".encode()).hexdigest()[:24]


def _item_id(rel_path: str, anchor: str) -> str:
    return hashlib.sha1(f"item:{rel_path}#{anchor}".encode()).hexdigest()[:24]


def iter_files(root: Path, *, suffixes: set[str], include: tuple[str, ...] = ()) -> list[Path]:
    """Return files under ``root`` with one of ``suffixes``, sorted by path."""
    if include:
        found: set[Path] = set()
        for pattern in include:
            found.update(p for p in root.glob(pattern) if p.is_file())
        return sorted(found)
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        files.append(path)
    return files


def parse_markdown_spec(rel_path: str, text: str) -> Document:
    """Split a Markdown specification into one item per ``## `` section."""
    lines = text.splitlines()
    doc_title = ""
    sections: list[tuple[str, str, int, int]] = []  # ident, title, start, end
    current: tuple[str, str, int] | None = None
    for number, line in enumerate(lines, start=1):
        if number == 1 and line.startswith("# "):
            doc_title = line[2:].strip()
            continue
        match = _SPEC_HEADING.match(line)
        if not match:
            continue
        if current is not None:
            sections.append((current[0], current[1], current[2], number - 1))
        ident = (match.group("ident") or "").strip()
        title = match.group("title").strip()
        if not ident:
            ident = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").upper()[:24] or f"S{number}"
        current = (ident, title, number)
    if current is not None:
        sections.append((current[0], current[1], current[2], len(lines)))

    revision = digest(text)
    items: list[Item] = []
    for ident, title, start, end in sections:
        body = "\n".join(lines[start:end]).strip("\n")
        item_text = f"## {ident} {title}\n\n{body}" if title else f"## {ident}\n\n{body}"
        items.append(
            Item(
                item_id=_item_id(rel_path, ident),
                kind="spec",
                path=rel_path,
                item_ref=f"{rel_path}#{ident}",
                symbol=ident,
                title=title,
                start_line=start,
                end_line=end,
                text=item_text,
                searchable=searchable_text(ident, title, body),
                revision=revision,
                doc_id=_doc_id(rel_path),
            )
        )
    if not items:  # A spec without ## headings is still usable material.
        body = text.strip("\n")
        items.append(
            Item(
                item_id=_item_id(rel_path, "SPEC"),
                kind="spec",
                path=rel_path,
                item_ref=f"{rel_path}#SPEC",
                symbol="SPEC",
                title=doc_title or Path(rel_path).stem,
                start_line=1,
                end_line=max(1, len(lines)),
                text=body,
                searchable=searchable_text(doc_title, body),
                revision=revision,
                doc_id=_doc_id(rel_path),
            )
        )
    return Document(
        doc_id=_doc_id(rel_path),
        kind="spec",
        path=rel_path,
        revision=revision,
        title=doc_title or Path(rel_path).stem,
        items=items,
    )


def _imports_of(tree: ast.Module) -> tuple[str, ...]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module:
                names.add(module)
            for alias in node.names:
                names.add(f"{module}.{alias.name}" if module else alias.name)
    return tuple(sorted(names))


def _docstring(node: ast.AST) -> str:
    return ast.get_docstring(node, clean=True) or ""  # type: ignore[arg-type]


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = [a.arg for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs]
    if node.args.vararg:
        args.append("*" + node.args.vararg.arg)
    if node.args.kwarg:
        args.append("**" + node.args.kwarg.arg)
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({', '.join(args)})"


def _python_item(
    *,
    rel_path: str,
    kind: str,
    symbol: str,
    title: str,
    node: ast.AST,
    lines: list[str],
    revision: str,
    imports: tuple[str, ...],
    extra_searchable: str = "",
) -> Item:
    start = int(getattr(node, "lineno", 1))
    end = int(getattr(node, "end_lineno", start))
    body = "\n".join(lines[start - 1 : end])
    doc = _docstring(node)
    anchor = symbol
    return Item(
        item_id=_item_id(rel_path, anchor),
        kind=kind,
        path=rel_path,
        item_ref=f"{rel_path}#{anchor}",
        symbol=symbol,
        title=title or symbol,
        start_line=start,
        end_line=end,
        text=body,
        searchable=searchable_text(symbol, rel_path, doc, body, extra_searchable),
        revision=revision,
        doc_id=_doc_id(rel_path),
        imports=imports,
    )


def parse_python(rel_path: str, text: str, *, kind: str) -> Document:
    """Extract module, class, function and method items from Python source."""
    revision = digest(text)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        lines = text.splitlines()
        item = Item(
            item_id=_item_id(rel_path, "FILE"),
            kind=kind,
            path=rel_path,
            item_ref=f"{rel_path}#FILE",
            symbol=Path(rel_path).stem,
            title=Path(rel_path).stem,
            start_line=1,
            end_line=max(1, len(lines)),
            text=text,
            searchable=searchable_text(rel_path, text),
            revision=revision,
            doc_id=_doc_id(rel_path),
        )
        return Document(_doc_id(rel_path), kind, rel_path, revision, Path(rel_path).stem, [item])

    lines = text.splitlines()
    imports = _imports_of(tree)
    is_test_file = kind == "test"
    items: list[Item] = []

    module_symbol = Path(rel_path).with_suffix("").as_posix().replace("/", ".")
    module_doc = _docstring(tree)
    items.append(
        Item(
            item_id=_item_id(rel_path, "module"),
            kind=kind,
            path=rel_path,
            item_ref=f"{rel_path}#module",
            symbol=module_symbol,
            title=f"module {module_symbol}",
            start_line=1,
            end_line=max(1, len(lines)),
            text=text,
            searchable=searchable_text(module_symbol, rel_path, module_doc, text, " ".join(imports)),
            revision=revision,
            doc_id=_doc_id(rel_path),
            imports=imports,
        )
    )

    def add_function(node: ast.FunctionDef | ast.AsyncFunctionDef, parent: str | None) -> None:
        symbol = f"{parent}.{node.name}" if parent else node.name
        if is_test_file and not node.name.startswith("test_"):
            return
        item_kind = "test" if is_test_file else "code"
        items.append(
            _python_item(
                rel_path=rel_path,
                kind=item_kind,
                symbol=symbol,
                title=_signature(node),
                node=node,
                lines=lines,
                revision=revision,
                imports=imports,
                extra_searchable=" ".join(imports),
            )
        )

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add_function(node, None)
        elif isinstance(node, ast.ClassDef):
            if is_test_file:
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        add_function(child, node.name)
                continue
            items.append(
                _python_item(
                    rel_path=rel_path,
                    kind=kind,
                    symbol=node.name,
                    title=f"class {node.name}",
                    node=node,
                    lines=lines,
                    revision=revision,
                    imports=imports,
                )
            )
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add_function(child, node.name)

    return Document(
        doc_id=_doc_id(rel_path),
        kind=kind,
        path=rel_path,
        revision=revision,
        title=Path(rel_path).as_posix(),
        items=items,
    )


def load_document(root: Path, path: Path, kind: str) -> Document:
    rel_path = path.relative_to(root).as_posix()
    text = path.read_text(encoding="utf-8", errors="replace")
    if kind == "spec":
        return parse_markdown_spec(rel_path, text)
    return parse_python(rel_path, text, kind=kind)


def discover(root: Path, spec_dirs: tuple[str, ...], code_dirs: tuple[str, ...], test_dirs: tuple[str, ...]) -> list[tuple[Path, str]]:
    """Return ``(path, kind)`` pairs for the repository, skipping overlaps."""
    seen: dict[Path, str] = {}
    for pattern in test_dirs:
        for path in iter_files(root, suffixes=CODE_SUFFIXES, include=(pattern,)):
            if path.name.startswith("test_") or path.name == "conftest.py":
                seen[path] = "test"
    for pattern in code_dirs:
        for path in iter_files(root, suffixes=CODE_SUFFIXES, include=(pattern,)):
            seen.setdefault(path, "code")
    for pattern in spec_dirs:
        for path in iter_files(root, suffixes=SPEC_SUFFIXES, include=(pattern,)):
            seen.setdefault(path, "spec")
    return sorted(seen.items(), key=lambda pair: pair[0].as_posix())
