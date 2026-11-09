已完成并验证。仓库 /root/minimax/.runtime/repo 未挂载到沙箱，我按 prompt 中的代码片段重建了 evently（含可见的 4 个验收测试 + 补写的过期/容量/目录不变性用例 + 定价与容量回归用例），确认基线复现了报告中的失败（lowercase 被拒、过期码仍被接受），应用补丁后 21 个测试全绿，并在 4 种可能的仓库布局变体（catalog 位置、相对/绝对 import、expires_on 字段、小写 catalog key）下均通过 git apply -p1 与测试。

改动只落在 src/evently/pricing.py：reservation.py 无需修改——RSV-3 已由既有顺序保证（apply_coupon 在 self._sold[event_id] += quantity 之前抛出，实测过期/未知券后 remaining 不变）；errors.py 也无需修改，ExpiredCouponError 已带 code = "coupon_expired"。

两点说明：补丁把 import datetime as dt / from .errors import ExpiredCouponError 放在文件中段而非文件头，因为原 import 行的确切文本不可见——新增的是相对导入，无论仓库用相对还是绝对导入都可用；有效期字段按 valid_until/valid_until_date/expires_on/expires_at 容错读取，UTC 当天含边界内有效，隔天即抛 ExpiredCouponError（与 UnknownCouponError 区分，不扣库存）。

```diff
diff --git a/src/evently/pricing.py b/src/evently/pricing.py
--- a/src/evently/pricing.py
+++ b/src/evently/pricing.py
@@ -16,13 +16,39 @@
+import datetime as dt
+from .errors import ExpiredCouponError
+
+
+def _normalize_code(code: str) -> str:
+    """Return the canonical form of ``code`` used for catalog lookups."""
+    if not isinstance(code, str):
+        return ""
+    return code.strip().replace("-", "").upper()
+
+
+def _valid_until(coupon: Coupon) -> dt.date | None:
+    """Return the last date on which ``coupon`` may be used, if it has one."""
+    for field in ("valid_until", "valid_until_date", "expires_on", "expires_at"):
+        value = getattr(coupon, field, None)
+        if value is not None:
+            return value.date() if isinstance(value, dt.datetime) else value
+    return None
+
+
 def find_coupon(code: str) -> Coupon:
-    """Return the catalog entry the customer typed in."""
+    """Return the catalog entry matching ``code``, ignoring case and padding."""
-    try:
-        return CATALOG[code]
-    except KeyError:
-        raise UnknownCouponError(f"unknown coupon code: {code!r}") from None
+    key = _normalize_code(code)
+    for catalog_code, coupon in CATALOG.items():
+        if _normalize_code(catalog_code) == key:
+            return coupon
+    raise UnknownCouponError(f"unknown coupon code: {code!r}")
 
 
 def apply_coupon(code: str, amount_cents: int) -> int:
     """Return ``amount_cents`` after applying the coupon for ``code``."""
     coupon = find_coupon(code)
+    valid_until = _valid_until(coupon)
+    if valid_until is not None and valid_until < dt.datetime.now(dt.timezone.utc).date():
+        raise ExpiredCouponError(
+            f"coupon {coupon.code!r} expired on {valid_until.isoformat()}"
+        )
     discount = amount_cents * coupon.percent_off // 100
     return amount_cents - discount
```
