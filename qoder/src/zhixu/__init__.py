"""知序 ZhiXu —— 学研工作台。

把散落在 Markdown 里的资料、任务和进度收进一个可回查出处的本地工作台，
核心存储使用嵌入式 seekdb（pylibseekdb），全文索引（ngram）+ HNSW 向量索引双通道检索。
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
