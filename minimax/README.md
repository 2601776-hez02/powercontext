# 规有所依 —— SDD 上下文管理系统（minimax/）

本目录是「MiniMax × OceanBase」赛题《规有所依——面向软件持续演进的 SDD 上下文管理系统》的参赛作品。
它实现一个**工程上下文模块**：给定一项开发任务，从 seekdb 中的工程资料索引里选出当前有效的
规格条目、需要修改的代码符号和用于验证的测试，连同 PowerContext 里保存的任务进展一起交给
MiniMax 开发助手，由它完成代码修改并自动跑通验证。

- 业务工程：`sample-project/`（一个可运行、带 pytest 的票务服务）
- 验证样例：`samples/tasks/task-01-coupon-policy.json`（任务、必要来源、回归范围）
- 提交的代码差异：`artifacts/code.diff`（MiniMax 产出）+ `patches/`

---

## 1. 环境要求

| 项 | 要求 | 说明 |
| --- | --- | --- |
| 操作系统 | Linux x86_64 / aarch64，macOS | 嵌入式 seekdb 依赖 `pylibseekdb`，**不支持 Windows** |
| Python | 3.11 或 3.12 | `pylibseekdb` 只提供到 `cp312-abi3` 的轮子 |
| 磁盘 | ≥ 1 GB 空闲 | venv + seekdb 数据目录 + PowerContext 数据 |

依赖版本（见 `requirements.txt`）：

```
powercontext[cli,server,seekdb]==1.2.0   # 任务状态持久化 + 服务端
PyMySQL>=1.1.1,<2                        # 嵌入式 seekdb 的 SQL 通道
requests>=2.31,<3                        # PowerContext / MiniMax 的 HTTP 调用
pytest>=8.0,<9                           # 验证样例的测试运行器
```

国内网络建议加镜像：

```bash
SDD_PYPI_INDEX=https://mirrors.aliyun.com/pypi/simple ./scripts/bootstrap.sh
```

## 2. 一键复现（不需要 MiniMax Key）

```bash
cd minimax
./scripts/bootstrap.sh     # 建 venv、装依赖、初始化 seekdb 表结构与全文索引
./scripts/replay.sh        # 完整验证样例：导入 → 上下文 → 状态 → 改前/改后测试 → 规格更新
```

`replay.sh` 会：

1. 从**空状态**开始（删除 `.runtime/seekdb` 与 PowerContext 数据目录）；
2. 启动 PowerContext，把 `sample-project` 的规格/代码/测试导入 seekdb；
3. 生成本次任务的上下文包（`artifacts/context-pack-v1.json|md`），并校验必要来源齐全；
4. 写入任务状态到 PowerContext，**新进程**读回，再**重启 PowerContext** 后再次读回；
5. 跑目标测试（改动前，退出码 1：行为缺失）→ 应用 `patches/recorded-coupon-policy.patch`
   → 再跑目标测试 + 回归测试（退出码 0）；
6. 把 `samples/specs-v2/pricing.md` 覆盖到工作副本、重新导入，证明上下文改用**更新后的规格**；
7. 全部日志落在 `records/replay/<时间戳>/`。

期望结尾输出：

```
spec revision : 1e10174f8aaa5bc1 -> 18471ec477520271
changed items : docs/specs/pricing.md#PRC-3, docs/specs/pricing.md#PRC-4
context follows the re-imported specification: OK
replay finished.
```

## 3. 现场用 MiniMax 跑一次（真实参与）

```bash
cd minimax
export MINIMAX_API_KEY=...            # 必需
export MINIMAX_MODEL=MiniMax-M3       # 可选，默认 MiniMax-M3（/v1/models 里可选别的）
export MINIMAX_GROUP_ID=...           # 可选，团队/GroupId；报 1004 时填
export MINIMAX_BASE_URL=https://api.minimaxi.com/v1   # 可选
./scripts/live-run.sh
```

`live-run.sh` 与 `replay.sh` 共用同一套导入与上下文装配，区别是第 3 步改为：

1. 在未修改的快照上运行目标测试（必须失败，且是**测试失败**而不是环境错误）；
2. 把上下文包发给 MiniMax，要求只输出 unified diff；
3. `git apply` 到 `.runtime/workdir`，跑目标测试；失败则把失败输出回灌，最多 2 次；
4. 目标测试通过后跑回归测试；
5. 记录 prompt、响应元数据（模型、token 用量、时延、请求 id）、补丁和测试日志；
6. 把验证结果写回 PowerContext。

产物：`records/live/<时间戳>/`、`records/minimax/<task>/`、`artifacts/code.diff`。

## 4. 模块调用入口

CLI（`python -m sdd_context`）：

| 命令 | 作用 | 关键输出 |
| --- | --- | --- |
| `sdd init` | 建 seekdb 表与全文索引 | 表名、索引名、seekdb 版本 |
| `sdd import --repo DIR` | 导入规格/代码/测试 | 文档数、条目数、三类 revision、变更日志 |
| `sdd context --task TASK.json` | 为任务生成上下文包 | `context-pack.json` + `context-pack.md` |
| `sdd state show/save --task` | 读写 PowerContext 任务状态 | 状态、done/todo、验证记录 |
| `sdd verify --task` | 跑测试并把结果写入 PowerContext | 退出码、通过数、日志路径 |
| `sdd agent --task` | 调 MiniMax 生成补丁并验证 | 尝试次数、补丁、目标/回归测试结果 |
| `sdd db stats` / `sdd db query --sql "..."` | 直接查 seekdb，用于举证 | 行数/结果集 |
| `sdd doctor` | 检查 seekdb / PowerContext / MiniMax / 样例 | 三项技术的就绪状态 |

上下文包（`context-pack.json`，schema `sdd-context-pack/v1`）的字段：

```json
{
  "task": {"task_id": "...", "title": "...", "description": "...", "keep_behavior": ["..."]},
  "database": {"target": "embedded-seekdb:/.../seekdb", "server_version": "5.7.25-OceanBase seekdb-v1.4.0.0"},
  "index": {"import_id": "...", "spec_revision": "...", "code_revision": "...", "test_revision": "..."},
  "materials": {
    "specs": [{"item_ref": "docs/specs/pricing.md#PRC-3", "revision": "...", "start_line": 28, "end_line": 37, "text": "...", "score": 0.5, "reasons": ["..."]}],
    "code":  [{"item_ref": "src/evently/pricing.py#apply_coupon", "start_line": 24, "end_line": 28, "text": "..."}],
    "tests": [{"item_ref": "tests/test_coupon_policy.py#test_expired_coupon_is_rejected"}]
  },
  "task_state": {"status": "in_progress", "done": [...], "todo": [...], "verifications": [...]},
  "checks": {"ok": true, "missing": {}, "source_status": {"ok": 13}},
  "retrieval": {"terms": [...], "candidate_count": 46, "queries": [{"purpose": "fulltext:...", "sql": "..."}]}
}
```

`sdd context --check` 会对比 `samples/tasks/*.json` 里 `must_include` 声明的必要来源，
缺任何一条就返回退出码 2；`retrieval.queries` 保留了本次实际执行的 SQL。

## 4.1 交付材料索引

| 题面要求的材料 | 本仓库中的位置 |
| --- | --- |
| 完整源码 | `sdd_context/`（上下文模块）、`scripts/`（启动与复现） |
| 运行说明 | 本文件（第 1–5 节） |
| 验证样例 | `samples/tasks/task-01-coupon-policy.json`、`docs/validation-sample.md`、`samples/specs-v2/` |
| 代码与测试 | `sample-project/`、`patches/`、`artifacts/code.diff`、`records/runs/*/tests.log` |
| 技术使用记录 | `docs/records.md` + `records/`（seekdb 查询、PowerContext 写入/复用、测试日志） |
| 一页技术说明 | `docs/technical-note.md` |

## 5. 数据初始化与重置

```bash
./scripts/bootstrap.sh                  # 初始化（可重复执行）
rm -rf .runtime                         # 删除索引、工作副本、PowerContext 数据
./scripts/stop-powercontext.sh          # 只停服务，保留数据
```

`replay.sh` 与 `live-run.sh` 开头都会做一次完整重置，因此每次运行都是**从空状态开始**。

## 6. 三项技术如何参与

| 技术 | 承担的作用 | 证据 |
| --- | --- | --- |
| **seekdb**（嵌入式 `pylibseekdb` 1.4.0） | 存全部工程资料（`sdd_documents` / `sdd_items`）并建 FULLTEXT 索引；上下文包的每条材料都来自 `MATCH ... AGAINST` 或精确 symbol/path 查询 | `sdd db stats`、`records/replay/*/replay.log` 中的 `retrieval.queries`、`sdd db query` |
| **PowerContext**（1.2.0，HTTP API） | 保存任务状态（done/todo/验证结果）与每次上下文包对应的 Source；重启后读回并参与上下文 | `records/powercontext/state-*.json`、`sdd state show`、PowerContext 自带 SQLite 数据目录 |
| **MiniMax**（`MiniMax-M3`） | 根据上下文包生成 unified diff，完成业务代码修改 | `records/minimax/<task>/attempt-*-prompt.json` / `response.json` / `*.patch`、`artifacts/code.diff` |

## 7. 目录结构

```
minimax/
├── README.md                     # 本文件：运行说明
├── requirements.txt
├── sdd_context/                  # 上下文模块（CLI + 库）
│   ├── chunking.py               # 规格/代码/测试 → 可寻址条目
│   ├── seekdb_store.py           # 嵌入式 seekdb：schema、导入、FTS 检索、统计
│   ├── importer.py               # 导入 + revision 变更日志
│   ├── retrieval.py              # 三路召回 + 可解释重排
│   ├── context_pack.py           # 上下文包（JSON/Markdown）+ 来源新鲜度校验
│   ├── state.py                  # PowerContext 任务状态（Memory + Source）
│   ├── agent.py                  # MiniMax 调用、补丁应用、测试回灌
│   └── testing.py                # 工作副本、pytest 运行与日志
├── sample-project/               # 业务工程（规格 docs/specs、代码 src/evently、测试 tests/）
├── samples/
│   ├── tasks/task-01-coupon-policy.json     # 验证样例：任务与必要来源
│   └── specs-v2/pricing.md                  # 规格更新样例（re-import 演示用）
├── patches/                      # 参考补丁 + MiniMax 记录的补丁
├── scripts/                      # bootstrap / replay / live-run / start|stop-powercontext
├── docs/                         # 一页技术说明、验证样例、技术使用记录
├── records/                      # 运行记录（含判定证据）
└── artifacts/                    # 上下文包、代码差异
```

## 8. 已知限制

- **Windows 不支持**：`pylibseekdb` 没有 Windows 轮子，`CodeService` 等索引逻辑也依赖 POSIX
  文件锁；Windows 上只能用 `SDD_SEEKDB_DSN` 指向一台已运行的 seekdb/MySQL 端点。
- **嵌入式 seekdb 单进程独占**：同一数据目录只允许一个进程打开；CLI 每条命令单独开关，
  不会并发。PowerContext 用默认 SQLite 后端，不与 seekdb 抢目录。
- **索引用 SPACE 分词器**：中文按二元组（bigram）入索引，英文按标识符切词；语义检索（embedding）
  不在本模块范围内，召回是词法 + 标识符 + 测试引用关联三路。
- **MiniMax 需要 Key**：没有 Key 时 `sdd agent` 会明确报错；`scripts/replay.sh` 用记录下来的
  补丁复现代码修改与测试结果。
- **PowerContext 的 Memory 单条上限 8192 字节**：任务状态超出时自动裁剪（保留最近 4 条验证结果），
  上下文包全文另存为 Source。
