#!/usr/bin/env bash
# 文档自检：markdown 表格列数一致 + README 里的相对链接都能打开。
#
# 为什么需要：README 是评审最先读的材料，一次替换把表格拼坏过两次
# （多一列 / 少一个竖线），肉眼看不出，渲染出来是乱的。
#
# 用法：bash scripts/09_docs_lint.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
[ -x ".venv/bin/python" ] && PY=".venv/bin/python"

"$PY" - <<'PY'
import pathlib
import re
import sys

root = pathlib.Path(".")
problems: list[str] = []

for path in sorted(root.rglob("*.md")):
    if any(part in {".venv", ".cache", "node_modules"} for part in path.parts):
        continue
    lines = path.read_text(encoding="utf-8").splitlines()
    block: list[tuple[int, str]] = []
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            block.append((number, stripped))
            continue
        if block:
            counts = {row.count("|") for _, row in block}
            if len(counts) > 1:
                problems.append(
                    f"{path}:{block[0][0]} 表格列数不一致：{[c for c in sorted(counts)]}"
                )
            block = []
    if block:
        counts = {row.count("|") for _, row in block}
        if len(counts) > 1:
            problems.append(f"{path}:{block[0][0]} 表格列数不一致：{[c for c in sorted(counts)]}")

# README 里的相对链接
readme = pathlib.Path("README.md")
for match in re.finditer(r"\]\(([^)#][^)]*)\)", readme.read_text(encoding="utf-8")):
    target = match.group(1).split("#")[0].strip()
    if not target or target.startswith(("http://", "https://", "mailto:")):
        continue
    if not (readme.parent / target).exists():
        problems.append(f"README.md 链接指向不存在的文件：{target}")

if problems:
    print("文档自检失败：")
    for item in problems:
        print("  -", item)
    sys.exit(1)

print("文档自检通过：表格列数一致，README 相对链接全部存在")
PY

# ---- 条数一致性：文档里写的「NN 条」必须和 conformance.md 的实际条数一致 ----
PY_CHECK="${PYTHON:-python3}"
if [ -x ".venv/bin/python" ]; then PY_CHECK=".venv/bin/python"; fi
"$PY_CHECK" - <<'PY' || exit 1
import pathlib, re, sys

conf = pathlib.Path("docs/conformance.md").read_text(encoding="utf-8")
m = re.search(r"共 (\d+) 条", conf)
if not m:
    print("!! conformance.md 里找不到条数")
    sys.exit(1)
actual = int(m.group(1))

files = ["README.md", "QUICKSTART.md", "SUBMISSION.md", "scripts/README.md",
         "docs/sample-walkthrough.md", "docs/evidence/release-check.md", "docs/evidence/fallbacks.md"]
bad = []
for name in files:
    path = pathlib.Path(name)
    if not path.exists():
        continue
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        for found in re.finditer(r"(\d+) 条(?:声明|自检|PASS)", line):
            if int(found.group(1)) != actual:
                bad.append(f"{name}:{line_no} 写了 {found.group(1)} 条，实际 {actual}")

if bad:
    print("!! 条数不一致：")
    for item in bad:
        print("   ", item)
    sys.exit(1)
print(f"条数一致性通过：文档里的「{actual} 条」都对得上")
PY
