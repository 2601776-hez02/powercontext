# 声明一致性自检（自动生成）

> `python3 -m engine.selfcheck` 生成；共 24 条，**24 PASS / 0 FAIL**。
> 每条都写明「声明」「实测」，只用到引擎 HTTP 接口与 seekdb，不需要 Dify 与模型。

| # | 声明 | 结果 | 实测 |
| --- | --- | --- | --- |
| 1 | seekdb 与引擎可用 | **PASS** | `seekdb=5.7.25-OceanBase seekdb-v1.4.0.0` |
| 2 | 初始化后应有 3 NPC / 11 物品 / 不少于 12 条前置记忆 | **PASS** | `npc=3 item=11 memory=521 (≥12)` |
| 3 | NPC 记忆有全文索引（ngram 分词） | **PASS** | `tw_memory.FULLTEXT` |
| 4 | NPC 记忆有 HNSW 向量索引 | **PASS** | `tw_memory.VECTOR` |
| 5 | 向量排序走向量索引扫描（不是全表扫） | **PASS** | `VECTOR INDEX ADAPTIVE SCAN (PRE-FILTER)` |
| 6 | 第一次请他喝酒 → 他说一句只在杯边说的事 | **PASS** | `客人要是问起柜台后头那盏灯——灯底下压着东西。第三块砖别踩，踩了要赔。` |
| 7 | 同一个人不会说第二遍 | **PASS** | `''` |
| 8 | 铜板不够时被拒绝，账本不出现负数 | **PASS** | `reason=no_coins coins=0` |
| 9 | 第 3 次记账当场逐出（结局「逐客」） | **PASS** | `verdict=banned wanted=3 ending=逐客` |
| 10 | 被逐出后再说话只会被挡在门外 | **PASS** | `turned_away` |
| 11 | 被逐出后同一会话进不来、换会话等于第二天还能进 | **PASS** | `banned=banned same=turned_away new=observed` |
| 12 | 好感过门槛 → NPC 说出守了多年的秘密（事实行里也写出来） | **PASS** | `kinds_has_secret=True facts_has_secret=True` |
| 13 | 只说「来壶酒」也能真的买到（引擎按场景补最便宜的酒） | **PASS** | `item=wine_shaodao verdict=success` |
| 14 | 目标在别的区域时会自己走过去（「帮小满收拾」→ 后厨） | **PASS** | `at=后厨 verdict=success` |
| 15 | 事实行里写出「一杯酒换来的那句杯边话」（模型改写不了） | **PASS** | `【第 1 回合 · 判定：成功】阿箬收下 3 个铜板，把桂花酿推到你面前。 【阿箬的杯边话】客人要是问起柜台后头那盏灯——灯底下压着东西。第三块砖别踩，踩了要赔` |
| 16 | 「请你喝一壶桂花酿」走送礼分支，返回正常结果而不是报错 | **PASS** | `verdict=success detail=阿箬收下 3 个铜板，把桂花酿推到你面前。` |
| 17 | 读心返回 NPC 脑子里的真实记忆行 | **PASS** | `6 条` |
| 18 | 读心也算一个回合（回合号推进） | **PASS** | `turn=3` |
| 19 | 回答末尾的判定行由引擎生成（含判定与状态） | **PASS** | `【第 3 回合 · 判定：只是看着】你盯着阿箬的眼睛，看见了一些不该看见的。 > 铜板 17｜风评 2｜被记账 0｜大堂 ` |
| 20 | 记忆里有「亲历 / 目击 / 传闻 / 杯边话」多种来源 | **PASS** | `kinds=['亲历', '旧事', '杯边话']` |
| 21 | 骰子与玩家名字无关（换名字复现同一串判定） | **PASS** | `obse succ obse fail succ succ succ obse succ clos` |
| 22 | 10 回合验证样例的判定与文档一致 | **PASS** | `obse succ obse fail succ succ succ obse succ clos` |
| 23 | 跨会话记忆来自 seekdb 的账本（不依赖额外服务） | **PASS** | `mode=seekdb sources=['seekdb']` |
| 24 | 打烊时把这一局写成「传说」存进 PowerContext | **PASS** | `{"available": true, "written": true, "status": 200, "artifact_id": "art_91c62422` |
