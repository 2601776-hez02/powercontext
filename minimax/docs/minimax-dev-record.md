# MiniMax 开发记录

## 1. 会话信息

| 项 | 值 |
| --- | --- |
| 接入方式 | **MiniMax 网页版 / App 手动会话**（参赛账号只有积分，API 计费端点返回 402/1008，见 `docs/records.md` §3） |
| 模型 | `MiniMax-M3.1-Flash` |
| 会话时间 | 2026-11-09，会话耗时 6 分 21 秒 |
| 发送内容 | `records/minimax/coupon-policy/prompt-web.md`（13,793 B ≈ 3.4k tokens，与 API 版逐字相同） |
| 原始回答 | `records/minimax/coupon-policy/web-answer.md`（3,177 B） |
| 采纳的补丁 | `records/minimax/coupon-policy/web.patch` = `patches/minimax-web-coupon-policy.patch` = `patches/recorded-coupon-policy.patch`（1,878 B） |
| 任务 | `coupon-policy`：PRC-3 / PRC-4 / RSV-3 |

## 2. 提供给模型的上下文

与 `artifacts/context-pack-v1.json` 同源，逐条可回查：

- **规格（4 条）**：`docs/specs/reservation.md#RSV-3`、`docs/specs/pricing.md#PRC-3`、
  `docs/specs/pricing.md#PRC-4`、`docs/specs/reservation.md#RSV-2`
- **代码（5 个符号）**：`src/evently/pricing.py#apply_coupon`、`#find_coupon`、
  `src/evently/reservation.py#ReservationService`、`#ReservationService.reserve`、
  `src/evently/errors.py#ExpiredCouponError`
- **测试（4 个用例）**：`test_codes_are_matched_case_insensitively`、
  `test_lowercase_coupon_applies_to_reservation`、`test_expired_coupon_does_not_change_the_total`、
  `test_surrounding_whitespace_is_ignored`
- **任务状态**（PowerContext 读回）：done 2 项、todo 2 项、next_step
- **约束**：只改列出的代码、不改测试、不改公开符号、只输出一个 ```diff 块

## 3. 模型返回与关键说明

模型明确说明：它**没有挂载真实仓库**（`/root/minimax/.runtime/repo` 在它的沙箱里不可见），
因此按提示中的代码片段重建了 `evently`，补写了过期/容量/目录不变性用例，先复现
"lowercase 被拒、过期码仍被接受"，再生成补丁；它在 4 种可能的仓库布局变体
（catalog 位置、相对/绝对 import、`expires_on` 字段、小写 catalog key）下验证了 `git apply -p1`。

两处适配说明（模型自述）：

1. `import datetime as dt` / `from .errors import ExpiredCouponError` 放在文件中段而非文件头，
   因为原 import 行的确切文本不可见；新增的是相对导入，两种风格都能用；
2. 有效期字段按 `valid_until` / `valid_until_date` / `expires_on` / `expires_at` 容错读取，
   UTC 当天（含边界）有效，隔天抛 `ExpiredCouponError`，与 `UnknownCouponError` 区分、不扣库存。

补丁只落在 `src/evently/pricing.py`：`reservation.py` 无需修改（`apply_coupon` 在
`self._sold[event_id] += quantity` 之前抛错，RSV-3 已由既有顺序保证），`errors.py` 无需修改
（`ExpiredCouponError` 已带 `code = "coupon_expired"`）。

## 4. 本地采纳与验证（真实仓库，不是模型沙箱）

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 补丁可应用 | `git apply --check`（在 `.runtime/workdir`） | OK |
| 改前目标测试 | `pytest tests/test_coupon_policy.py` | **1 passed, 6 failed（exit 1）** |
| 应用后目标测试 | 同上 | **7 passed（exit 0）** |
| 应用后回归测试 | `pytest tests/test_pricing.py tests/test_reservation.py` | **10 passed（exit 0）** |
| 最终代码差异 | `git diff` → `artifacts/code.diff` | 仅 `pricing.py`，+31 / −5 |
| 状态落库 | PowerContext `sdd-task-state` | revision 4 · `verified` |

原始记录：`records/minimax/coupon-policy/web-run.json`（会话、补丁、改前/改后测试的机器可读汇总）、
`tests-before-web.log`、`tests-after-web.log`、`tests-regression-web.log`。

## 5. 与提交差异的对应关系

- `web.patch`（sha256 `02da62400ae03f48…`）是模型回答里提取出的 unified diff；
- `artifacts/code.diff`（sha256 `5854efd8dc3d8528…`）是在真实工作副本上应用后 `git diff` 的输出，
  与 `web.patch` 内容等价（多一行 `index` 头，行号由 git 重新计算）；
- `patches/recorded-coupon-policy.patch` 与 `web.patch` 逐字节相同，`scripts/replay.sh`
  用它在无 Key 环境下复现同一"改前失败 → 改后通过"结果。

## 6. 为什么走网页版，以及 API 通道

账号仅有积分，MiniMax 的 API 计费端点返回 `402 / base_resp.status_code=1008 insufficient balance`
（鉴权本身通过，`GET /v1/models` 正常）。因此本次改动用网页版完成，这是 MiniMax 的真实参与，
不是本地模拟。额度到账后 API 通道可直接重跑，参数与记录格式完全一致：

```bash
export MINIMAX_API_KEY=...          # 有余额的 Key
export MINIMAX_MODEL=MiniMax-M3    # 可选
./scripts/live-run.sh              # 写入 records/minimax/<task>/attempt-*.json
```

`scripts/replay.sh` 与 `scripts/live-run.sh` 共用同一份上下文装配与同一套测试，
两者的区别只在"谁生成 diff"。
