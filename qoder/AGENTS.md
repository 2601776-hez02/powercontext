# 知序 ZhiXu —— Qoder 项目约定

本目录是 2026 OceanBase 数据库大赛 Qoder 赛题的参赛作品。以下约定同时被 Qoder IDE 与 Qoder CLI 读取
（Qoder 规则与 AGENTS.md 兼容；两者冲突时以 `.qoder/rules/` 为准）。

## 目标

做一个「学习 / 科研 / 工作提效」的本地工作台：资料能查回出处、任务能追上进度、周报能自动汇总。
核心存储必须使用 **seekdb**（`pylibseekdb` 嵌入式引擎），不允许用自制 SQLite / JSON / 内存实现替代。

## 目录

- `src/zhixu/`：核心包（store / corpus / text / embedding / search / tasks / digest / web / export_site / cli）
- `data/corpus/`：中文 Markdown 样例语料（16 篇，带 front matter）
- `data/seed/`：术语表与任务种子
- `site/`：项目网站（纯静态，Qoder Sites 发布内容）
- `tests/`：pytest，覆盖分块坐标、检索出处、预算边界、幂等、重启恢复、任务事件、周报、API、站点导出

## 开发命令

```bash
pip install -r requirements.txt
python run.py init
python run.py ask "证据召回率怎么评测"
python run.py serve
python run.py doctor
python -m pytest -q
python run.py export-site
```

## 代码约定

- Python 3.11+，只依赖标准库 + `pylibseekdb` + `PyMySQL`；不引入 Web 框架或 ORM。
- 中文文案直接写 UTF-8 字符，不要用 `\uXXXX` 转义。
- 片段坐标是不可变契约：`原文[char_start:char_end] == 片段正文`，任何改动都要有测试保护。
- 数据库操作集中在 `store.py`；业务模块只写 SQL，不直接持有连接。
- 建表必须 `IF NOT EXISTS`；导入必须按内容哈希幂等；写操作必须有事务。
- 检索的向量排序必须带 `APPROXIMATE`，否则优化器会退化为全表扫描。
- 新增功能先写测试（pytest），再写实现；测试不得依赖网络或外部模型。
- 站点数据只能由 `export-site` 从真实数据库导出，禁止手写演示结果。

## 交付

- 站点：`site/index.html` + `site/assets/` + `site/data/demo.json`，通过 Qoder Sites 发布。
- 提交：分支 `2026-competition-ecosystem`，目录 `qoder/`，commit 需包含源码、样例数据、测试与站点。
- 文档：`README.md` 说明痛点、架构、运行方式、验证结果与 seekdb 集成点。
