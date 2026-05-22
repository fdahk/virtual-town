# 2026-05-02 NPC「绕行→到达」死循环

## 一、症状

新游戏跑起来后，单个 NPC（小芳）的事件流持续刷屏：

```
16:45:34 agent.action_finished  小芳 到达目的地
16:45:34 agent.action_started   小芳：绕行中，寻找建筑入口
16:45:33 agent.action_finished  小芳 到达目的地
16:45:33 agent.action_started   小芳：绕行中，寻找建筑入口
... （每秒 1-2 次循环）
```

约 6 秒内 ~14 条 started/finished 事件，描述始终是「绕行中，寻找建筑入口」。
NPC 视觉上原地几格抖动，没有进展。

## 二、根因

### 2.1 调用链

`decide_human_action`（`app/domain/simulation/rule_agent.py`）跨场景路径：

```
NPC 在 scene A，日程目标在 scene B
  ├── _find_portal_from_to(A, B) → portal_tile P
  ├── _astar_with_fallback(start → P) ❌ 失败（NPC 走不到 portal P）
  └── _make_recovery_or_wait(...)
       ├── _recovery_tile_bfs(5 格内) ✅ 找到空地 R
       ├── astar(start → R) ✅ 成功
       └── 返回 wander 决策（path 短小，metadata = {} 空）
                                            ↑
                                      ★ Bug 所在
```

### 2.2 引擎放大循环

`engine._apply_decision`（`app/domain/simulation/engine.py`）读 metadata：

```python
stuck = bool(meta.get("stuck"))                    # False
unreachable_loc = meta.get("unreachable_location_id")  # None
if unreachable_loc:
    self._enqueue_unreachable(...)                 # ← 不会进入
if not stuck:
    agent.last_decision_at = now_dt                # 按 ai_tick 节奏重决策
```

→ NPC 不可达目标 **没被加入黑名单**，下个 ai_tick 又选同一个 location_id，
又走同一条调用链，又 wander 5 格，又"到达目的地"。

频率高低取决于世界倍速 + `ai_tick_minutes`，但**循环本身的根因独立于频率**。

### 2.3 设计意图错位

`_make_recovery_or_wait` 原注释只声明：「**返回 wait 时**才打 stuck=True +
unreachable_location_id」。但实际上 wander 分支也是「找不到通往真正目标的路径」
的副产物，只是因为 BFS 在 5 格内总能找到空地，所以**永远走 wander 分支**——
导致黑名单永远不会被写入。

## 三、修复

### 3.1 短期 fix（本次落地）

`app/domain/simulation/rule_agent.py::_make_recovery_or_wait`：

- **wander 分支**：metadata 也带 `unreachable_location_id`（如果传入了），
  让引擎把目标加入 5 分钟黑名单。
- **不带 `stuck=True`**：NPC 实际在动，按正常 ai_tick 节奏重决策即可，
  避免每个 tick 都重新决策（会被黑名单挡住，但仍浪费 LLM/规则开销）。
- **wait 分支**保持原有行为不变（stuck=True + unreachable_location_id）。

修复后下次决策链路：

```
T+0s   stuck → wander 5 格 + 黑名单写入 loc_target
T+ai_tick  下次决策：slot.location_id in blacklist → 走
           _wander_within_scene_or_wait → 半径 5 漫步，描述变成
           "目标 X 暂时不可达，先在附近闲逛"
T+5min     黑名单过期 → 再尝试一次（如果世界拓扑修复了就好，
           没修复就再写一次黑名单，循环刷新）
```

### 3.2 测试

新增 `tests/test_rule_agent_unreachable.py::test_recovery_wander_marks_target_unreachable`：
构造一道横墙把 NPC 起点与 portal 隔开，验证 wander 决策的 metadata 必须带
`unreachable_location_id` 且不带 `stuck`。

全套回归 112 passed / 7 skipped / 1 deselected。

## 四、未解决：为什么 portal 真不可达

短期 fix 治标不治本——它消除了「每秒刷屏」的诡异观感，但 NPC 仍然到不了
建筑入口，5 分钟黑名单过期后会再次尝试。**根本原因在世界拓扑层**，可能是：

1. **建筑入口 portal 位置不正确**：generator 生成的 portal `from_tile` 落在
   建筑外 walkable 区域之外（比如墙体或装饰物里），`is_walkable(portal_tile)`
   = False，astar 自然走不到。
2. **户外路径被障碍物挡住**：generator 在 outdoor 放对象（树、栅栏、水池）
   时挡住了通往 portal 的所有路径。
3. **多场景 portal 拓扑断链**：portals 表里 from_scene → to_scene 方向写反，
   或者中转 portal 缺失。

排查步骤（下一次出现该现象时执行）：

```python
# 在 backend 里 dump：
# 1. 该 NPC 当前 (scene_id, x, y)
# 2. 该 NPC 当前日程的 location_id 和 entry_tiles
# 3. 该 location 所在 scene 与 NPC 当前 scene 的 portal 列表
# 4. portal_tile 的 is_walkable() 与周围 8 格的 walkable 状态
# 5. astar(NPC 位置, portal_tile) 的实际路径长度

# 或更直接：写一个 /api/debug/agent/{id}/path-diagnose 临时端点
```

短期可观察 `agent:{id}:unreachable` Redis set（修复后会有 location_id 写入），
统计哪些 location 反复被拉黑——那些就是世界生成 bug 的高发位置。

## 五、教训

- **rule_agent 兜底分支必须考虑"被反复触发"的稳态行为**，不能只看单次决策
  能不能产出 valid action。这次 wander 分支单看完全合理（NPC 在动），但
  与"5 分钟后才过期"的黑名单 + ai_tick 决策节奏组合时，缺失 unreachable
  标记就放大成了死循环。
- **凡是 wander/wait 兜底分支，都应统一通过 metadata 与引擎对话**，而不是
  让"是否标记"成为隐式约定。这次把 wander 与 wait 两个分支的 metadata 协议
  对齐就解决了。
- **下一阶段世界生成器应增加自检**：每个 location 应该有至少一条从该
  location 所在 scene 的任意 walkable tile 出发能 A* 到达 portal_tile 的
  路径。生成时跑一次 reachability check，不通过则报错（或自动 unblock 一条
  走廊）。
