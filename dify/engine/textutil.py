"""中文短文本工具：分词（字符 n-gram）与标题化。

游戏里所有文本都是短句（一句玩家动作 + 一句 NPC 记忆），所以不引入任何分词
库：汉字取字面二元组，西文取词，标点/空白直接丢掉。这样同一份代码在评审
机器上得到的分词结果完全一致，全文索引与向量通道都可复现。
"""

from __future__ import annotations

import re
import unicodedata

_CJK_RANGES = (
    (0x3400, 0x4DBF),  # CJK 扩展 A
    (0x4E00, 0x9FFF),  # CJK 基本区
    (0xF900, 0xFAFF),  # 兼容表意
    (0x3040, 0x30FF),  # 假名（容许日文客串）
)

_ASCII_WORD = re.compile(r"[A-Za-z0-9_]+")
_SPACE = re.compile(r"\s+")


def is_cjk(char: str) -> bool:
    code = ord(char)
    for low, high in _CJK_RANGES:
        if low <= code <= high:
            return True
    return False


def normalize(text: str) -> str:
    """全角转半角、压缩空白、去首尾空格。"""

    return _SPACE.sub(" ", unicodedata.normalize("NFKC", text or "")).strip()


def ascii_words(text: str) -> list[str]:
    return _ASCII_WORD.findall(normalize(text))


def cjk_bigrams(text: str) -> list[str]:
    """连续的汉字串切成二元组；单字串保留单字。"""

    out: list[str] = []
    run: list[str] = []
    for char in normalize(text):
        if is_cjk(char):
            run.append(char)
            continue
        if run:
            out.extend(_gram_run(run))
            run = []
    if run:
        out.extend(_gram_run(run))
    return out


def _gram_run(run: list[str]) -> list[str]:
    if len(run) == 1:
        return [run[0]]
    return ["".join(run[i : i + 2]) for i in range(len(run) - 1)]


def keywords(text: str, limit: int = 8) -> list[str]:
    """给全文检索用的关键词：汉字二元组 + 西文词，按出现顺序去重。"""

    seen: dict[str, None] = {}
    for token in cjk_bigrams(text) + ascii_words(text):
        if len(token) >= 2:
            seen.setdefault(token, None)
        if len(seen) >= limit:
            break
    return list(seen)


def fts_query(text: str) -> str:
    """把玩家输入压成 seekdb 自然语言模式能用的检索式。"""

    tokens = keywords(text)
    return " ".join(tokens) if tokens else normalize(text)


def clip(text: str, limit: int = 60) -> str:
    text = normalize(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"
