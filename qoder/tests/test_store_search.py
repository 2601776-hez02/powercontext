from __future__ import annotations

from pathlib import Path

from zhixu.corpus import ingest, seed_aliases
from zhixu.search import ask, index_inventory, search
from zhixu.store import Store


def _ingest(store: Store, corpus_dir: Path) -> None:
    stats = ingest(store, corpus_dir)
    assert stats.added >= 2
    assert stats.chunks >= 2


def test_schema_and_indexes(store: Store, corpus_dir: Path) -> None:
    _ingest(store, corpus_dir)
    names = {item["index"] for item in index_inventory(store)}
    assert {"ft_body", "vk_embedding"} <= names
    assert store.info().schema_version == "1"


def test_ingest_is_idempotent(store: Store, corpus_dir: Path) -> None:
    _ingest(store, corpus_dir)
    before = (store.count("zx_documents"), store.count("zx_chunks"))
    stats = ingest(store, corpus_dir)
    assert stats.added == 0 and stats.updated == 0
    assert (store.count("zx_documents"), store.count("zx_chunks")) == before


def test_changed_document_replaces_chunks(store: Store, corpus_dir: Path) -> None:
    _ingest(store, corpus_dir)
    target = corpus_dir / "01-hnsw.md"
    original = target.read_text(encoding="utf-8")
    target.write_text(original + "\n\n## 新增小节\n\n" + "补充内容。" * 30 + "\n", encoding="utf-8")
    stats = ingest(store, corpus_dir)
    assert stats.updated == 1
    assert store.count("zx_documents") == 3
    row = store.fetch_one("SELECT char_count FROM zx_documents WHERE path = %s", ("01-hnsw.md",))
    assert row is not None and int(row["char_count"]) > len(original)


def test_search_returns_citations_that_round_trip(store: Store, corpus_dir: Path) -> None:
    _ingest(store, corpus_dir)
    hits, meta = search(store, "efSearch 召回率 延迟", limit=5)
    assert hits, "应当命中 HNSW 笔记"
    assert meta.latency_ms >= 0
    top = hits[0]
    assert top.document_path == "01-hnsw.md"
    assert top.start_line <= top.end_line
    assert top.char_start < top.char_end
    assert all(channel in {"fts", "vector"} for channel in top.channels)


def test_alias_expansion_improves_recall(store: Store, corpus_dir: Path) -> None:
    _ingest(store, corpus_dir)
    alias_path = Path(__file__).resolve().parents[1] / "data" / "seed" / "aliases.json"
    seed_aliases(store, alias_path)
    hits, meta = search(store, "HNSW 参数 怎么调", limit=5)
    assert hits
    assert "向量索引" in meta.aliases


def test_ask_respects_budget(store: Store, corpus_dir: Path) -> None:
    _ingest(store, corpus_dir)
    for budget in (512, 900, 1800):
        result = ask(store, "efSearch 召回率", budget_bytes=budget)
        assert result["content_bytes"] <= budget
        assert result["content_bytes"] == len(result["content"].encode("utf-8"))
        assert result["content"].startswith("BEGIN_ZHIXU_CONTEXT_V1")
        assert result["content"].endswith("END_ZHIXU_CONTEXT_V1")
        for item in result["items"]:
            citation = item["citation"]
            assert 0 <= citation["char_start"] < citation["char_end"]


def test_data_survives_reopen(tmp_path: Path, corpus_dir: Path) -> None:
    first = Store(tmp_path / "persist")
    first.open()
    _ingest(first, corpus_dir)
    counts = (first.count("zx_documents"), first.count("zx_chunks"))
    first.close()

    second = Store(tmp_path / "persist")
    second.open()
    try:
        assert (second.count("zx_documents"), second.count("zx_chunks")) == counts
        hits, _ = search(second, "efSearch", limit=3)
        assert hits
    finally:
        second.close()
