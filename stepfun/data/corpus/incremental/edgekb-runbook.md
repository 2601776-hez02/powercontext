# EdgeKB 运维手册

服务默认监听 `127.0.0.1:5173`，可用 `PORT` 环境变量改写。启动命令是 `npm start`，本质是 `node server/index.mjs`。

数据目录默认是 `data/seekdb`（嵌入式模式），里面是 seekdb 的数据库文件与日志；删掉这个目录等于清空全部数据，但需要重启服务。更换目录用 `SEEKDB_PATH`。

对外接口：

- `GET /api/health`：服务与数据库是否就绪；
- `GET /api/status`：集合、文档数、片段数、seekdb 版本；
- `POST /api/import`：提交片段与向量，可选是否刷新索引；
- `POST /api/search`：混合检索，返回排序结果与实际执行的 SQL；
- `POST /api/reset`：清空知识库（删除集合并按同一 schema 重建）。

模型文件放在 `models/` 下，由 `npm run fetch-models` 拉取；浏览器运行时放在 `web/vendor/`，由 `npm run fetch-vendor` 拉取。两者都不进版本库。

排障顺序：先看 `/api/health`，再看服务端日志里的 `[server] seekdb ready` 行，最后在浏览器控制台确认 `crossOriginIsolated` 为 true。
