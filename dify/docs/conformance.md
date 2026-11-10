# 声明一致性自检（自动生成）

> `python3 -m engine.selfcheck` 生成；共 18 条，**18 PASS / 0 FAIL**。
> 每条都写明「声明」「实测」，只用到引擎 HTTP 接口与 seekdb，不需要 Dify 与模型。

| # | 声明 | 结果 | 实测 |
| --- | --- | --- | --- |
| 1 | seekdb 与引擎可用 | **PASS** | `seekdb=5.7.25-OceanBase seekdb-v1.4.0.0` |
| 2 | 初始化后应有 3 NPC / 11 物品 / 不少于 12 条前置记忆 | **PASS** | `npc=3 item=11 memory=107 (≥12)` |
| 3 | NPC 记忆有全文索引（ngram 分词） | **PASS** | `tw_memory.FULLTEXT` |
| 4 | NPC 记忆有 HNSW 向量索引 | **PASS** | `tw_memory.VECTOR` |
| 5 | 向量排序走向量索引扫描（不是全表扫） | **PASS** | `VECTOR INDEX ADAPTIVE SCAN (PRE-FILTER)` |
| 6 | 第一次请他喝酒 → 他说一句只在杯边说的事 | **PASS** | `客人要是问起柜台后头那盏灯——灯底下压着东西。第三块砖别踩，踩了要赔。` |
| 7 | 同一个人不会说第二遍 | **PASS** | `''` |
| 8 | 铜板不够时被拒绝，账本不出现负数 | **PASS** | `reason=no_coins coins=0` |
| 9 | 第 3 次记账当场逐出（结局「逐客」） | **PASS** | `verdict=banned wanted=3 ending=逐客` |
| 10 | 被逐出后再说话只会被挡在门外 | **PASS** | `turned_away` |
| 11 | 好感过门槛 → NPC 说出守了多年的秘密 | **PASS** | `kinds=['亲历', '亲历', '亲历', '秘密', '亲历', '旧事', '旧事', '旧事', '旧事']` |
| 12 | 读心返回 NPC 脑子里的真实记忆行 | **PASS** | `6 条` |
| 13 | 读心也算一个回合（回合号推进） | **PASS** | `turn=3` |
| 14 | 回答末尾的判定行由引擎生成（含判定与状态） | **PASS** | `【第 3 回合 · 判定：只是看着】你盯着阿箬的眼睛，看见了一些不该看见的。 > 铜板 17｜风评 2｜被记账 0｜大堂 ` |
| 15 | 记忆里有「亲历 / 目击 / 传闻 / 杯边话」多种来源 | **PASS** | `kinds=['亲历', '旧事', '杯边话']` |
| 16 | 骰子与玩家名字无关（换名字复现同一串判定） | **PASS** | `obse succ obse fail succ succ succ obse succ clos` |
| 17 | 10 回合验证样例的判定与文档一致 | **PASS** | `obse succ obse fail succ succ succ obse succ clos` |
| 18 | 打烊时把这一局写成「传说」存进 PowerContext | **PASS** | `{"available": true, "written": true, "status": 200, "artifact_id": "art_b7020aa3` |
