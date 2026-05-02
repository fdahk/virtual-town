# 2026-05-02 世界尺寸 vs 布局错配 ——「暂时不可达」刷屏的真正根因

## 一、症状回顾

22 NPC 测试时多次观察到：

1. 大量 NPC 长时间执行同一类决策、不产生记忆 / 互动 ——
   被前面阶段处理过一次（吞吐扩容到 16 槽位、决策锁、错峰启动、连接池放大），
   但症状没有完全消失；
2. 单个 NPC（先后是「小芳」）持续刷屏：
   - 第一阶段：`绕行中，寻找建筑入口 → 到达目的地 → 绕行中…`（已修，
     见 `20260502-recovery-wander-loop.md`）；
   - 第二阶段：`小芳：目标 loc_hobbs_cafe_interior 暂时不可达，先在附近闲逛`
     每秒 1-2 次刷屏（命中黑名单分支但仍每个 ai_tick 重决策）。

第一阶段处理后，第二阶段一旦重启游戏不再复现，但只是黑名单短期撑住，
**目标依然真实不可达**——只要时间够长一定还会再现。

本次复盘把这个"真实不可达"的真正物理原因找了出来。

## 二、真正的根因：默认 outdoor 尺寸 90×60 装不下硬编码布局

### 2.1 矛盾点

`backend/app/domain/world_gen/outdoor.py` 里 `BUILDING_LAYOUT` /
`HOME_LAYOUT` / `OUTDOOR_AREAS` 是**硬编码坐标**，按设计 PM 选定的
"参数化模板"分布在 120×90 的小镇布局里：

| 类别        | 最远坐标                   | 需要的最小地图尺寸 |
|-----------|------------------------|-----------|
| 北区住宅 ayan | door=(104, 24)          | width≥105 |
| 中区住宅 xiaoke | door=(114, 55)          | width≥115 |
| 南区住宅 laomu | door=(80, 82)           | height≥83 |
| 农场屋     | door=(44, 72)           | height≥73 |
| 木工坊     | door=(92, 69)           | width≥93 / height≥70 |
| forest_grove | bounds 32×87 → 右下 (32,87) | height≥88 |

但 `app/core/config.py` 默认值是 `world_gen_outdoor_default_width=90` /
`height=60`——**两个数字在所有维度上都装不下硬编码布局**。

### 2.2 为什么之前没爆掉？

`outdoor.py` 里 `set_tile` 越界**静默 return**：

```python
def set_tile(x: int, y: int, **kw: Any) -> None:
    if not (0 <= x < width and 0 <= y < height):
        return  # ← 静默跳过
    tiles[y * width + x].update(kw)
```

后果：

1. `block_rect` 把建筑墙体写入网格——超出地图边界的墙体被静默丢弃；
2. 但 `Location.entry_tiles` / `Portal.from_tile` **仍然按硬编码坐标
   写入数据库**，比如 `portal_home_chenbo_in.from_tile = (92, 24)`；
3. NPC 决策时 `astar()` 把 portal_tile 当成目标，
   `grid.is_walkable(92, 24)` 因 `in_bounds` 失败永远返回 False，
   `_astar_with_fallback` 直接返回 `[]`；
4. rule_agent 走 `_make_recovery_or_wait` 兜底；
   - 因为是越界、不是真正"被墙挡住"，`_recovery_tile_bfs` 也搜不到
     任何能从 origin 到达且在地图内的格子，最终走 wait；
5. 引擎写入 5 分钟黑名单。下一次决策命中黑名单 →
   `_wander_within_scene_or_wait` 的"暂时不可达"分支 → 每个 ai_tick 刷屏。

### 2.3 受影响的 NPC（默认 22 NPC + 默认 90×60 时）

```
loc_farmhouse           door (44, 72)  → height 越界
loc_woodshop            door (92, 69)  → width / height 越界
loc_home_chenbo         door (92, 24)  → width 越界
loc_home_ayan           door (104, 24) → width 越界
loc_home_yueling        door (102, 55) → width 越界
loc_home_xiaoke         door (114, 55) → width 越界
loc_home_laomu          door (80, 82)  → height 越界
loc_home_xiaodi         door (90, 82)  → width / height 越界
loc_forest_grove        entry (16, 72) → height 越界
loc_farmland            entry (44, 73) → height 越界
```

也就是说，**8 户 NPC 永远进不了自己的家**、农场屋老板/木工永远进不了
自己的工作地——他们一启动就进入"暂时不可达"循环，吃满后台 LLM 配额、
也产生不出新记忆，正是用户最早报告的"很多 NPC 一直在执行一样的决策、
没有产生记忆数据"。

## 三、修复

### 3.1 把默认尺寸提升到 120×90

`backend/app/core/config.py`：

```python
world_gen_outdoor_default_width: int = Field(default=120, ge=120, le=200)
world_gen_outdoor_default_height: int = Field(default=90, ge=90, le=200)
```

`.env.example` 同步：

```bash
WORLD_GEN_OUTDOOR_DEFAULT_WIDTH=120
WORLD_GEN_OUTDOOR_DEFAULT_HEIGHT=90
```

`ge` 约束直接调到 120 / 90，**杜绝任何人再把默认值调小到布局装不下**。

### 3.2 fail-fast：地图装不下硬编码布局立刻报错

在 `build_outdoor_tiles()` 入口加 `_assert_layout_within_bounds()`：

- 遍历 BUILDING_LAYOUT / HOME_LAYOUT / OUTDOOR_AREAS；
- 检查 bounds 框、door、door 外一格（portal_out 落点）、area entry；
- 任何越界 → 立即 `ValueError`，错误消息列出全部冲突项 + 修复建议。

替代旧的 `set_tile` 静默跳过——以后再有人把 `WORLD_GEN_OUTDOOR_*`
调到 80 或者把 BUILDING_LAYOUT 加到地图外，新游戏 / seed 立即启动失败，
不会再产生 "看似跑起来但 NPC 一半人僵住" 的诡异世界。

### 3.3 BFS 解卡半径 5→8

`rule_agent._recovery_tile_bfs` 的 `max_radius` 从 5 提到 8。半径 5 在
河岸围栏 / 大型障碍物背后的角色容易 BFS 不到任何可走格、被迫 stuck=True
原地僵住；8 给了足够余量绕过单条围栏 / 一道墙体（增加的成本只是 BFS
在最坏情况下多访问 ~50 格，量级可忽略）。

### 3.4 加 CI 守门员：`backend/tests/test_world_gen_reachability.py`

5 + 2 = 7 个测试覆盖：

1. `test_every_location_entry_is_walkable`：每个 `Location.entry_tiles[0]`
   在所属 scene 内、网格上 walkable；
2. `test_outdoor_locations_reachable_from_center`：outdoor 上每个 location
   entry 必须能从 (54, 42)（玩家初始点 / 中央广场）A* 到达；
3. `test_every_portal_from_tile_is_walkable`：每个 portal 的 from_tile
   在 from_scene 内可走；
4. `test_indoor_entry_reachable_from_portal_landing`：每间室内 location
   的 entry 能从 portal 落地点 (7, INDOOR_H-2) A* 到达——验证家具
   布局没有把入口区切断；
5. `test_outdoor_to_indoor_chain_reachable`：端到端 outdoor 中央 →
   outdoor portal_in → 室内 entry 整链可达，覆盖 NPC 真实上班路径；
6. `test_build_outdoor_tiles_rejects_too_small_map`：90×60 必须抛
   `ValueError`、错误消息包含尺寸 + 越界条目；
7. `test_build_outdoor_tiles_accepts_default_size`：120×90 必须正常返回
   ≥10 公共建筑 + ≥12 住宅。

任何破坏世界连通性的改动（新加建筑压住主街、装饰物落在门格上、
室内家具堵入口、布局/尺寸不匹配 …）都会让这一组断言失败。

## 四、未覆盖 / 下一步

1. **现有存档**：旧存档 `outdoor` 是 90×60，本次默认改动**不会自动迁移**。
   只对"新游戏"生效。已有用户存档继续用旧尺寸——其中 8 户家 + 农场屋 +
   木工坊仍然不可达。建议要么手动开新游戏，要么后续补一条
   "存档导入时若 outdoor 尺寸 < 120×90 则按比例放大并重布局"的迁移；
   现阶段人工确认即可，不做自动迁移以免破坏对比测试用的存档。
2. **越界写库的历史 portal/location**：apply_plan 这一侧没改——理论上
   生成器层 fail-fast 就够了，但如果未来允许 LLM/玩家手动 patch
   portal 坐标，应在 apply_plan 也做 in_bounds 校验。
3. **决策刷屏二级解**：本次没加"命中黑名单时返回长 duration wait"的
   降频策略，因为根因（真实不可达）已经消除；如果未来还有别的
   合理性问题导致黑名单频繁触发，可以参考前一轮 `npc智能决策.md` §6
   留下的设计点。

## 五、验证

```
$ cd backend && pytest tests/test_world_gen_reachability.py -v
========================== 7 passed in 0.40s ==========================

$ pytest tests/ --deselect tests/test_dialogue.py::test_rule_intent_threaten -q
119 passed, 7 skipped, 1 deselected, 10 warnings in 3.81s
```

修复落地后默认新游戏的 22 NPC 会全部住进各自的家、走到各自的工作地，
再不会因为门口越界而集体 "暂时不可达"。
