from __future__ import annotations

import math

from zhixu.embedding import DIM, cosine, embed, embed_literal
from zhixu.text import chunk_content, line_span, make_snippet, parse_document


def test_parse_front_matter() -> None:
    raw = "---\ntitle: 标题\nkind: spec\ntags: a, b\n---\n\n# 正文标题\n\n内容"
    document = parse_document(raw)
    assert document.title == "标题"
    assert document.kind == "spec"
    assert document.tags == ("a", "b")
    assert raw[document.body_start :].startswith("\n# 正文标题")
    assert raw[document.body_start :].strip().startswith("# 正文标题")


def test_chunk_offsets_are_exact() -> None:
    content = "# 标题\n\n## 小节一\n\n" + "这是第一段内容。" * 40 + "\n\n## 小节二\n\n第二段内容。"
    chunks = chunk_content(content, target=200, overlap=40)
    assert chunks, "应当切出至少一个片段"
    for chunk in chunks:
        assert content[chunk.start : chunk.end] == chunk.text
        assert 0 <= chunk.start < chunk.end <= len(content)
        assert chunk.text.strip() == chunk.text
    assert len(chunks) >= 2


def test_heading_only_and_tiny_sections_are_dropped() -> None:
    content = "# 极短资料\n\n## 空小节\n\n## 只有一句\n\n一句话。"
    chunks = chunk_content(content)
    assert chunks == []


def test_line_span() -> None:
    content = "第一行\n第二行\n第三行"
    assert line_span(content, 0, 3) == (1, 1)
    assert line_span(content, 4, 7) == (2, 2)
    assert line_span(content, 0, len(content)) == (1, 3)


def test_snippet_prefers_query_window() -> None:
    body = "无关内容。" * 30 + "这里专门讨论 efSearch 与召回率的关系，延迟也会上升。" + "尾部内容。" * 20
    snippet = make_snippet(body, "efSearch 召回率", width=80)
    assert "efSearch" in snippet
    assert snippet.startswith("…") and snippet.endswith("…")


def test_embedding_shape_and_similarity() -> None:
    vector = embed("HNSW 的 efSearch 影响召回率")
    assert len(vector) == DIM
    assert math.isclose(math.sqrt(sum(value * value for value in vector)), 1.0, rel_tol=1e-6)
    assert embed("HNSW 的 efSearch 影响召回率") == vector

    related = embed("HNSW efSearch 召回率 延迟")
    unrelated = embed("周报模板 四段结构 下周计划")
    assert cosine(vector, related) > cosine(vector, unrelated)


def test_embed_literal_is_parseable() -> None:
    literal = embed_literal("中文检索")
    assert literal.startswith("[") and literal.endswith("]")
    values = [float(part) for part in literal[1:-1].split(",")]
    assert len(values) == DIM
