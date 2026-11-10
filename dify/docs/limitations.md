# 已知边界与取舍

写在这里的都是「知道不够好、但故意的」地方，评审可以据此判断哪些是能力边界、哪些是 bug。

## 1. 向量通道只做「词面相近」，不做语义

`engine/embed.py` 是**确定性字符 n-gram 哈希**（1024 维、无符号、L2 归一化），不调用任何
Embedding 服务。原因：

* 评审机器不需要额外 API Key，断网也能复现；
* 同一句话永远得到同一个向量，验证样例的检索结果才能重复出现；
* 512 条以内的小记忆库，哈希向量足够把「偷 / 摸走 / 顺走」落在相近的桶里。

它做不到的事：**同义改写**（「顺手牵羊」跟「偷玉佩」的余弦接近 0）。这类情况靠两件事兜住：

1. seekdb 的全文通道（`MATCH ... AGAINST` + ngram 分词）是主检索通道，权重 1.0 > 向量的 0.65；
2. Dify 的动作解析 LLM 节点会把自由说法归一成 `steal` 这样的动词，规则判定不依赖检索。

如果要真语义检索，把 `engine/embed.py` 换成调用你的 Embedding 服务即可，DDL 里的
`VECTOR(1024)` 需要跟着改维度并重建表。

## 2. 判定是硬编码规则，不是模型自由裁决

`engine/rules.py` 里写死了成功率、好感增减、结局阈值。这是刻意的：

* 模型不能凭空决定「你偷到了没有」——否则账本、库存、结局都会被幻觉污染；
* 骰子由 `blake2b(guest|turn|动词|对象|物品|原话)` 派生，**同一串操作永远同一个结果**，
  验证样例才可复现。注意它含 `turn`：**不 reset 直接再玩一遍，回合号继续累加，判定本来就会变**
  （`05_verify.sh` 与 `dify_walkthrough.py` 都会先 `POST /admin/reset`）。

模型负责的是**叙事与语气**（Dify 的 LLM 节点），不是判定。为了让这一点在界面上也可核对，
Dify 的 Answer 节点除了叙事，还会拼上 `/facts/form` 返回的**事实行**：

```
【第 4 回合 · 判定：失败】阿箬的手先按住了你的手腕：「客人，这个不卖。」
> 铜板 17｜风评 0｜被记账 1｜大堂
```

这一行由引擎从 `tw_event` + `tw_player` 直接生成，模型无法改写；所以「失败被写成成功」这类
声明与结果不符的情况在结构上不会发生。

开场声明的三条规矩（一杯酒换一个秘密 / 酒钱不能欠 / 动手的人记账上）都有对应实现，
`docs/sample-walkthrough.md` §5.1 给了三条 10 秒就能跑完的验证命令。

## 3. Dify 默认不许 HTTP 节点访问私网（必须放行宿主机网段）

Dify 1.19 的 HTTP Request 节点默认拒绝私网地址，直连酒馆引擎会报
`blocked by SSRF protection`。放行方法是给 **ssrf_proxy** 容器开白名单：

```bash
# dify/deploy/dify/.env
SSRF_PROXY_ALLOW_PRIVATE_IPS=172.17.0.0/16      # docker0 网段
docker compose up -d --force-recreate ssrf_proxy agent_ssrf_proxy
```

`scripts/02_dify.sh` 已自动写入这一行。改完必须重建 ssrf_proxy 容器（只重启 api 没用，
Squid 的 ACL 是在容器启动时从环境变量渲染的）。

## 4. 依赖 Dify 的 HTTP 节点地址

DSL 里有三个 HTTP Request 节点，地址都是 `http://172.17.0.1:9100/...`（Linux 上 Docker 默认网桥的
宿主机地址）：`/act/form`、`/mind/form`、`/facts/form`。如果引擎装在别的机器、或 Docker 网络被改过，
这三个 URL 要一起改：

* 同机 Docker：`172.17.0.1`（`ip -4 addr show docker0` 可确认）
* 引擎在另一台机器：改成引擎的 IP
* 引擎跑在容器里并与 Dify 同网络：改成 `http://tavern-engine:9100`

## 5. 单机部署、单进程假设

引擎用 `uvicorn` 单进程 + 每线程一条 seekdb 连接。真实并发下没有做连接池调优；
`POST /admin/reset` 也没有鉴权（它只服务本地验证）。要上线需要：加鉴权、把 reset 关掉、
把引擎放进 compose 网络、给 seekdb 配账号权限。

## 6. seekdb 的两个用途，主次分明

* **主用途（必选、已验证）**：酒馆业务库——状态、事务、账本、NPC 记忆（全文 + HNSW）。
* **可选用途（未作为验收前提）**：把 Dify 知识库的向量也存进同一个 seekdb
  （`VECTOR_STORE=seekdb`）。本次没有做，因为 Dify 的知识库需要额外的 Embedding 模型，
  而本作品刻意不引入 Embedding 依赖。

如果要启用：

1. 在 Dify 里配置一个 Embedding 模型（OpenAI 兼容接口即可）；
2. 建一个知识库，把「酒馆设定集」之类的文档灌进去；
3. 用 `docker exec seekdb mysql -h127.0.0.1 -uroot -p... -D test -e "show tables"` 能看到
   Dify 建的集合表——这就是「seekdb 承载 Dify 向量存储」的实物证据。

## 7. 中文分词的边界

全文索引用 `WITH PARSER ngram`（`ngram_token_size=2`）。它对中文短句很够用，但对
单字查询（如「灯」）会退化成大量候选；`engine/recall.py` 里的 FTS 查询会先试原句、
没命中再试抽出来的关键词。

## 8. 结局阈值

`wanted >= 3` 逐客、`reputation >= 25` 传说、`>= 8` 常客。10 回合的验证剧本拿不到「传说」，
需要玩得更久——这是设计意图（一局 10 回合是「常客」，多来几夜才是传说）。

## 9. 国内拉镜像可能很慢

官方 compose 要拉 10 个镜像、约 4 GiB。国内主机直连 Docker Hub 常超时，建议给 dockerd 配
`registry-mirrors`（`/etc/docker/daemon.json`）。本次实测可用的镜像源按速度大致是：

| 镜像源 | 大镜像（dify-api 2.7 GB） | 小镜像（busybox 4 MB） |
| --- | --- | --- |
| `docker.m.daocloud.io` | 约 50 分钟，稳定 | 7 秒 |
| `docker.1panel.live` | 可用 | 30 秒 |
| `docker.1ms.run` | 不稳（多次卡住） | 较慢 |

个别镜像卡住时，可以单独 `docker pull <mirror>/<image>:<tag>` 再 `docker tag` 成官方名字，
compose 就会直接用本地镜像。
