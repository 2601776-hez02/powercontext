# 空库首次启动（自动生成）

> 新建一个空数据库，直接起引擎，验证「评审第一次跑」这条路：建表、灌种子、跑一回合。

## health

```json
{"ok":true,"uptime_s":12.2,"seekdb":{"version":"5.7.25-OceanBase seekdb-v1.4.0.0","dsn":"root@127.0.0.1:2881/tavern_fresh","tables":{"tw_memory":15,"tw_event":1,"tw_player":1,"tw_npc":3,"tw_item":11,"tw_legend":0}},"powercontext":{"enabled":true,"available":true,"url":"http://127.0.0.1:8010","status":200},"config":{"start_coins":20,"max_turns":40,"gossip":true}}
```

## 跑一回合

```
verdict = fail
detail  = 阿箬的手先按住了你的手腕：「客人，这个不卖。」
memory  = ['亲历：他把手伸向玉佩，被我按住了。这笔账我', '目击：我听见动静，回头看见阿箬按着他的手腕', '传闻：渡口那边都在传：全新客2动过柜台上的']
status  = > 铜板 20｜风评 0｜被记账 1｜大堂
```

## 记忆里存的原话

```json
[{"npc":"aruo","kind":"亲历","raw":"把柜台上那枚玉佩塞进袖子里。"},{"npc":"laotao","kind":"目击","raw":"把柜台上那枚玉佩塞进袖子里。"},{"npc":"xiaoman","kind":"传闻","raw":"把柜台上那枚玉佩塞进袖子里。"}]
```
