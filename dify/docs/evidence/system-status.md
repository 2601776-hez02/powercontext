# 当前系统状态快照（自动生成）

> 由 `deploy/` 同款调用实时抓取，用于说明「此刻这套东西是活的」。

| 组件 | 状态 |
| --- | --- |
| 酒馆引擎 | `ok=True`，uptime 12 分钟 |
| seekdb | `5.7.25-OceanBase seekdb-v1.4.0.0`，表行数 {'tw_memory': 1722, 'tw_event': 1093, 'tw_player': 109, 'tw_npc': 3, 'tw_item': 11, 'tw_legend': 56} |
| PowerContext | `{'enabled': True, 'available': True, 'url': 'http://127.0.0.1:8010', 'status': 200}` |
| Dify 控制台 | `{'step': 'finished', 'setup_at': '2026-11-09T16:58:38'}` |

## 三套断言（在本机跑出来的）

* 声明一致性：> `python3 -m engine.selfcheck` 生成；共 35 条，**35 PASS / 0 FAIL**。
* 验证样例：> `python3 -m engine.spec_check` 生成；guest=`无名客`；共 10 回合，**全部 PASS**。
* 不变式审计：1082+ 项（含全分支扫测、奇怪输入、压力场景、记忆来源、作弊）

## 公开地址

* 应用（浏览器直接开）：<https://parents-pendant-stamps-pools.trycloudflare.com/chat/US2WZk6wfvkw7R4Q>
* 引擎（给 Dify 调）：<https://presence-hierarchy-necessarily-females.trycloudflare.com/health>

> 这两个地址由作者的 ECS + Cloudflare quick tunnel 托管，可能下线；
> 完全复现请用 README 第 5 节的本地部署（不依赖作者侧任何服务）。

