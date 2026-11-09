"""忘川酒馆引擎包。

模块划分：
* ``parse``      一句话 → 结构化动作（模型给了 fields 就校验，没给就用关键词）
* ``rules``      动作 → 判定结果 + 记忆素材（纯函数、可单测）
* ``db``         seekdb 连接、DDL、事务
* ``recall``     NPC 记忆的写入与混合检索（全文 + 向量 + RRF）
* ``powercontext`` 跨会话记忆（HTTP，可降级）
* ``game``       一回合的编排（读状态 → 判定 → 落库 → 检索 → 交叙事）
* ``app``        FastAPI 外壳（Dify HTTP Request 节点调用）
* ``cli``        命令行：init / act / demo / sql / mind / ledger
"""

__all__ = ["game", "rules", "parse", "db", "recall", "powercontext", "app", "cli"]
__version__ = "1.0.0"
