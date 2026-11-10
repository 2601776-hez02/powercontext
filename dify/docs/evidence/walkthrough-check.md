# 验证样例逐回合比对

> `python3 -m engine.spec_check` 生成；guest=`无名客`；共 10 回合，**全部 PASS**。

| # | 输入 | 结果 | 判定 | 差异 |
| --- | --- | --- | --- | --- |
| 1 | 我在靠窗那张桌子坐下，先打量一圈。 | **PASS** | `observed` | - |
| 2 | 要一壶桂花酿，铜板放在柜台上。 | **PASS** | `success` | - |
| 3 | 我问阿箬：柜台后头那盏灯，为什么夜里从来不灭？ | **PASS** | `observed` | - |
| 4 | 趁她低头算账，把柜台上的玉佩塞进袖子。 | **PASS** | `fail` | - |
| 5 | 我低头跟阿箬道歉，说刚才手滑了。 | **PASS** | `success` | - |
| 6 | 给老陶讲一个故事：有个客人偷了灯，灯却没有灭。 | **PASS** | `success` | - |
| 7 | 去后厨，替小满把那一摞碗端出来。 | **PASS** | `success` | - |
| 8 | 读心，看看小满现在在想什么。 | **PASS** | `observed` | - |
| 9 | 回大堂，再要一壶烧刀子，我要跟老陶喝一杯。 | **PASS** | `success` | - |
| 10 | 打烊了，我该走了。 | **PASS** | `closed` | - |
| 11 | （新开一局）我又来了，还认得我吗？ | **PASS** | `跨会话 hits=1 mode=seekdb` | memory_hits_min=✓，memory_source=✓，scene_mentions=✓，verdict=✓ |
