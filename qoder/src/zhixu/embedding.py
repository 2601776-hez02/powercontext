"""本地向量化：字符 n-gram 哈希 + 次线性词频，384 维、L2 归一化。

不依赖任何外部模型或 API：同样的文本在任何机器上都得到同样的向量，
因此向量索引可以在离线环境复现，也不会把资料正文发到第三方服务。
字符二元组让中文专有名词、英文缩写都有稳定表示；配合全文索引构成混合检索。
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter

from .text import ascii_words, cjk_bigrams, is_cjk

DIM = 384

_ASCII_WEIGHT = 1.4
_BIGRAM_WEIGHT = 1.0
_UNIGRAM_WEIGHT = 0.35


def _bucket(token: str) -> tuple[int, float]:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    index = value % DIM
    sign = 1.0 if (value >> 63) & 1 else -1.0
    return index, sign


def token_weights(text: str) -> dict[str, float]:
    """返回 token -> 权重（次线性词频 × 通道权重）。"""

    counts: Counter[str] = Counter()
    for word in ascii_words(text):
        if len(word) >= 2:
            counts["w:" + word] += _ASCII_WEIGHT
    for gram in cjk_bigrams(text):
        counts["b:" + gram] += _BIGRAM_WEIGHT
    for char in text:
        if is_cjk(char):
            counts["c:" + char] += _UNIGRAM_WEIGHT
    return {token: weight * (1.0 + math.log(weight)) for token, weight in counts.items()}


def embed(text: str) -> list[float]:
    vector = [0.0] * DIM
    weights = token_weights(text)
    if not weights:
        weights = {"w:empty": 1.0}
    for token, weight in weights.items():
        index, sign = _bucket(token)
        vector[index] += sign * weight
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        vector[0] = 1.0
        return vector
    return [value / norm for value in vector]


def embed_literal(text: str) -> str:
    """seekdb VECTOR 列接受 ``'[v1,v2,...]'`` 形式的文本字面量。"""

    return "[" + ",".join(f"{value:.6f}" for value in embed(text)) + "]"


def cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))
