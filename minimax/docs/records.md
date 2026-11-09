# 技术使用记录

本文件汇总三项指定技术在验证样例中的实际使用情况。原始记录都在 `records/`，
可以逐条核对；这里只做索引和小结。

## 0. 运行环境

| 项 | 值 | 出处 |
| --- | --- | --- |
| 主机 | 阿里云 ECS，Alibaba Cloud Linux 3，x86_64，4 vCPU / 16 GiB | `records/replay/*/replay.log` |
| Python | 3.12.16（venv） | `records/events.jsonl` |
| seekdb | 嵌入式 `pylibseekdb`，`5.7.25-OceanBase seekdb-v1.4.0.0` | `records/init.json` |
| PowerContext | `powercontext[cli,server]==1.2.0`，SQLite 后端，`http://127.0.0.1:8138`（默认端口被占用时自动顺延） | `records/replay/*/doctor.log` |
| MiniMax | `MiniMax-M3`，`https://api.minimaxi.com/v1` | `sdd_context/config.py` |

## 1. seekdb 使用记录

**存储**：`sdd init` 建 4 张表 + 1 个全文索引（`records/init.json`）：

```
sdd_documents  sdd_items  sdd_imports  sdd_document_history
ix_sdd_item_search  FULLTEXT ON sdd_items(searchable) WITH PARSER SPACE
```

**写入**：两次导入（`records/imports/`）

| import_id | 文档 | 条目 | spec revision | 变更 |
| --- | --- | --- | --- | --- |
| `20261109T133214296735` | 10 | 50 | `1e10174f8aaa5bc1` | added 10 |
| `20261109T133225022549` | 10 | 50 | `18471ec477520271` | changed 1（`docs/specs/pricing.md`） |

条目分布：spec 10 / code 20 / test 20。

**检索**：上下文包里保存了本次实际执行的 SQL（`artifacts/context-pack-v1.json` →
`retrieval.queries`），例如：

```sql
-- fulltext（46 行候选）
SELECT item_id, kind, path, item_ref, symbol, title, start_line, end_line, revision, text,
       MATCH(searchable) AGAINST (%s IN NATURAL LANGUAGE MODE) AS score
FROM sdd_items
WHERE MATCH(searchable) AGAINST (%s IN NATURAL LANGUAGE MODE)
ORDER BY score DESC, item_ref LIMIT %s;

-- 精确符号命中（36 行候选）
SELECT ... FROM sdd_items
WHERE LOWER(symbol) LIKE %s OR LOWER(path) LIKE %s OR LOWER(title) LIKE %s
ORDER BY kind, item_ref LIMIT %s;
```

直接查库复核：

```bash
sdd db stats
sdd db query --sql "SELECT kind, COUNT(*) FROM sdd_items GROUP BY kind"
sdd db query --sql "SELECT path, change_kind, revision_before, revision_after FROM sdd_document_history"
```

## 2. PowerContext 使用记录

协议：HTTP JSON（`POST /v1/scope-bindings/resolve`、`/v1/memory/remember`、
`/v1/memory/search`、`/v1/sources/content`），`powercontext[cli,server]==1.2.0`。

| 动作 | 请求要点 | 结果 | 记录 |
| --- | --- | --- | --- |
| Scope 解析 | `{"allow_default": true}` | `scope_id=scp_25dxjx8wdp1j6h2a7983v7x7y9` | `records/powercontext/*.json` |
| 写任务状态 | `kind="sdd-task-state"`，正文 587 B | `memory.revision` 1 → 3（每次更新 +1），`entry.version` 1 | `state-coupon-policy-r1.json`、`verification-*-r2/r3.json` |
| 存上下文包 | `source_id="sdd-context-coupon-policy-<import_id>"` | `status=accepted`，`position=2` | `records/events.jsonl` 第 2 行 |
| 重启后读状态 | 新进程 `POST /v1/memory/search` `query="SDD task state coupon-policy"` | 读回 revision 1，`done/todo` 完整 | `records/replay/*/state-show.log` |
| 重启 PowerContext 后再读 | 停进程 → 重启 → 再查 | 内容不变 | `records/replay/*/state-show-after-restart.log` |
| 写入验证结果 | 两次 `sdd verify` | r2：`1 passed, 6 failed`（改前）；r3：`17 passed`（改后） | `verification-coupon-policy-r2.json`、`-r3.json` |

状态机：`in_progress` →（目标测试失败）→ `in_progress` →（全绿）→ `verified`。
上下文包里 `task_state` 段就是读回来的这份 JSON（见 `context-with-state.md`）。

## 3. MiniMax 使用记录

**调用方式**：`sdd agent` → `MiniMaxClient`（`POST {base}/text/chatcompletion_v2`，
OpenAI 兼容端点 `{base}/chat/completions` 作为回退），温度 0.2，`max_tokens=8000`。
输入是 `context-pack.md`（约 13 KB）+ 固定 system prompt；要求只输出 unified diff。

**记录内容**（每次尝试一个目录，`records/minimax/coupon-policy/`）：

| 文件 | 内容 |
| --- | --- |
| `attempt-N-prompt.json` | 实际发送的 system + user 消息（含上下文包全文） |
| `attempt-N-response.json` | 模型、端点、finish_reason、请求 id、usage（prompt/completion tokens）、时延、推理字符数（不落推理原文） |
| `attempt-N-response.md` | 模型原始回答 |
| `attempt-N.patch` | 从回答中提取出的 unified diff |
| `agent-run.json` | 改前测试、每次尝试的测试结果、最终目标/回归测试结果、耗时 |

**与代码差异的对应**：最终 `git diff` 存为 `artifacts/code.diff`；`patches/recorded-coupon-policy.patch`
是同一补丁的独立副本，`scripts/replay.sh` 用它复现"改前失败 → 改后通过"。

> 现场运行方式见 README 第 3 节（`export MINIMAX_API_KEY=... && ./scripts/live-run.sh`）。
> `scripts/replay.sh` 不需要 Key，用记录下来的补丁完成同样的改前/改后验证。

## 4. 一条命令复核全部记录

```bash
cd minimax
cat records/events.jsonl | head -20                 # 导入 / 上下文 / 状态 / 验证 事件流
cat records/replay/*/replay.log                     # 完整回放日志（无 ANSI、逐条命令）
cat records/powercontext/verification-*-r3.json     # 改后 17 passed 的验证记录
python -m json.tool artifacts/context-pack-v1.json | head -40   # 上下文包与 SQL
git -C .runtime/workdir diff                        # 应用补丁后的真实差异
```
