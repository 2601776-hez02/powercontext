# 知序 ZhiXu 项目规则（Always Apply）

- 语言：面向用户的文案、注释、文档一律使用简体中文；代码标识符用英文。
- 存储：所有数据（资料、片段、向量、任务、事件、周报）必须落在 seekdb（`pylibseekdb`）里；
  禁止用自制 SQLite / JSON / 内存索引替代检索，禁止 mock seekdb 通过测试。
- 相似度：片段表同时使用 `FULLTEXT ... WITH PARSER ngram` 与 `VECTOR ... HNSW`；
  向量排序 SQL 必须带 `APPROXIMATE`，并保留 `EXPLAIN` 自检。
- 引用：任何检索结果都必须携带 `path / heading / start_line / end_line / char_start / char_end`，
  且 `原文[char_start:char_end]` 与展示正文一致。
- 初始化：DDL 使用 `IF NOT EXISTS`；入库按 `sha256` 幂等；写操作使用事务。
- 测试：改动核心逻辑前先补 pytest；测试不得依赖网络、外部模型或真实密钥。
- 站点：`site/` 为纯静态；`site/data/demo.json` 只能由 `python -m zhixu export-site` 生成，
  不得手写演示数据；每次发布前重新导出并检查 `content_bytes <= budget_bytes`。
- 依赖：只允许标准库、`pylibseekdb`、`PyMySQL`（测试另加 `pytest`）。
