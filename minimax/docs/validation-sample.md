# 验证样例

## 1. 要改什么

**任务**：让优惠码匹配不区分大小写、忽略首尾空白，并拒绝已过期的优惠码；被拒绝的优惠码
不得占用库存。定价、容量、数量校验等既有行为保持不变。

任务定义：`samples/tasks/task-01-coupon-policy.json`

| 字段 | 内容 |
| --- | --- |
| task_id | `coupon-policy` |
| 目标测试 | `tests/test_coupon_policy.py`（7 个用例，交付快照上 6 个失败、1 个通过） |
| 回归测试 | `tests/test_pricing.py`、`tests/test_reservation.py`（10 个用例，改前改后都必须通过） |
| 保持行为 | 单价/小计、VIP20 20%、未知码报错、数量与容量错误类型 |

## 2. 当前有效的规格（上下文必须包含）

| 来源 | 条目 | 要求 |
| --- | --- | --- |
| `docs/specs/pricing.md` | `PRC-3` | 大小写不敏感、去首尾空白；未知码 `UnknownCouponError`、总额不变 |
| `docs/specs/pricing.md` | `PRC-4` | `valid until` 为 UTC 当日或之后可用；过期必须 `ExpiredCouponError`，不得报成未知码 |
| `docs/specs/reservation.md` | `RSV-3` | 优惠码未知/过期导致失败时**不得消耗容量**；验证在更新座位计数之前完成 |

上下文还必须定位到这些代码与测试来源：

| 类型 | 来源 |
| --- | --- |
| 代码 | `src/evently/pricing.py#find_coupon`、`src/evently/pricing.py#apply_coupon`、`src/evently/reservation.py#ReservationService.reserve` |
| 测试 | `tests/test_coupon_policy.py`（至少含 `test_codes_are_matched_case_insensitively`、`test_expired_coupon_is_rejected`、`test_expired_coupon_does_not_consume_capacity`） |

`sdd context --check` 会用同一份清单校验，缺项退出码 2。

## 3. 初始数据（交付快照）

```
sample-project/
├── docs/specs/pricing.md         # 规格 v1（PRC-3 不含短横线归一化、PRC-4 不含 coupon_expired）
├── docs/specs/reservation.md
├── src/evently/pricing.py        # find_coupon 精确字典查找；apply_coupon 不校验有效期
├── src/evently/catalog.py        # SPRING10 已过期(2020-03-31)、VIP20 有效(2099-12-31)
├── src/evently/reservation.py    # reserve → apply_coupon → 再累加座位
└── tests/                        # 目标测试 7 个 + 回归测试 10 个
```

快照缺陷（即"改前失败"的原因，不是环境问题）：

- `find_coupon("vip20")` → `UnknownCouponError`（大小写敏感）；
- `find_coupon("  vip20 ")` → `UnknownCouponError`（未去空白）；
- `apply_coupon("SPRING10", …)` → 返回打折后的金额（未校验 `valid_until`）。

## 4. 操作步骤

```bash
cd minimax
./scripts/bootstrap.sh
./scripts/replay.sh                   # 无 Key 复现：导入 → 上下文 → 状态 → 改前/改后测试 → 规格更新
# 或真实调用 MiniMax：
export MINIMAX_API_KEY=... && ./scripts/live-run.sh
```

## 5. 预期结果

| 步骤 | 预期 |
| --- | --- |
| `sdd import --repo .runtime/repo` | 10 个文档 / 50 个条目：spec 10、code 20、test 20；spec revision 稳定 |
| `sdd context --check` | 退出码 0；`materials.specs` 含 PRC-3、PRC-4、RSV-3；`checks.ok = true` |
| `sdd verify --targets tests/test_coupon_policy.py`（改前） | **退出码 1**，6 个用例失败、1 个通过（未知码在旧代码上也会被拒，属于已成立的行为） |
| 应用补丁后跑三个测试文件 | **退出码 0**，17 passed |
| `sdd state show` | 状态 `verified`，`verifications` 里能看到改前(exit 1)与改后(exit 0)两条 |
| 重启 PowerContext 后 `sdd state show` | 内容不变（状态来自 PowerContext 持久化，不在内存） |
| 覆盖 `samples/specs-v2/pricing.md` 后 `sdd import` + `sdd context` | spec revision 变化；PRC-3 出现"campaign dashboard"、PRC-4 出现"coupon_expired" |

`replay.sh` 第 11 步会用断言逐条检查上表的后三行。

## 6. 重置

```bash
./scripts/stop-powercontext.sh
rm -rf .runtime          # 索引 + 工作副本 + PowerContext 数据
./scripts/bootstrap.sh   # 重新初始化
```

`sample-project/` 本身**永远不会被修改**：所有改动都发生在 `.runtime/workdir` 或
`.runtime/repo`（由 `scripts/replay.sh` 复制而来）。
