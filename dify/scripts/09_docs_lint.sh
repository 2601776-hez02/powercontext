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
