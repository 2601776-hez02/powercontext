# 忘川酒馆 · 一个会记住你的文字酒馆

> 2026 OceanBase 数据库大赛 · 生态赛题 **「Just for Fun：用 Dify 做一个好玩的智能应用」** 参赛作品
>
> Dify 1.19.0 × seekdb 1.4.0 × PowerContext 1.5.0

---

## 0. 一句话

**你走进渡口的「忘川酒馆」，三个 NPC 会记住你干过的每一件事**——你偷过的玉佩、讲过的故事、道过的歉都会留在他们的记忆里；下次换个会话再进门，酒馆还认得出你，甚至会把你的传闻说给旁人听。

酒馆的「记忆」不是一个字符串数组，而是 seekdb 里带 **全文索引 + HNSW 向量索引**的真实数据；跨会话的「传说」则交给 PowerContext 的 Memory 保存与检索。

---

## 1. 好玩在哪

### 1.1 三个 NPC，三种记性

| NPC | 身份 | 说话方式 | 他会记住什么 |
| --- | --- | --- | --- |
| **阿箬** | 老板娘 | 话少、先算账、动气时只说「记账上」 | 你欠的每一分钱、你伸手拿过的东西、柜台第三块砖 |
| **老陶** | 说书人 | 评书腔、爱打比方、听完故事要收两枚铜板 | 每个人的脚步、二十年前渡口翻的船、谁在他碗里放过东西 |
| **小满** | 打杂 | 一声「哎呀」开头、问什么都先答「我什么都没看见」 | 三年前那块桂花糖、后厨那只不叫的三花猫 |

### 1.2 玩法：一回合一句话

你输入任何一句中文——「我把玉佩塞进袖子」「给老陶讲个故事」「去后厨替小满端碗」——引擎会：

1. 判定这件事**成不成**（偷东西有成功率，被抓要记账）；
2. **在事务里**改账本：铜板、好感、物品、风评、被记账次数；
3. 让在场的 NPC **各写一条第一人称记忆**，还有概率变成传闻传播给没看见的人；
4. **检索**此刻每个 NPC 会想起什么（全文 + 向量），把「想起的旧事」交给 Dify 写进台词；
5. 把「这一回合到底发生了什么」以**纯文本事实行**附在回答末尾——**由引擎写，模型改不了**。

### 1.2.1 酒馆开场就声明的三条规矩，逐条都能验

开场白里写着「一杯酒换一个秘密；酒钱不能欠；动手的人，记账上」。这三条不是气氛话：

| 声明的规矩 | 实现 | 怎么验 |
| --- | --- | --- |
| **一杯酒换一个秘密** | 第一次请某个 NPC 喝酒（或当着他的面买酒）→ 他会说一句只在杯边说的事；**同一个人不会说第二遍** | 买一壶桂花酿 → 阿箬说「柜台后头那盏灯底下压着东西」；再买一壶 → 不再提 |
| **酒钱不能欠** | 铜板不够直接拒绝（`reason=no_coins`），账本里不会出现负数 | 把铜板花到 0，再点一碗面 → 「你差着」 |
| **动手的人，记账上** | 偷窃失败 / 威胁 → 被记账 +1；**记到第三次当场逐出**（结局「逐客」，之后再说话只会得到「门在你身后合上了」） | 连偷三次玉佩 → 第 3 次判定 `banned` + 结局 |

另外两个长期后果：好感越过门槛，NPC 会**说出守了多年的秘密**（阿箬的信、老陶的眼睛、小满的猫）；风评攒够 25 分，你就是渡口的**「传说」**。

> 判定与状态永远以引擎为准：Dify 的叙事节点只负责写小说，**判定行（「【第 N 回合 · 判定：失败】…」+「> 铜板…」）由 `/facts/form` 生成后直接拼进回答**，模型没有机会把失败写成成功。

### 1.3 独门玩法：读心

随时输入「读心，看看阿箬现在在想什么」，引擎会把**这个 NPC 脑子里的真实记忆行**摊开给你看（`POST /mind/form`）——包括她记下你刚才做的事、她听来的传闻、以及你请她喝过酒时她说漏的那句。**读心也算一个回合**：会记进账本、推进回合号。这是玩法，也是「seekdb 真的在存记忆」最直观的证据。

### 1.4 跨会话：「它还记得你」

一局结束（「打烊了」）时，这一夜会被写成一段「传说」存进 PowerContext；下一局开场，引擎把它检索回来，NPC 的第一句话就会提到你的上一回。

---

## 2. 技术分工（谁干什么，不能互相替代）

```
┌──────────────────────────────────────────────────────────────────────┐
│ 浏览器 / Dify API                                                    │
│   │                                                                  │
│   ▼                                                                  │
│ Dify 1.19.0（Chatflow，advanced-chat）                               │
│   ① 开场 / 回合 的条件判断（IF/ELSE + 会话变量 opened）               │
│   ② LLM 节点：把玩家一句话解析成结构化动作 JSON                      │
│   ③ HTTP Request 节点：调用酒馆引擎 /act/form（表单提交）             │
│   ④ LLM 节点：按引擎给出的事实写小说体叙事（引用 NPC 的回忆）         │
│   ⑤ Answer 节点：输出正文 + 状态行                                    │
└───────────────┬──────────────────────────────────────────────────────┘
                │ HTTP（JSON）
                ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 酒馆引擎（FastAPI，dify/engine/，:9100）                              │
│   判定规则 / 事务 / 记忆写入 / 混合检索 / 结局                        │
└───────┬───────────────────────────────────────────┬──────────────────┘
        │ MySQL 协议（pymysql）                      │ HTTP（Memory API）
        ▼                                           ▼
┌───────────────────────────────┐        ┌──────────────────────────────┐
│ seekdb 1.4.0（:2881，库 test） │        │ PowerContext 1.5.0（:8010）   │
│  tw_player   账本/铜板/风评   │        │  Memory(scope=               │
│  tw_npc      三个 NPC 与秘密  │        │    wangchuan-tavern/<酒客>)  │
│  tw_affinity 好感             │        │  记「你这个酒客的传说」       │
│  tw_item/inventory 物品       │        │  → 下一局开场检索回来         │
│  tw_event    每回合流水       │        └──────────────────────────────┘
│  tw_memory   ★NPC 记忆        │
│    FULLTEXT(body) WITH PARSER │
│      ngram                    │
│    VECTOR KEY(embedding)      │
│      DISTANCE=COSINE HNSW     │
│  tw_legend   每一夜的结局     │
└───────────────────────────────┘
```

| 组成 | 在这个作品里实际承担什么 | 为什么非它不可 |
| --- | --- | --- |
| **Dify**（必选） | 全部流程编排：开场/回合分支、动作 JSON 解析、工具调用、叙事生成、输出格式 | 没有它就没有「一句话 → 判定 → 小说」这条链；DSL 见 `dsl/` |
| **seekdb**（选定） | ① 酒馆的**唯一事实源**：状态、事务、账本；② **NPC 记忆的检索**：同一条 SQL 里既用全文索引也用 HNSW 向量索引；③ 事务回滚兜底非法动作 | 记忆要「按人按会话过滤 + 排序 + 只取前几条」，还得能原子更新账本；把记忆放内存或 JSON 里做不到可复现的检索与回滚 |
| **PowerContext**（额外） | 跨会话的**长期记忆**：每局结束写一条 `tavern-legend`，下局开场按 scope 检索回来 | 它管的是「跨进程、跨会话、多用户 scope 隔离」的记忆，和 seekdb 里「这一局的短期记忆」是两种生命周期，刻意分开 |

> 题目允许「seekdb 或 PowerContext 二选一」。这里两个都用，但**分工不重叠**：seekdb 管局内事实，PowerContext 管跨局传说。关掉 PowerContext（`POWERCONTEXT_ENABLED=0`）游戏照样能玩，只是开场不再提你的上一回。

### 2.1 一回合的时序

```
玩家：趁她低头算账，把柜台上的玉佩塞进袖子
  │
  ├─① Dify LLM「拆动作」 → {"verb":"steal","target":"aruo","item":"jade_pei","tone":"sneaky"}
  ├─② Dify HTTP → POST /act/form
  │     ├ seekdb：查账本/好感/在场（tw_player/tw_affinity/tw_npc）
  │     ├ 规则判定（骰子 = hash(guest,turn,动作)，可复现）→ 失败，被记账
  │     ├ 一个事务：tw_event + tw_player(wanted+1) + tw_affinity(-25)
  │     │            + tw_memory(阿箬：「他把手伸向玉佩，被我按住了…」)
  │     │            + tw_memory(老陶：目击) + 可能 tw_memory(传闻)
  │     └ 混合检索：MATCH(body) AGAINST(...) ∪ cosine_distance(...) APPROXIMATE
  │                 → RRF 融合 → 每个在场 NPC 想起 1~3 条
  ├─③ Dify LLM「叙事」 → 「阿箬的手先按住你的手腕。老陶：话说回来……」
  ├─④ Dify HTTP → POST /facts/form
  │     └ seekdb：取这个玩家最近一条 tw_event + 当前状态，回一段纯文本：
  │        【第 N 回合 · 判定：失败】阿箬的手先按住了你的手腕…
  │        > 铜板 17｜风评 0｜被记账 1｜大堂
  └─⑤ Answer = ③的正文 + ④的事实行（判定行由引擎写，模型无从改写）
```

---

## 3. 目录结构

```
dify/
├── README.md                     ← 本文（运行说明）
├── dsl/
│   └── wangchuan-tavern.chatflow.yaml   Dify Chatflow 的 DSL（导入即用）
├── engine/                       酒馆引擎（FastAPI + pymysql）
│   ├── app.py                    HTTP 接口
│   ├── game.py                   一回合的编排
│   ├── rules.py                  判定规则（纯函数，可单测）
│   ├── recall.py                 记忆写入 + 全文/向量混合检索
│   ├── embed.py                  确定性向量化（1024 维，无外部依赖）
│   ├── parse.py                  兜底动作解析
│   ├── db.py / config.py         seekdb 访问层 / 配置
│   ├── powercontext.py           PowerContext HTTP 客户端
│   └── cli.py                    命令行：init / act / demo / mind / sql
├── data/
│   ├── seed/tavern.json          世界设定：3 个 NPC、11 件物品、12 条前置记忆
│   └── samples/walkthrough.json  验证样例的剧本（10 回合）
├── scripts/                      一键脚本（01 检查 → 05 验证）
├── deploy/
│   ├── .env.example              引擎与记忆层的配置模板
│   └── dify_provision.py         导入 DSL / 发布 / 建 API Key 的小工具
├── tests/test_logic.py           22 项单测（不需要数据库与模型）
└── docs/
    ├── sample-walkthrough.md     验证样例：初始状态 / 输入 / 预期 / 实际
    ├── evidence/                 截图与日志（seekdb 证据、Dify 节点记录）
    └── limitations.md            已知边界
```

---

## 4. 运行环境

| 项 | 要求 | 本次验证环境 |
| --- | --- | --- |
| 操作系统 | Linux x86_64（macOS 也可跑引擎，Windows 请用 WSL2） | Alibaba Cloud Linux 3 |
| CPU / 内存 | 4 核 / **8 GiB 起**（Dify 全家桶约 4 GiB，seekdb 约 2~4 GiB） | 4 vCPU / 16 GiB |
| 磁盘 | ≥ 15 GiB 空闲（镜像约 4 GiB） | 100 GiB ESSD |
| Docker | 24+，**Compose v2.24+**（`env_file` 里的 `VECTOR_STORE` 需要它） | Docker 26.1.3 / Compose 2.27.0 |
| Python | **3.11+**（PowerContext 的要求；引擎本身 3.10+） | 3.12.16 |
| 模型服务 | 任一 OpenAI 兼容的对话模型（本作品用 **DeepSeek `deepseek-flash`**，也可换通义/MiniMax/本地 Ollama） | api.deepseek.com |
| Embedding | **不需要**：向量由 `engine/embed.py` 本地确定性生成（1024 维字符 n-gram 哈希） | — |

软件版本（全部固定）：

* Dify **1.19.0**（自部署，官方 `docker/` 目录 + `COMPOSE_PROFILES=seekdb,postgresql`）
* seekdb **1.4.0**（镜像 `oceanbase/seekdb:latest`，由 Dify 的 compose 一并拉起）
* PowerContext **1.5.0**（PyPI，`powercontext[server,cli]`）
* 引擎依赖：FastAPI / uvicorn / PyMySQL / python-multipart（见 `engine/requirements.txt`）

---

## 5. 从零跑起来

> 下面四步都在仓库的 `dify/` 目录下执行。首次约 15~25 分钟（主要是拉镜像）。

### 第 0 步：检查环境

```bash
cd dify
bash scripts/01_check.sh
```

### 第 1 步：拉起 Dify + seekdb

```bash
bash scripts/02_dify.sh
```

脚本做三件事：取 Dify 1.19.0 的 `docker/` 目录 → 写 `.env`（`COMPOSE_PROFILES=seekdb,postgresql`、`VECTOR_STORE=seekdb`）和 `envs/security.env`（`SECRET_KEY`、`INIT_PASSWORD`）→ `docker compose up -d` 并等到 `:8080/console/api/setup` 可用。

* 控制台：<http://127.0.0.1:8080>
* 初始管理员：邮箱随意（如 `tavern@example.com`），密码见 `deploy/dify/envs/security.env` 的 `INIT_PASSWORD`（默认 `DifyTavern2026`）
* seekdb：`127.0.0.1:2881`，用户 `root`，密码 `difyai123456`，库 `test`

### 第 2 步：起酒馆引擎（建表 + 灌世界数据）

```bash
cp deploy/.env.example deploy/.env     # 改密码/端口就在这里
bash scripts/03_engine.sh
```

成功后 `curl http://127.0.0.1:9100/health` 会打印 seekdb 版本、每张表的行数、PowerContext 状态。

### 第 3 步：起 PowerContext（跨会话记忆；可跳过）

```bash
bash scripts/04_powercontext.sh        # 装 powercontext[server,cli]==1.5.0 并起 :8010
```

跳过也没关系：`deploy/.env` 里设 `POWERCONTEXT_ENABLED=0`，引擎会自动降级（`/health` 里 `available: false`）。

### 第 4 步：装模型插件 + 导入 DSL（命令行，一条都别少）

```bash
export DIFY_INIT_PASSWORD=DifyTavern2026     # = deploy/dify/envs/security.env 里的 INIT_PASSWORD
export DIFY_PASSWORD=DifyTavern2026          # 管理员密码
python3 deploy/dify_provision.py login
```

`login` 会依次做：验证初始化口令 → 创建管理员 → 登录（Dify 1.19 的 console 用
Cookie + `X-CSRF-Token`，登录密码还要先 Base64，脚本里都处理好了）。

**4.1 装模型供应商插件**（Dify 1.19 的模型能力全部来自插件，镜像里没有内置供应商）

```bash
python3 deploy/dify_provision.py plugin   --identifier "langgenius/openai_api_compatible:0.0.73@4cf123ffaa4556f079bed6c47f3a462cc825504b859b183239e74eb19136a4b5"
```

插件标识来自 Marketplace 的清单：<https://marketplace.dify.ai/api/v1/dist/plugins/manifest.json>，
换成别的版本时把 `0.0.73@<sha256>` 一起换掉。

**4.2 配模型**（本作品只需一个对话模型；不需要 Embedding / Rerank）

```bash
python3 deploy/dify_provision.py credentials   --provider langgenius/openai_api_compatible/openai_api_compatible   --model deepseek-flash   --json '{"endpoint_url":"https://api.deepseek.com/v1","api_key":"<你的 key>","mode":"chat","context_size":"65536","max_tokens_to_sample":"8192"}'
python3 deploy/dify_provision.py models        # 应该能看到 deepseek-flash
```

> 注意：`openai_api_compatible` 没有 provider 级凭据，凭据挂在**每个模型**上；字段名是
> `endpoint_url`（不是 `base_url`）。这是 Dify 1.19 插件 schema 的约定，写错了会静默不生效。

**4.3 导入 DSL、发布、拿 API Key**

```bash
python3 deploy/dify_provision.py import --file dsl/wangchuan-tavern.chatflow.yaml
python3 deploy/dify_provision.py publish
python3 deploy/dify_provision.py apikey        # 输出 app-xxxxxxxx
python3 deploy/dify_provision.py chat --message "要一壶桂花酿，铜板放在柜台上。"
```

也可以走界面：控制台 → 创建应用 → **导入 DSL** → 选 `dsl/wangchuan-tavern.chatflow.yaml`
→ 右上角「发布」。DSL 里两个 LLM 节点写的是 `deepseek-flash`，换成你自己的模型名即可。

**4.4 关于请求地址（两个容易踩的坑）**

* DSL 里 HTTP 节点的地址是 `http://172.17.0.1:9100/act/form` 与 `/mind/form`——宿主机在 Docker
  默认网桥上的地址。引擎不在这台机器上时改成 `<引擎IP>:9100`。
* Dify 1.19 的 HTTP 节点默认**禁止访问私网地址**，必须允许宿主机的网段，否则会报
  `blocked by SSRF protection`。`scripts/02_dify.sh` 已经写好了：
  `.env` 里 `SSRF_PROXY_ALLOW_PRIVATE_IPS=172.17.0.0/16`，改完后要 `docker compose up -d --force-recreate ssrf_proxy`。
* HTTP 节点用 `x-www-form-urlencoded` 提交，玩家输入里带引号/换行也不会破坏 JSON。

### 第 5 步：跑验证样例

```bash
bash scripts/05_verify.sh                                  # 引擎侧：判定 / 记忆 / 检索 / 执行计划
python3 deploy/dify_walkthrough.py --base http://127.0.0.1:8080 \
        --engine http://127.0.0.1:9100 --guest 无名客       # Dify 侧：走完整条 Chatflow
```

第一个脚本清洗玩家状态、跑完 10 回合剧本、把证据写进 `docs/evidence/`；
第二个脚本用 Dify 的应用 API 逐句发送同样的 10 句，把每轮 **Dify 生成的叙事**存成
`docs/evidence/dify-walkthrough.json`。逐条对照见 [`docs/sample-walkthrough.md`](docs/sample-walkthrough.md)。

本次实际跑出来的样子（`docs/evidence/dify/`）：

| 截图 | 说明 |
| --- | --- |
| ![编排](docs/evidence/dify/00-workflow-canvas.png) | Chatflow 的 11 个节点：开始 → 拆动作 → 是否读心（IF）→ 两条分支（引擎/读心 → 叙事 → 事实行）→ 回答 |
| ![对话](docs/evidence/dify/02-chat-memory.png) | 真实对话：阿箬说「客人上回也是这么数铜板的，一个也没少给」——她引用的是上一局留在 seekdb 里的记忆 |

---

## 6. 怎么体验

1. 打开 Dify 里的应用（`http://127.0.0.1:8080/app/<app-id>`），或把应用嵌进网页；
2. 第一次输入任意一句话（例如「有人吗」），会看到开场白与三条提示；
3. 之后每回合输入一句话，观察：NPC 的台词、你的铜板/风评、以及「NPC 为什么会这么说」；
4. 想验证「记忆是真的」：输入「读心，看看阿箬现在在想什么」，或直接查库：
   ```bash
   .venv/bin/python -m engine.cli mind --guest 无名客 --npc aruo
   .venv/bin/python -m engine.cli sql "SELECT npc,kind,turn,body FROM tw_memory WHERE guest='无名客' ORDER BY id DESC LIMIT 5"
   ```
5. 输入「打烊了，我该走了」结束一局；再点「开启新对话」（同一个「你的名字」），第一句话就会提到你的上一回——
   PowerContext 里的传说是按 `scope = wangchuan-tavern/<你的名字>` 存的。
6. 想直接看「她到底记了什么」，在对话框输入「读心，看看阿箬现在在想什么」——这条会走 IF 分支，
   引擎把 `tw_memory` 里属于阿箬的真实记忆行原样交给模型。

---

## 7. 数据与初始化

* **世界初始数据**：`data/seed/tavern.json`（3 个 NPC / 11 件物品 / 12 条前置记忆）；`python -m engine.cli init` 幂等写入 seekdb，重复执行不会重复插入。
* **知识库**：本作品**不依赖** Dify 知识库——NPC 的记忆是运行时产生的，不是预置文档。若你想额外给叙事加一份「酒馆设定集」知识库，Dify 的向量会写进同一个 seekdb（`VECTOR_STORE=seekdb`），步骤见 [`docs/limitations.md`](docs/limitations.md)。
* **重置**：`POST /admin/reset {"guest":"无名客"}` 或 `.venv/bin/python -m engine.cli reset --guest 无名客`，只清这个玩家，世界设定不动。

---

## 8. 验证样例（摘要）

完整版见 [`docs/sample-walkthrough.md`](docs/sample-walkthrough.md)。10 回合剧本在 `data/samples/walkthrough.json`，判定是**确定性的**（骰子 = `blake2b(guest|turn|动词|对象|物品|原话)`）。

> 复跑前必须先把玩家洗回初始状态（`05_verify.sh` / `dify_walkthrough.py` 都会自动做 `POST /admin/reset`）：
> 回合号会继续累加，骰子随之变化，所以「不重置直接再来一遍」的判定本来就和下表不同——这是设计，不是不确定。

| # | 输入 | 判定 | 关键看点 |
| --- | --- | --- | --- |
| 1 | 我在靠窗那张桌子坐下，先打量一圈。 | observed | 每个在场 NPC 各想起一件「压着的旧事」（ambient） |
| 2 | 要一壶桂花酿，铜板放在柜台上。 | success | 铜板 -3；阿箬写记忆；**传闻**跑到老陶耳朵里；**规矩一：她说了一句杯边话** |
| 3 | 我问阿箬：柜台后头那盏灯，为什么夜里从来不灭？ | observed | 全文检索命中「灯芯夜里从不灭」那条前置记忆 |
| 4 | 趁她低头算账，把柜台上的玉佩塞进袖子。 | **fail（caught）** | 好感 -25、被记账 +1；阿箬与老陶各写一条记忆 |
| 5 | 我低头跟阿箬道歉，说刚才手滑了。 | success | 好感 +8、风评 +1 |
| 6 | 给老陶讲一个故事：有个客人偷了灯，灯却没有灭。 | success | 铜板 +2、风评 +3 |
| 7 | 去后厨，替小满把那一摞碗端出来。 | success（含挪步） | 好感过门槛 → **秘密解锁**（三花猫） |
| 8 | 读心，看看小满现在在想什么。 | observed | 走 IF 分支调用 `/mind/form`，返回她脑子里真实的记忆（含传闻与杯边话）；这一下也记一回合 |
| 9 | 回大堂，再要一壶烧刀子，我要跟老陶喝一杯。 | success | 回到大堂、继续记账 |
| 10 | 打烊了，我该走了。 | closed | 结局「常客」；PowerContext 写入传说 |

每一回合的回答末尾都会带一行引擎写的事实行（详见 `docs/evidence/dify-walkthrough.json`）：

```
【第 4 回合 · 判定：失败】阿箬的手先按住了你的手腕：「客人，这个不卖。」
> 铜板 17｜风评 0｜被记账 1｜大堂
```

---

## 9. 已知限制

见 [`docs/limitations.md`](docs/limitations.md)。三条要点：

1. **向量通道只做词面相近**：`embed.py` 是字符 n-gram 哈希（无外部 Embedding 服务、可离线复现），同义改写靠全文通道与叙事模型兜底；文档不宣称语义检索。
2. **规则是硬编码的**：判定逻辑在 `engine/rules.py`，NPC 的自由发挥由 Dify 的 LLM 节点承担，但「能不能偷到」由引擎说了算，避免模型编造状态。
3. **Dify 的 HTTP 节点依赖网络连通**：引擎地址写死在一个节点里，换机器要改（README 第 4.3 步）。

---

## 10. 复用与出处

* `deploy/dify/` 是 `scripts/02_dify.sh` 现场下载的上游 `docker/` 副本（约 900 KB，已 gitignore），
  仓库里不重复提交；第一次跑 02 脚本时会自动生成。
* Dify 的 `docker/` 目录来自上游 <https://github.com/langgenius/dify> 的 **1.19.0** tag，`scripts/02_dify.sh` 原样取用，未改动其编排逻辑；只通过 `.env` / `envs/security.env` 配置。
* seekdb 镜像 `oceanbase/seekdb:latest`（Dify 1.19.0 compose 里自带的 `seekdb` profile，官方支持它作为 Dify 的向量存储）。
* PowerContext 使用 PyPI 上的 `powercontext==1.5.0`（与比赛仓库 `oceanbase/powercontext` 同源），只调用它的 HTTP Memory API。
* 世界设定、NPC 文案、判定规则、引擎代码、DSL 均为本次原创。

---

## 11. 提交材料对照表

| 题目要求 | 位置 |
| --- | --- |
| Dify DSL + 导入顺序 | `dsl/wangchuan-tavern.chatflow.yaml`（单个应用，导入即用） |
| 运行说明 | 本文 |
| 验证样例（初始状态/输入/步骤/预期/实际） | `docs/sample-walkthrough.md` + `data/samples/walkthrough.json` |
| 实际运行结果与证据 | `docs/evidence/`（引擎日志、seekdb 查询、Dify 节点记录与截图） |
| 自定义后端源码 + 依赖 + 启动说明 | `engine/` + `engine/requirements.txt` + `scripts/03_engine.sh` |
| 数据/初始化方式 | `data/seed/tavern.json` + `scripts/03_engine.sh`（`engine.cli init`） |
| 预置记忆/互动状态的标识方式 | 玩家标识 = Dify 会话里的 `guest` 输入（DSL 变量）；PowerContext scope = `wangchuan-tavern/<guest>` |
| 现成插件 | 仅用 Dify 官方的模型供应商插件（OpenAI-API-compatible / DeepSeek），无第三方自定义插件 |
