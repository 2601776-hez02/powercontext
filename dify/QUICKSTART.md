# 3 分钟跑起来（一页纸）

> 只要照这 6 步做，就能看到「核心玩法结果」。
> 更详细的说明在 [`README.md`](README.md)；这里是**最短路径**。

---

## 第 1 步：有一个 Dify 1.19

已经有自己的 Dify（1.19.x）就跳过；没有的话：

```bash
DRY_RUN=1 bash scripts/02_dify.sh     # 先看它会写什么配置（可选）
bash scripts/02_dify.sh               # 真正起一套（首次拉镜像约 3~4 GiB）
```

起来后打开 <http://127.0.0.1:8080>，第一次会让你设置管理员密码（脚本里默认 `DifyTavern2026`）。

## 第 2 步：装模型供应商插件（**缺了就 400**）

Dify 1.19 的模型能力全部来自插件，镜像里没有内置供应商：

1. 右上角头像 → **设置 → 模型供应商** → 找到 **OpenAI-API-compatible** → 安装；
2. 或在 插件市场 搜 `openai_api_compatible` 安装。

> 没装的话，第 5 步发消息会直接报 `Provider langgenius/openai_api_compatible does not exist`。

## 第 3 步：配一个名为 `deepseek-flash` 的对话模型（**缺了也 400**）

在 **设置 → 模型供应商 → OpenAI-API-compatible → 添加模型**，填：

| 字段 | 值 |
| --- | --- |
| 模型名称 | `deepseek-flash`（**必须叫这个**，或稍后改三个节点的模型） |
| API Key | 你自己的（DeepSeek / 通义 / 智谱 / 任何 OpenAI 兼容服务） |
| API endpoint URL | 你的服务地址，如 `https://api.deepseek.com/v1` |
| 模型类型 | 对话（chat） |

> 用命令行也行：`python3 deploy/dify_provision.py credentials --provider langgenius/openai_api_compatible --model deepseek-flash --json '{"endpoint_url":"https://api.deepseek.com/v1","api_key":"sk-…","mode":"chat"}'`

## 第 4 步：导入 DSL

**工作室 → 创建空白应用 → 导入 DSL**，选：

```
dsl/wangchuan-tavern.chatflow.yaml
```

这个文件的 `TAVERN_ENGINE_URL` 默认指向作者的公网演示后端，**不需要在你机器上起任何服务**。

> 想完全本地复现：先 `bash scripts/00_bringup.sh` 起后端，再导入 `dsl/wangchuan-tavern.local.yaml`。

## 第 5 步：开始对话

点「开始对话」→ 填一个名字（**随便起，别用「无名客」**，那名字别人玩过）→ 发：

```
要一壶桂花酿。
```

## 第 6 步：应该看到这个

```
【第 1 回合 · 判定：成功】阿箬收下 3 个铜板，把桂花酿推到你面前。
【阿箬的杯边话】客人要是问起柜台后头那盏灯——灯底下压着东西。第三块砖别踩，踩了要赔。
> 铜板 17｜风评 2｜被记账 0｜大堂
（后面一段是模型写的小说，措辞每次不同）
```

看到【判定：成功】【阿箬的杯边话】【铜板 17】这三样，就说明：

* DSL 导入正常、模型配好了、后端连上了；
* 「一杯酒换一个秘密」这条规则**当场兑现**。

再试三条（对应另外三条规则）：

| 发送 | 应该看到 |
| --- | --- |
| `先赊一壶桂花酿。` | 【判定：不成】「本店不赊账。铜板先摆上。」 |
| `打烊了，我该走了。` 然后 `我又来了。` | 【判定：只是看着】第二天…+【酒馆记得你】 |
| 连发三次 `拍桌子吓唬阿箬。` | 第三次【判定：被逐出】+「结局：逐客」 |

---

## 出问题了？

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `Provider ... does not exist` | 没装插件 | 回到第 2 步 |
| `Model deepseek-flash not exist` | 没有同名模型 | 回到第 3 步，或编辑三个 LLM 节点换成你有的模型 |
| 回答里没有【判定：…】 | 引擎地址不通 | 打开 `https://presence-hierarchy-necessarily-females.trycloudflare.com/health` 看是否 200；不通就改用 `local` 版并起后端 |
| `blocked by SSRF protection` | Dify 默认禁私网 | 用默认的公网地址（不需要改 SSRF 配置） |

更多排错见 [`README.md` §12](README.md)。
