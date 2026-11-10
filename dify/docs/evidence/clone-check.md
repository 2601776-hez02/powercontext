# 从 GitHub 全新 clone 的检查（自动生成）

> 模拟评审：`git clone --depth 1 --branch 2026-competition-ecosystem <repo>`，
> 然后检查「README 及所有 markdown 里指向本地文件的链接」是否都能打开。

## 结果

```
克隆到 .../judge-clone-d4sw491t
HEAD: 44bdd1ce37b1c00502bf9276e55e83431cd8b9f6 | 与目标一致: True
dify 目录文件数: 91

=== README/markdown 里指向本地文件的链接 ===
全部存在 OK

=== 关键文件 ===
  ok   dify/README.md
  ok   dify/QUICKSTART.md
  ok   dify/SUBMISSION.md
  ok   dify/rules.json
  ok   dify/dsl/wangchuan-tavern.chatflow.yaml
  ok   dify/dsl/wangchuan-tavern.local.yaml
  ok   dify/docs/conformance.md
  ok   dify/docs/evidence/dify/playthrough.gif
  ok   dify/docs/evidence/dify/00-workflow-canvas.png
  ok   dify/scripts/12_reviewer_check.sh
  ok   dify/scripts/06_selfcheck.sh
  ok   dify/engine/game.py
  ok   dify/data/samples/walkthrough.json
```

## 这个检查现在也是自动的

`scripts/09_docs_lint.sh` 原先只检查 `README.md` 的链接，现在**扫描所有 markdown**：
任何 markdown 链接（中括号 + 圆括号）指向不存在的文件，都会报出 `文件:行`。

（故意在 `docs/limitations.md` 里插入一个坏链接验证过：检查能抓到；删掉后恢复通过。）
