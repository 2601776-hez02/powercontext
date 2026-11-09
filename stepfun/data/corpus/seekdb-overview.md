# seekdb 是什么

seekdb 是 OceanBase 推出的轻量级 AI 原生数据库。它有两种形态：嵌入式（进程内打开一个数据目录，不需要独立的服务进程）与服务端（监听 2881 端口，兼容 MySQL 协议，可以用任何 MySQL 客户端连接）。两种形态共用同一套 SQL 与同一套索引能力，因此同一份建表语句与查询语句在本地嵌入式与远端服务端上都能直接跑通。

在线能力上，seekdb 把三类检索放进同一个数据库：

1. 向量索引：支持 HNSW 与 IVF 系列，向量列类型写作 `VECTOR(512)`，距离度量可选 cosine、l2、inner_product，查询时用 `cosine_distance(embedding, ?)` 或 `APPROXIMATE` 关键字走索引。
2. 全文索引：倒排索引配合分词器，支持 `ngram`、`ngram2`、`beng`、`ik`、`space` 等分析器，用 `MATCH(column) AGAINST ('关键词' IN NATURAL LANGUAGE MODE)` 做相关度检索。
3. 标量过滤：普通关系列与 JSON 元数据列都可以参与 WHERE 条件，也能和向量、全文条件写在同一条 SQL 里。

对个人知识库这类场景来说，seekdb 的价值在于“一份数据、一条 SQL”：文档正文、元数据和向量都落在同一张表里，检索时不需要在数据库和应用之间来回搬运，也不需要额外维护一个向量库再加一个搜索引擎。
