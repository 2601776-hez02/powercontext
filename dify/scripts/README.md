# 忘川酒馆 · 部署脚本说明

这里的脚本把「Dify 1.19.0 + seekdb + PowerContext + 酒馆引擎」四件事按顺序拉起来。
所有脚本都可以重复执行（幂等），默认全部跑在**一台 Linux 机器**上，用 Docker
Compose 编排，不依赖任何云服务。

| 脚本 | 作用 | 何时跑 |
| --- | --- | --- |
| `01_check.sh` | 检查 Docker/Compose 版本、端口占用、磁盘与内存 | 第一次，1 分钟 |
| `00_bringup.sh` | 一条命令：检查 → 起 seekdb → 起引擎 → 跑验证 → 自检（评审用这个） | 第一步 |
| `02_dify.sh --dry-run` | 只取源码 + 写 .env/security.env + `compose config` 校验，不启动容器（`DRY_RUN=1`） | 想先看配置时 |
| `02b_seekdb.sh` | 只起 seekdb（评审已有自己的 Dify 时用），并把连接信息写进 `deploy/.env` | 第 1 步的替代 |
| `02_dify.sh` | 下载 Dify 1.19.0 的 docker 目录，写入 `.env`/`security.env`，`compose up -d` | 第一次，10~20 分钟（拉镜像） |
| `03_engine.sh` | 建 Python 虚拟环境、装引擎依赖、初始化 seekdb 表与世界数据、起 `:9100` 服务 | 第一次，2 分钟 |
| `04_powercontext.sh` | 装 PowerContext 1.5.0 并起 `:8000`（记忆层，可跳过） | 第一次，3 分钟 |
| `05_verify.sh` | 跑验证样例：引擎回放 + Dify 应用 API 调用 + seekdb 证据 | 每次改动后 |
| `06_selfcheck.sh` | 声明一致性自检（58 条 PASS/FAIL）→ `docs/conformance.md` | 改规则或改文档后 |
| `12_reviewer_check.sh` | **一键自检**：引擎 / 自检 / 样例 / 审计 / 文档 / 单测 / DSL 一致性，最后打一张汇总表 | 评审想快速确认时 |

## 端口

| 端口 | 服务 | 说明 |
| --- | --- | --- |
| `8080` | Dify Web/Console | Nginx 入口，`nginx` 容器 |
| `2881` | seekdb | Dify 知识库向量存储 + 酒馆业务表（同一实例） |
| `9100` | 酒馆引擎 | Dify 的 HTTP Request 节点调用它 |
| `8000` | PowerContext | 跨会话「酒馆对你的记忆」 |

## 关于 seekdb 的两个用途

1. **Dify 知识库的向量存储**：`.env` 里 `VECTOR_STORE=seekdb`、`COMPOSE_PROFILES=seekdb,postgresql`，
   Dify 会把知识库的集合与向量写进 seekdb（`oceanbase/seekdb` 容器，库名 `test`）。
2. **酒馆自己的业务库**：引擎用 MySQL 协议连同一个 seekdb 实例，建 `tw_*` 表，
   世界状态、账本、NPC 记忆（全文 + HNSW 向量索引）都在这里。

两处用的是同一个 seekdb 实例，但表前缀不同，互不干扰；关掉知识库也不影响玩法。

## 只需要最少依赖时

只想要 seekdb（不跑 PowerContext）：

```bash
POWERCONTEXT_ENABLED=0 ./scripts/03_engine.sh
```

引擎在 PowerContext 不可用时会自动降级：跨会话传说只写进 seekdb 的 `tw_legend` 表，
其它玩法完全不变（`/health` 里 `powercontext.available` 会是 `false`）。

| `10_stale_claims.sh` | 旧文案扫描：检查仓库里是否还残留已废弃的说法（例如已被新引导取代的旧说法） | 改过规则/引导后 |
| `09_docs_lint.sh` | 文档自检：所有 markdown 表格列数一致 + README 相对链接都存在 | 改过 README 后 |
