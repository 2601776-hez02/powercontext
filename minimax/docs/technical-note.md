# 一页技术说明

## 1. 模块结构

```
task.json ──┐
            ▼
      ┌───────────────┐   扫描 repo（glob）
      │  chunking     │   ├─ Markdown 规格：## ID 段落 → 条目（PRC-3 …）
      │  (解析/切分)   │   ├─ Python 源码：AST → module/class/function/method
      └──────┬────────┘   └─ 测试文件：test_* 函数（带 import 表）
             │ Item(kind, item_ref, symbol, start/end line, revision, text, searchable)
             ▼
      ┌───────────────┐   sdd_documents / sdd_items
      │ seekdb_store  │   FULLTEXT (searchable) WITH PARSER SPACE
      │ (嵌入式 seekdb)│   sdd_imports / sdd_document_history（revision 变更日志）
      └──────┬────────┘
             │ 三路召回
             ▼
      ┌───────────────┐   ① MATCH…AGAINST 词法分
      │  retrieval    │   ② 精确 symbol/path 命中
      │ (可解释重排)   │   ③ 测试引用关联（被选代码的符号名出现在测试体里）
      └──────┬────────┘
             │ 规格 ≤4 / 代码 ≤5 / 测试 ≤4，每条带 score + reasons
             ▼
      ┌───────────────┐   ┌──────────────────┐
      │ context_pack  │◄──┤ PowerContext     │ 任务状态：done / todo / 验证结果
      │               │   │ (HTTP, Memory)   │ 上下文包另存为 Source（幂等 source_id）
      └──────┬────────┘   └──────────────────┘
             │ JSON + Markdown（每条材料带 path、行号、revision）
             ▼
      ┌───────────────┐   system prompt + 上下文包 → diff-only 回答
      │  agent        │   git apply → pytest 目标 → pytest 回归（失败回灌，≤2 次）
      └──────┬────────┘
             │ 补丁 + 测试日志 + 请求/响应元数据
             ▼
      records/ artifacts/
```

## 2. 上下文选择方式

**索引**：规格按 `## <ID>` 段落切条，代码按 AST 切到符号，测试按 `test_*` 切到用例；
每条都记 `path`、`start_line`/`end_line`、`content sha256` 与去重后的 `searchable`
（标识符 + docstring/正文 token，中文按 bigram）。测试条目额外保留 import 表。

**召回三路**（全部落在 seekdb SQL 上）：

1. `MATCH(searchable) AGAINST (… IN NATURAL LANGUAGE MODE)`，取回 120 条候选；
2. 任务关键词的精确命中：`LOWER(symbol|path|title) LIKE`，保证 `apply_coupon`、
   `pricing.py` 这类标识符不会被分词细节漏掉；
3. 测试关联：把已选代码条目的模块名与符号名回查 `kind='test'` 的正文，
   命中者标记 `linked`（"这个测试会调用到要改的函数"）。

**重排**（可解释、无魔法）：

```
score = 0.35·归一化FTS + 0.35·精确命中 + 0.40·测试关联
      + 0.20·关键词在标识符中的覆盖率 + 0.20·关键词正文覆盖率
      + 0.10·全词正文覆盖率
      × kind 先验(规格1.0/代码0.97/测试0.93) × 整文件条目降权(0.45)
```

每条材料都带 `reasons`（如 `exact symbol/path match; keyword coverage 0.83`），
`context-pack.json` 的 `retrieval.queries` 还保留实际 SQL，便于复核"为什么选它"。

**不被硬编码**：`samples/tasks/*.json` 里只有任务文本、关键词和 `must_include` 清单；
清单只用于 `--check` 校验，不参与选取。改规格 → 重新 `import` → revision 变化 →
下一次 `context` 直接引用新正文（`replay.sh` 第 10–11 步断言了这一点）。

**失效判定**：`context` 会重新读盘比对 `revision`，把条目标成 `ok` / `stale`
（导入后文件又改过）/ `missing`。这回答"先前验证是否仍适用于当前代码"。

## 3. 任务状态（PowerContext）

- 写：`POST /v1/memory/remember`，`kind="sdd-task-state"`，正文是紧凑 JSON
  （status / done / todo / verifications / next_step / revision），单条 ≤8192 字节；
- 读：`POST /v1/memory/search`（`query="SDD task state <task_id>"`），按 `task_id` 与
  `revision` 选出最新的一条；
- 上下文包另存为 Source：`POST /v1/sources/content`，`source_id="sdd-context-<task>-<import_id>"`，
  同一 import 幂等，规格变更后 import 变化自然产生新 Source；
- 重启验证：`sdd state show` 每次都是新进程；`replay.sh` 还额外重启 PowerContext 进程再读。

## 4. MiniMax 的分工

- **只做代码修改**：输入是上下文包 Markdown + 约束（只改列出的文件、不改测试、保留公开符号、
  只输出 unified diff）；输出被 `git apply --check` 校验后才落到工作副本；
- **闭环验证**：目标测试不过就把 pytest 失败摘要回灌，最多 2 次；目标测试通过后再跑回归；
- **记录**：prompt、响应元数据（模型/端点/usage/时延/请求 id/推理字符数）、补丁、测试日志
  全部落盘；**不落模型推理原文**，只有 `reasoning_chars` 计数。
- **两条通道，一份上下文**：`sdd prompt` 导出的文本与 API 请求逐字相同，网页版回答由
  `sdd adopt` 收编（`records/minimax/<task>/web-*`），与 API 通道共用 apply / 测试 / 记录流程。
  本次提交的差异来自 `MiniMax-M3.1-Flash` 的网页版会话（账号只有积分、API 余额为 0）。

## 5. 成本与边界

- 单次上下文包约 12–20 KB（≈4–6 K tokens），一次任务 1 次调用、最多 2 次；
- 导入是幂等的整库替换（样例 50 条目 < 1 s），真实工程可换成按 doc 增量 upsert；
- 嵌入式 seekdb 单进程独占；PowerContext 用 SQLite 后端，两者数据目录互不干扰。
