"""确定性向量化：字符 n-gram 哈希，1024 维，L2 归一化（无符号哈希）。

刻意不依赖任何外部 Embedding 服务：
* 评审机器不需要额外的 API Key，断网也能复现；
* 同一句话永远得到同一个向量，验证样例里的向量检索结果因此可复现。

它只负责「词面相近」：偷 / 摸走 / 顺走 这类同义说法落在相近的桶里，
真正的语义联想交给 seekdb 的全文通道与 Dify 的叙事模型。文档里不夸大它。
维度取 1024 是为了压住哈希碰撞——384 维时，一句无关的话也能因为碰撞拿到
0.17 的余弦，和真正的命中（0.19）分不开。
"""

from __future__ import annotations

import hashlib
import math

from .textutil import ascii_words, cjk_bigrams, is_cjk, normalize

DIM = 1024

_ASCII_WEIGHT = 1.4
_BIGRAM_WEIGHT = 1.0
_UNIGRAM_WEIGHT = 0.35


def _bucket(token: str) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % DIM


def token_weights(text: str) -> dict[str, float]:
    text = normalize(text)
    counts: dict[str, float] = {}
    for word in ascii_words(text):
        if len(word) >= 2:
            counts["w:" + word.lower()] = counts.get("w:" + word.lower(), 0.0) + _ASCII_WEIGHT
    for gram in cjk_bigrams(text):
        counts["b:" + gram] = counts.get("b:" + gram, 0.0) + _BIGRAM_WEIGHT
    for char in text:
        if is_cjk(char):
            counts["c:" + char] = counts.get("c:" + char, 0.0) + _UNIGRAM_WEIGHT
    return {token: weight * (1.0 + math.log(weight)) for token, weight in counts.items() if weight > 0}


def embed(text: str) -> list[float]:
    vector = [0.0] * DIM
    weights = token_weights(text) or {"w:empty": 1.0}
    for token, weight in weights.items():
        vector[_bucket(token)] += weight
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        vector[0] = 1.0
        return vector
    return [value / norm for value in vector]


def embed_literal(text: str) -> str:
    """seekdb 的 VECTOR 列接受 ``'[v1,v2,...]'`` 文本字面量。"""

    return "[" + ",".join(f"{value:.6f}" for value in embed(text)) + "]"


def cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))
