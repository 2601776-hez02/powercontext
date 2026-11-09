"""纯文本工具：切分、分块、摘要、行号。

这里刻意不做任何会改变字符偏移的归一化——片段的 ``start``/``end`` 必须能在
原文里精确回查（``content[start:end] == text``），否则引用会失真。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
_HEADING_ONLY_RE = re.compile(r"^#{1,4}\s+[^\n]*\n?\s*$")
_ASCII_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_+\-.#]*", re.IGNORECASE)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_SENTENCE_END = "。！？!?；;…\n"
_FRONT_MATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


@dataclass(frozen=True)
class Section:
    heading: str
    start: int
    end: int


@dataclass(frozen=True)
class Chunk:
    heading: str
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class ParsedDocument:
    title: str
    kind: str
    tags: tuple[str, ...]
    content: str
    body_start: int


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_cjk(char: str) -> bool:
    return bool(_CJK_RE.match(char))


def ascii_words(text: str) -> list[str]:
    return [m.group(0).lower() for m in _ASCII_WORD_RE.finditer(text)]


def cjk_bigrams(text: str) -> list[str]:
    chars = [c for c in text if is_cjk(c)]
    grams = [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]
    return grams


def query_terms(query: str) -> list[str]:
    """把查询拆成用于高亮的词项（英文单词 + 中文二元组）。"""

    terms: list[str] = []
    seen: set[str] = set()
    for word in ascii_words(query):
        if len(word) > 1 and word not in seen:
            seen.add(word)
            terms.append(word)
    for gram in cjk_bigrams(query):
        if gram not in seen:
            seen.add(gram)
            terms.append(gram)
    return terms


def make_snippet(body: str, query: str, width: int = 120) -> str:
    """找一段最能覆盖查询词项的窗口，两端按需补省略号。"""

    text = body.strip()
    if len(text) <= width:
        return text
    terms = query_terms(query)
    if not terms:
        return text[:width].rstrip() + "…"

    lower = text.lower()
    best_pos, best_score = 0, -1
    step = max(1, width // 4)
    for pos in range(0, max(1, len(text) - width + 1), step):
        window = lower[pos : pos + width]
        score = sum(1 for term in terms if term in window)
        if score > best_score:
            best_pos, best_score = pos, score
        if score == len(terms):
            break
    start = max(0, best_pos - 12)
    end = min(len(text), start + width)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


def line_span(content: str, start: int, end: int) -> tuple[int, int]:
    """把字符区间换算成 1 起始的行号区间（闭区间）。"""

    start = max(0, min(start, len(content)))
    end = max(start, min(end, len(content)))
    start_line = content.count("\n", 0, start) + 1
    # end 为半开区间：若正好落在行首，不应多算一行
    tail = end - 1 if end > start else start
    end_line = content.count("\n", 0, tail) + 1
    return start_line, end_line


def parse_document(raw: str) -> ParsedDocument:
    """解析可选的 front matter，并推断标题/类型/标签。"""

    meta: dict[str, str] = {}
    body_start = 0
    match = _FRONT_MATTER_RE.match(raw)
    if match:
        body_start = match.end()
        for line in match.group(1).splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip().lower()] = value.strip().strip("\"'")

    title = meta.get("title", "")
    if not title:
        for line in raw[body_start:].splitlines():
            heading = _HEADING_RE.match(line)
            if heading:
                title = heading.group(2).strip()
                break
    if not title:
        title = "未命名资料"

    kind = meta.get("kind", "note").strip() or "note"
    tags_raw = meta.get("tags", "")
    tags = tuple(tag.strip() for tag in re.split(r"[,，、;；\s]+", tags_raw) if tag.strip())
    return ParsedDocument(title=title, kind=kind, tags=tags, content=raw, body_start=body_start)


def split_sections(content: str, body_start: int = 0) -> list[Section]:
    """按 Markdown 标题切成小节；标题前的正文算作一个空标题小节。"""

    lines = content.splitlines(keepends=True)
    offsets: list[int] = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line)

    starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if offsets[index] < body_start:
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            starts.append((offsets[index], heading.group(2).strip()))

    sections: list[Section] = []
    if not starts:
        return [Section(heading="正文", start=body_start, end=len(content))]
    first_offset = starts[0][0]
    if first_offset > body_start:
        prefix = content[body_start:first_offset]
        if prefix.strip():
            sections.append(Section(heading="正文", start=body_start, end=first_offset))
    for index, (start, heading) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(content)
        sections.append(Section(heading=heading, start=start, end=end))
    return sections


def _sentence_cut(content: str, start: int, end: int) -> int:
    """从 end 往回找一个句子边界，避免把一句话劈开。"""

    floor = start + max(1, (end - start) // 2)
    for pos in range(end, floor, -1):
        if content[pos - 1] in _SENTENCE_END:
            return pos
    return end


def chunk_content(
    content: str,
    *,
    body_start: int = 0,
    target: int = 520,
    overlap: int = 80,
) -> list[Chunk]:
    """按小节 + 目标长度分块，片段坐标严格指向原文。"""

    chunks: list[Chunk] = []
    for section in split_sections(content, body_start=body_start):
        text_stripped = content[section.start : section.end].strip()
        if not text_stripped:
            continue
        start = section.start
        while start < section.end:
            end = min(start + target, section.end)
            if end < section.end:
                end = _sentence_cut(content, start, end)
            chunk_start, chunk_end = start, end
            # 去掉首尾空白但保持坐标与原文一致
            while chunk_start < chunk_end and content[chunk_start].isspace():
                chunk_start += 1
            while chunk_end > chunk_start and content[chunk_end - 1].isspace():
                chunk_end -= 1
            if chunk_end - chunk_start >= 12:
                text = content[chunk_start:chunk_end]
                # 只有标题行、或过短的小节不单独成块：它们缺少可引用的信息量
                if not _HEADING_ONLY_RE.match(text) and len(text) >= 48:
                    chunks.append(
                        Chunk(
                            heading=section.heading,
                            text=text,
                            start=chunk_start,
                            end=chunk_end,
                        )
                    )
            if end >= section.end:
                break
            next_start = end - overlap
            if next_start <= start:
                next_start = end
            start = next_start
    return chunks
