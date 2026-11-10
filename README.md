# 2026 OceanBase 数据库大赛 · 生态赛题

本分支用于提交「生态赛题」的参赛作品，按赛题分目录存放：

| 目录 | 赛题 | 说明 |
| --- | --- | --- |
| [`dify/`](dify/README.md) | **Just for Fun：用 Dify 做一个好玩的智能应用** | 本次提交的作品 |

---

## 🏮 忘川酒馆 —— 一个会记住你的文字酒馆

一句话一回合。三个 NPC（老板娘阿箬、说书人老陶、打杂的小满）会把你干过的每一件事
写进 seekdb 里的记忆表（ngram 全文 + HNSW 向量），下一次开口，他们记得你。

* **想直接玩（零配置）**：<https://parents-pendant-stamps-pools.trycloudflare.com/chat/US2WZk6wfvkw7R4Q>
* **想本地跑**：`cd dify && bash scripts/00_bringup.sh` → 导入 `dify/dsl/wangchuan-tavern.chatflow.yaml` → 配一个对话模型
* **想核对「声明 vs 实际」**：
  * 声明逐条：`dify/docs/conformance.md`（49 条 PASS/FAIL，含实测值）
  * 样例逐条：`dify/docs/evidence/walkthrough-check.md`（11 步逐条断言）
  * seekdb 在干活：`dify/docs/evidence/seekdb-explain.json`、`engine-transcript.json`、`docs/evidence/dify/` 截图

运行说明、技术分工、已知限制、提交材料对照表，全部在 **[`dify/README.md`](dify/README.md)**。

> 技术栈：Dify 1.19.0（Chatflow 11 节点）× seekdb 1.4.0（业务库 + 全文/向量检索）×
> PowerContext 1.5.0（跨会话长期记忆，可关）× 一个 FastAPI 小引擎（判定与落库）。
