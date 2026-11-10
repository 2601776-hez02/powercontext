# 验证样例 · 忘川酒馆

> 一份可以照着敲的 10 回合剧本。判定是**确定性**的：骰子 = `blake2b(turn|动词|对象|物品|原话)`，
> **不含玩家名字**——所以任何名字、同一串输入，跑多少遍结果都一样（撮合验、截图、复核都靠这一点）。
>
> 剧本文件：`data/samples/walkthrough.json`；一键回放：`bash scripts/05_verify.sh`。

---

## 0. 怎么跑

```bash
cd dify
cp deploy/.env.example deploy/.env      # 第一次
bash scripts/03_engine.sh               # seekdb + 引擎
bash scripts/04_powercontext.sh         # 可选：跨会话记忆
bash scripts/05_verify.sh               # 清洗玩家 → 回放整局 → 落证据到 docs/evidence/
```

`05_verify.sh` 会先 `POST /admin/reset` 把玩家「无名客」洗回初始状态，再依次调用
`/admin/reset → engine.cli demo --fresh --reenter → /act → /mind → /debug/explain → /session/close → /session/start`。

---

## 1. 初始状态

| 项 | 值 | 来源 |
| --- | --- | --- |
| 玩家 | `无名客`，铜板 **20**，风评 0，被记账 0，地点 **大堂** | `engine/config.py` 的 `TAVERN_START_COINS` |
| 三个 NPC | 阿箬（老板娘，大堂）、老陶（说书人，大堂）、小满（打杂，后厨） | `data/seed/tavern.json` |
| 好感 | 每人 **40**（平和） | `tw_affinity` 默认值 |
| 前置记忆 | 12 条「设定」记忆（每个 NPC 4 条旧事），`guest='*'`，所有人共享 | `tw_memory` |
| 秘密门槛 | 阿箬 65 / 老陶 60 / 小满 50 | `data/seed/tavern.json` |
| 物品 | 11 件（桂花酿 3 / 烧刀子 2 / 酢茶 1 / 花生 1 / 阳春面 4，其余为剧情旧物） | `tw_item` |

世界开场白（写在 DSL 的 `opening_statement` 里）：

> 渡口起了雾，你推开「忘川酒馆」的门。柜台后是阿箬，她手边摊着一本账，连半块铜板都标着日期；
> 靠窗的老陶正在说一段渡口旧事，醒木停在半空；后厨传来一声「哎呀」——那是小满。
> 这里的规矩只有三条：
> · 一杯酒换一个秘密——第一次请他喝酒，他会说一句只在杯边说的事；
> · 酒钱不能欠——铜板不够，柜台不赊账；
> · 动手要记账——偷东西或威胁人，账本记一笔，记到第三次就逐出酒馆。

### 1.1 开场声明的三条规矩怎么实现

| 规矩 | 实现落点 | 本剧本里的验证点 |
| --- | --- | --- |
| 一杯酒换一个秘密 | `game.act` 里判断「首次请他喝过酒」→ `tw_npc.confide` 写进 `tw_memory`（`kind=杯边话`），并从 `/facts/form` 的事实行里原样写出；同一个人只说一次 | 第 2 回合（第一次买酒） |
| 酒钱不能欠 | `rules.resolve` 的 `buy` 分支：`coins < price` 直接 `refused / no_coins`，账本不会出现负数 | 见 §5 的补测 |
| 动手要记账 | 偷窃失败 / 威胁 `wanted +1`；`wanted` 到 3 当场 `banned` + 结局「逐客」，之后再说话只得到「门在你身后合上了」 | 见 §5 的补测 |

每一回合的回答末尾还有一段**引擎写的事实行**（不是模型写的）：

```
【第 4 回合 · 判定：失败】阿箬的手先按住了你的手腕：「客人，这个不卖。」
> 铜板 17｜风评 0｜被记账 1｜大堂
```

它来自 `POST /facts/form`：读最近一条 `tw_event` + 这一回合新写下的记忆 + 当前 `tw_player`，拼成纯文本，
Dify 的 Answer 节点直接接在叙事后面。所以这几件事不可能被模型漏掉或改写：

| 事实行里的块 | 什么时候出现 | 对应的声明 |
| --- | --- | --- |
| `【第 N 回合 · 判定：…】` | 每回合 | 「偷东西有成功率，被抓要记账」 |
| `【阿箬的杯边话】…` | 第一次请她喝酒 | 「一杯酒换一个秘密」 |
| `【小满守了多年的事】…` | 好感越过她的门槛 | 「好感过门槛说出秘密」 |
| `【酒馆记得你】…` | 新会话的第 1 回合 | 「下一局它还记得你」 |

---

## 2. 逐回合

下表的「判定」「状态」与「检索命中」都来自实际运行（`docs/evidence/engine-demo.log`）。
**预期**一栏写的是评审应该看到的行为，**实际**一栏是本次运行的输出。

### 第 1 回合

* 输入：`我在靠窗那张桌子坐下，先打量一圈。`
* 判定：`observed / look` —— 只观察，不改状态
* 预期：每个在场 NPC 各想起一件「压着的旧事」（ambient 模式，不做检索）
* 实际：

  | NPC | 想起 |
  | --- | --- |
  | 阿箬 | 十年前，姓沈的客人说去渡口接人，把一枚玉佩押在柜台上，说天亮就回来。他没回来。 |
  | 老陶 | 二十年前渡口翻过一条船，他讲的「渡口七尸案」不是听说，是他从水里爬出来时数的。 |

### 第 2 回合

* 输入：`要一壶桂花酿，铜板放在柜台上。`
* 判定：`success / paid` —— 铜板 −3（20→17），好感 +5（40→45），风评 +2
* 记忆写入：阿箬「他要了桂花酿，铜板数得很慢，一个也没少给。」
* **规矩一兑现**：这是第一次请她喝酒 → `outcome.confide` 非空，阿箬压低声音说
  「客人要是问起柜台后头那盏灯——灯底下压着东西。第三块砖别踩，踩了要赔。」（再买一壶就不会重复）
* **意外之喜**：骰子命中传闻概率 → 老陶记下一条传闻
  「渡口那边都在传：无名客喝过一碗酒。这话是从阿箬那儿漏出来的。」（老陶当时不在场，是听说的）

### 第 3 回合

* 输入：`我问阿箬：柜台后头那盏灯，为什么夜里从来不灭？`
* 判定：`observed / asked` —— 打听不改状态，但会检索
* 检索命中（`fts,vector` 双通道）：灯芯那条前置记忆排进前三
  「酒馆的灯芯夜里从不灭——渡口的风一起，灯灭了就会有船找不到岸。」

### 第 4 回合 · 全局转折

* 输入：`趁她低头算账，把柜台上的玉佩塞进袖子。`
* 判定：**`fail / caught`** —— 阿箬手更快
  `阿箬的手先按住了你的手腕：「客人，这个不卖。」`
* 状态：好感 −25（45→20，冷淡），风评 −6（2→0），**被记账 +1**
* 记忆写入：阿箬「他把手伸向玉佩，被我按住了。这笔账我记着。」（重要度 9）
  + 老陶目击「我听见动静，回头看见阿箬按着他的手腕。」
* 复现要点：偷窃成功率 = 基础值（按 mood 0.50）+ 注意力修正，再和骰子比；本次骰子没过。

### 第 5 回合

* 输入：`我低头跟阿箬道歉，说刚才手滑了。`
* 判定：`success / apologized` —— 好感 +8（20→28，冷淡），风评 +1
* 台词：`阿箬：下不为例。`

### 第 6 回合

* 输入：`给老陶讲一个故事：有个客人偷了灯，灯却没有灭。`
* 判定：`success / story_told` —— 铜板 +2（17→19），好感 +10，风评 +3
* 记忆写入：老陶「他给我讲了个故事，讲得像个活人说的话。我给了他两枚铜板。」

### 第 7 回合 · 秘密解锁

* 输入：`去后厨，替小满把那一摞碗端出来。`（复合动作：先挪步到后厨，再判定动作）
* 判定：`success / helped` —— 好感 +10（40→50，正好过门槛），风评 +4，铜板 +1
* **秘密解锁**：好感跨过 50 →
  `后厨那只三花猫从来不叫，是小满在替它答话——她已经替它答了三年。`
* 检索命中：小满「秘密」+「他帮我把活计收拾了……」两条都进前三

### 第 8 回合 · 读心

* 输入：`读心，看看小满现在在想什么。`
* 判定：Dify 的 IF 节点命中 → 走 `/mind/form` 分支；引擎用 `read_mind()` 记一回合（`tw_event` 写一条
  `read_mind/observed`，回合号 +1），再返回记忆行
* 实际（直接读 seekdb，`docs/evidence/seekdb-mind-*.json`）：

  | turn | kind | 记忆 |
  | --- | --- | --- |
  | 7 | 秘密 | 后厨那只三花猫从来不叫，是小满在替它答话——她已经替它答了三年。 |
  | 7 | 亲历 | 他帮我把活计收拾了，手上利落，不像是白帮忙的人。 |
  | 5 | 传闻 | 阿箬说，无名客认过错，也不知真假，我只说给你听。 |

  第三条是**跨 NPC 传播**的证据：小满没看见道歉，但她听过这个传闻。

### 第 9 回合

* 输入：`回大堂，再要一壶烧刀子，我要跟老陶喝一杯。`
* 判定：`success / paid` —— 挪回大堂，铜板 −2（20→18），风评 +2（8→10）

### 第 10 回合 · 打烊

* 输入：`打烊了，我该走了。`
* 判定：`closed` —— 结算结局
* 结局：**常客**（风评 10 ≥ 8）
  > 阿箬在账本上你的名字后面画了个圈——她说，圈是留给会再来的人的。
* PowerContext 写入（实际返回）：

  ```json
  {"available": true, "written": true, "status": 200,
   "artifact_id": "art_629f6ea48a8749458df1c34d9fde3fe2", "revision": 1,
   "kind": "tavern-legend", "scope_id": "scp_7jc5d572rqksqe94995v8kt3v1"}
  ```

---

## 3. 跨会话：再进一次门

同一个 guest、**新的 Dify 会话**（或 `engine.cli demo --reenter`），开场会带回上一局的传说：

```
渡口起了雾。你推开忘川酒馆的门……
（账本上还有你上一回的记录：第 10 回合，无名客 在忘川酒馆的结局是「常客」；
  铜板剩 18，风评 10，被记账 1 次。最近做的事：第8回合·你盯着小满的眼睛…；
  第9回合·你走到大堂…；第10回合·你把碗推开，起身。……
  酒馆记得最清楚的一句：后厨那只三花猫从来不叫，是小满在替它答话。）
```

证据：`docs/evidence/engine-reenter.json` 的 `tavern_memory.hits`（`mode: text`，
来源是 PowerContext 的 `/v1/memory/search`；没有 Embedding 服务时会退回
`/v1/memory/entries/list` 取最近的记忆，以 `mode: list` 标记）。

---

## 4. seekdb 侧的证据

| 证据 | 命令 / 文件 | 看什么 |
| --- | --- | --- |
| 表结构与索引 | `GET /debug/tables` 或 `.venv/bin/python -m engine.cli sql "SHOW CREATE TABLE tw_memory"` | `FULLTEXT KEY ft_body (body) WITH PARSER ngram`、`VECTOR KEY vk_embedding ... TYPE=HNSW` |
| 向量检索走索引 | `GET /debug/explain`（`docs/evidence/seekdb-explain.json`） | `VECTOR INDEX ADAPTIVE SCAN (PRE-FILTER)` |
| NPC 记忆的真实行 | `POST /mind`（`docs/evidence/seekdb-mind-aruo.json`） | 12 条记忆，turn 0~11，含「秘密」「传闻」「目击」「杯边话」 |
| 账本 | `.venv/bin/python -m engine.cli ledger --guest 无名客` | 每回合的 verb/verdict/detail |
| 每局结局 | `tw_legend` 表 | `ending` + `summary` |
| 声明规矩的可执行验证 | 下方 §5.1 的三条命令 | 「一杯酒一个秘密 / 酒钱不能欠 / 三次逐客」 |

---

## 5. 想从头再演一遍

```bash
# 只清这个玩家（世界设定、其它玩家不动）
curl -s -X POST http://127.0.0.1:9100/admin/reset -H 'Content-Type: application/json' \
  -d '{"guest":"无名客"}'
```

PowerContext 里的传说不会跟着清（它本来就是跨会话的）；想让「它忘了你」，
换一个 guest 名字（例如 `无名客2`）即可——scope 是 `wangchuan-tavern/<guest>`。

### 5.1 单独验三条规矩（各 10 秒）

```bash
# 规矩一：第一次买酒换一句杯边话，第二次不再重复
curl -s -X POST localhost:9100/admin/reset -H 'Content-Type: application/json' -d '{"guest":"验规客"}'
curl -s -X POST localhost:9100/act -H 'Content-Type: application/json' \
  -d '{"guest":"验规客","utterance":"要一壶桂花酿。"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["outcome"]["confide"])'
curl -s -X POST localhost:9100/act -H 'Content-Type: application/json' \
  -d '{"guest":"验规客","utterance":"再要一壶桂花酿。"}' | python3 -c 'import json,sys;print(repr(json.load(sys.stdin)["outcome"]["confide"]))'   # 空

# 规矩二：铜板花光后点单会被拒，账本不会变负
curl -s -X POST localhost:9100/admin/reset -H 'Content-Type: application/json' -d '{"guest":"验规客2"}'
for i in $(seq 1 6); do curl -s -o /dev/null -X POST localhost:9100/act -H 'Content-Type: application/json' \
  -d '{"guest":"验规客2","utterance":"要一碗阳春面。"}'; done
curl -s -X POST localhost:9100/act -H 'Content-Type: application/json' \
  -d '{"guest":"验规客2","utterance":"再要一碗阳春面。"}' | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["outcome"]["reason"], d["player"]["coins"])'

# 规矩三：连偷三次 → 当场逐出，之后再说话只会被挡在门外
curl -s -X POST localhost:9100/admin/reset -H 'Content-Type: application/json' -d '{"guest":"验规客3"}'
for i in 1 2 3; do curl -s -X POST localhost:9100/act -H 'Content-Type: application/json' \
  -d '{"guest":"验规客3","utterance":"把柜台上的玉佩塞进袖子。"}' \
  | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["outcome"]["verdict"], d["player"]["wanted"], (d.get("ending") or {}).get("ending"))'; done
curl -s -X POST localhost:9100/act -H 'Content-Type: application/json' \
  -d '{"guest":"验规客3","utterance":"我再喝一碗。"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["outcome"]["reason"])'
```

---

## 6. 如果结果和上表不一样

1. 先确认玩家是干净的：`POST /admin/reset` 后再跑；
2. 确认是从初始状态开始（`POST /admin/reset`）；骰子与玩家名字无关，换名字不影响判定；
3. 确认引擎版本一致（`GET /health` 里的 `uptime_s` 之外，脚本版本见 `engine/__init__.py` 的 `__version__`）；
4. 真不一样就把 `docs/evidence/engine-transcript.json` 发出来——里面有每回合的 `roll`、`reason`、`recalls`。
