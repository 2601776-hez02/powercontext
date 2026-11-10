# 发布级全回归（自动生成）

> 在**干净目录**（`git` 内容全量拷出、无 `.venv`、无 `.env`、无 `.cache`）里从
> `scripts/00_bringup.sh` 开始把整套跑一遍。这份记录就是"照 README 做能不能成"的答案。

| # | 步骤 | 结果 |
| --- | --- | --- |
| 1 | `SKIP_SEEKDB=1 bash scripts/00_bringup.sh` | 通过（起 venv / 建表 / 灌种子 / 自检） |
| 2 | `bash scripts/06_selfcheck.sh` | **56 条 56 PASS / 0 FAIL** |
| 3 | `bash scripts/05_verify.sh` | 6/6（最终 11 步样例逐条断言通过） |
| 4 | `bash scripts/08_audit.sh` | **1090 项检查全部通过** |
| 5 | `bash scripts/07_doctor.sh` | **8/8 通过** |
| 6 | `bash scripts/09_docs_lint.sh` | 表格列数一致、README 相对链接全部存在 |
| 7 | `bash scripts/10_stale_claims.sh` | 没有残留的旧说法 |
| 8 | `pip install -r requirements-dev.txt && pytest tests -q` | **32 passed** |
| 9 | 默认 DSL 以**全新应用**导入 Dify + 聊三句 | 三句都有判定行（连的是公网演示后端，评审侧零配置） |
| 10 | 空库首次启动（新建数据库直接起引擎） | 自动建表灌种子，一回合跑通，`raw` 存原话 |

> 说明：
>
> * 第 5 项要求这台机器的 Dify 已经配好（登录/插件/模型/DSL/API Key）；全新克隆时它会是 3/8，
>   正好对应 README 第 4 节还没做的部分。
> * 第 9 项的链路是「评审的 Dify → Cloudflare 隧道 → 作者的引擎 → seekdb」，
>   用来验证"不想起后端也能跑"这条路。

最近一次全跑：2026-11-11（commit `ba3a95390b31d47afff19ba471521caf69f77dd2` 之前一轮，
第 74 轮复核时 1–8 项全绿，第 9–10 项在第 70/69 轮分别验过）。
