# 新增物品 & 自然事件 SOP

> 适用版本：当前主干分支  
> 维护人：开发团队  
> 每节末尾的"✅ 完成标志"是合并前必须通过的验收条件。

---

## 目录

1. [核心架构速览](#1-核心架构速览)
2. [强制约束：美术资源规范](#2-强制约束美术资源规范)
3. [物品生命周期与运行时变化](#3-物品生命周期与运行时变化)
4. [SOP-A：新增世界物品（WorldObject）](#sop-a新增世界物品worldobject)
5. [SOP-B：新增自然事件（NaturalEvent）](#sop-b新增自然事件naturalevent)
6. [SOP-C：新增交互动词与运行时效果](#sop-c新增交互动词与运行时效果)
7. [两者联动：带自然事件的新物品](#两者联动带自然事件的新物品)
8. [常见错误与排查](#常见错误与排查)
9. [快速检查清单](#快速检查清单)

---

## 1. 核心架构速览

```
seed.py               ← 物品在地图上的定义（位置、状态、交互）
    │
    ▼
PostgreSQL (world_objects 表)
    │
    ▼
engine.py / EngineObject   ← 运行时内存缓存，dirty flag 控制回写
    │
    ├── natural_events.py  ← 事件 Handler 修改 EngineObject.state → 生成 WorldEvent
    │       │
    │       ▼
    │   rule_agent.py      ← 规则型 NPC 响应（storm/fire 强制行为）
    │   agent_decision.py  ← LLM 感知上下文（_append_natural_context）
    │
    ▼
WebSocket → simulation.delta.events[]
    │
    ▼ (TownPage.tsx 转发)
eventBus  ←→  TownScene.ts（Phaser 视觉效果层）
                    │
                    ▼
              ObjectPanel.tsx（点击物品后右侧交互面板）
```

**关键约定**

| 层 | 文件 | 职责 |
|---|---|---|
| 数据定义 | `backend/app/db/seed.py` | 物品的位置、类型、初始 state、交互列表 |
| 类型约束 | `backend/app/schemas/world.py` | Pydantic 序列化，`object_type` Literal |
| 前端类型 | `frontend/src/types/domain.ts` | TypeScript 侧 `WorldObject.object_type` |
| 事件逻辑 | `backend/app/domain/simulation/natural_events.py` | `NaturalEventHandler` 子类 |
| 前端事件类型 | `frontend/src/game/eventBus.ts` | `GameEvent` union type |
| 前端事件转发 | `frontend/src/pages/TownPage.tsx` | `_NATURAL_EVENT_TYPES` Set |
| 视觉效果 | `frontend/src/game/scenes/TownScene.ts` | `addEffect` / `removeEffect` |

---

## 2. 强制约束：美术资源规范

> ❌ **严禁**：任何持久存在于游戏世界中的物品或事件视觉效果，不得仅靠代码绘制的纯色块（`this.add.rectangle` / `gfx.fillStyle`）作为最终外观交付。  
> ✅ **必须**：每个新物品和自然事件效果必须有真正的美术资源作为视觉表现。

### 美术资源的三个来源（按优先级）

| 优先级 | 来源 | 适用场景 |
|---|---|---|
| 1 | **Kenney Tiny Town tileset**（已有） | 绝大多数室外物品、植物、建筑装饰 |
| 2 | **下载 OpenGameArt / Kenney 免费资源** | Tiny Town 缺失的物品种类 |
| 3 | **Phaser 程序化效果**（粒子/tween） | 只用于**瞬态动态效果**（火焰、萤火虫、水波），不能用于静态物品外观 |

---

### 规则 R1：静态物品必须使用 tile_frame

所有静态、持久的 `WorldObject` **必须**在 `state` 中指定 `tile_frame`（Kenney Tiny Town 帧序号）。

```python
# ✅ 正确
state={"tile_frame": 94}

# ❌ 错误 —— 不能用纯色块作为物品的最终外观
state={"color": 0x3AB83A}
```

`state.color` 只允许作为**临时占位**，在提交合并前必须替换为真实 tile。

**如何查找正确的 tile_frame？**

```bash
# 方法 A：查看已有速查表（见 A-2 节）
# 方法 B：用脚本分析 tileset 图像
cd /Users/mac/virtual-town/backend && source .venv/bin/activate
python scripts/analyze_tiles.py  # 输出帧序号预览（如有此脚本）

# 方法 C：直接查看 Kenney Tiny Town 文档
# 下载地址：https://www.kenney.nl/assets/tiny-town
# 对照 tileset.png，帧从左上角 0 开始，按行排列，每行 tile_columns 个
```

---

### 规则 R2：自然事件效果必须有视觉锚点

自然事件（如蘑菇出现、水坑形成）的视觉效果允许用程序化绘制，但**物品本体**（蘑菇区地块、水坑地面）必须已有 tile_frame，程序化效果只是叠加层。

```
物品底层（tile_frame）+ 动态效果叠加（addEffect）= 最终外观
   ↑ 必须有真实美术      ↑ 可以程序化
```

---

### 规则 R3：Kenney Tiny Town 没有的物品先下载再接入

若 Tiny Town tileset 中无合适帧，执行以下流程：

#### 步骤 1：在 Kenney.nl 查找免费资源

```
https://www.kenney.nl/assets  →  搜索物品关键词  →  下载 CC0 授权包
```

常用资源包：
- `kenney.nl/assets/tiny-town` — 当前主 tileset
- `kenney.nl/assets/tiny-dungeon` — 室内/地下场景
- `kenney.nl/assets/tiny-battle` — 战斗/营地场景
- `opengameart.org` — 搜索 CC0/CC-BY 授权的像素图

#### 步骤 2：在 `fetch_assets.sh` 中注册下载

**文件**：`scripts/fetch_assets.sh`

```bash
# 下载新资源（示例）
download_asset \
  "https://example.com/path/to/asset.png" \
  "frontend/public/assets/tilesets/my_new_asset.png" \
  "CC0 · Kenney.nl"

# 若使用 CC-BY 授权，同时在 CREDITS 区块追加：
# SOURCE: My Asset - Author Name - License URL
```

#### 步骤 3：在 `tilesets.manifest.json` 注册新 tileset（若是新图集）

**文件**：`frontend/public/assets/manifest/tilesets.manifest.json`

```jsonc
{
  "tilesets": {
    "kenney_tiny_town": { ... },   // 已有
    "my_new_tileset": {            // 新增
      "url": "/assets/tilesets/my_new_asset.png",
      "tile_width": 16,
      "tile_height": 16,
      "tile_columns": 8,
      "total": 64
    }
  }
}
```

#### 步骤 4：在 `TownScene.ts` 的 `preload()` 中加载

```typescript
this.load.spritesheet("my_new_tileset", m.tilesets.tilesets.my_new_tileset.url, {
  frameWidth: 16,
  frameHeight: 16,
});
```

#### 步骤 5：在 `renderObjects` 中使用新 tileset

若物品使用非默认 tileset，在 `WorldObject.state` 中加入 `tileset_key`：

```python
# seed.py
state={"tile_frame": 5, "tileset_key": "my_new_tileset"}
```

```typescript
// TownScene.ts renderObjects()
const tilesetKey = obj.state?.tileset_key ?? KENNEY_TILE_KEY;
const img = this.add.image(cx, cy, tilesetKey, tileFrame);
```

---

### 规则 R4：程序化效果的分类许可

| 类型 | 是否允许程序化 | 说明 |
|---|---|---|
| 静态物品外观 | ❌ 禁止 | 必须用 tile_frame |
| 自然事件起始/结束瞬态 | ✅ 允许 | 火花、消散粒子 |
| 持续循环效果 | ✅ 允许 | 火焰跳动、萤火虫闪烁、水波 |
| 天气叠加层 | ✅ 允许 | 雨/雾/雷全屏半透明 |
| 状态指示图标 | ✅ 允许 | 蘑菇"已出现"绿圈提示 |

---

## 3. 物品生命周期与运行时变化

> 世界中的物品**不是**只在 `seed.py` 阶段被创建一次的"静态家具"。任何 `WorldObject` 都可以：
> - 在运行时**改变状态**（蘑菇出现/枯萎、果实成熟/被摘、火焰点燃/熄灭、花朵开合）
> - 在运行时**被新生成**（NPC 在田里收获掉落、玩家放置道具、自然事件投放新物品）
> - 在运行时**被销毁**（蘑菇被采摘后消失、临时道具被消耗）
>
> 任何缺乏"运行时反馈"的物品都是**未完成**的物品。下面列出**唯一允许**的实现路径。

### 3.1 三类运行时变化必须使用对应 API

| 场景 | 后端 API | 前端事件 | 视觉效果 |
|---|---|---|---|
| **状态改变**（state 字段更新） | `engine.apply_object_state_change(obj_id, patch, ...)` | `world.object_state_changed`（自动包含完整 state） | 自动通过 `refreshObjects` 重画 objectLayer |
| **运行时生成** | `engine.spawn_object(scene_id=..., name=..., ...)` | `world.object_spawned` | 新对象出现在 `objectLayer` |
| **运行时销毁** | `engine.despawn_object(obj_id, ...)` | `world.object_despawned` | 对象从地图消失 + 关联效果层（fire/mushroom/firefly...）一起清理 |

⚠️ **不允许**绕过这三个 API 直接改 DB 或 `EngineObject.state`。绕过会导致前端不感知，物品视觉永远不更新。

---

### 3.2 自然事件 Handler 中的状态变化模板

在 `natural_events.py` 的 `_evt(...)` 调用中，**只要事件涉及对象状态**就必须传入 `obj=obj`：

```python
# ✅ 正确：obj 自动注入 state 到 payload，前端 _OBJECT_LIFECYCLE_TYPES 自动消费
events.append(self._evt(
    ctx,
    event_type="nature.mushroom_appeared",
    description=f"{obj.name} 周围冒出了新鲜蘑菇",
    target=obj.id,
    importance=2,
    obj=obj,   # ← 关键：必传
))

# ❌ 错误：手写 payload 漏掉 state，前端无法刷新视觉
events.append(self._evt(
    ctx,
    event_type="nature.mushroom_appeared",
    description="...",
    target=obj.id,
    payload={"object_id": obj.id, "x": obj.x, "y": obj.y},   # ← 缺 state！
))
```

`_evt(... obj=obj)` 的等价输出 payload：

```jsonc
{
  "object_id": "obj_xxx",
  "x": 22, "y": 19,
  "name": "公园草丛",
  "scene_id": "scene_town_outdoor",
  "state": { "mushroom_present": true, "mushroom_ticks": 0, "tile_frame": 29 }
}
```

---

### 3.3 玩家交互的状态副作用

在 `player_service.py::_apply_interaction_effect` 中，按交互动词分支：

```python
# ✅ 正确：用 engine API 修改状态，自动广播
if action == "pick" and obj_state.get("mushroom_present"):
    engine.apply_object_state_change(
        eng_obj.id,
        {"mushroom_present": False, "mushroom_ticks": None},
        actor=player_eng.id,
        description=f"{player_eng.name} 在 {obj_name} 采到了一朵蘑菇",
        event_type="nature.mushroom_picked",
    )
    return f"你采到了一朵蘑菇 🍄", "mushroom"
```

约定：
- patch 字典中 value=`None` 表示**删除**该 state 字段（如 `mushroom_ticks` 的计时器）
- patch 字典中其他值表示**新值**（如 `mushroom_present: False`）
- 同时改多个字段就把它们都放进 patch

---

### 3.4 完全销毁物品（消耗后消失）

如果物品本身是"一次性"的（如玩家放置的临时火堆），用 `despawn_object`：

```python
# 比如玩家点燃后被烧光的篝火
if action == "extinguish" and obj_state.get("on_fire"):
    engine.despawn_object(
        eng_obj.id,
        actor=player_eng.id,
        description=f"{player_eng.name} 踩灭了篝火，木炭散落",
    )
    return "篝火被踩灭了", None
```

`despawn_object` 内部会：
1. 标记 `pending_delete=True`
2. 下个 tick 的 `_persist_tick` 从 DB 删除
3. 广播 `world.object_despawned` → 前端 `removeSceneObject` + `refreshObjects` 清掉视觉
4. 同时触发 `removeEffect()` 清理 `effectLayer` 中可能残留的火焰/萤火虫/水波等动效

---

### 3.5 运行时生成新物品

适用场景：
- 自然事件投放（如农场每天清晨自动出现新作物）
- NPC 行为副作用（如砍树后地上掉一块木头）
- 玩家行为（如撒下种子）

**使用方式**：

```python
# 在自然事件 Handler 中
new_obj = ctx.engine.spawn_object(   # ⚠️ ctx 暂未直接暴露 engine，见下文
    scene_id=ctx.scene_id,
    name="掉落的木头",
    object_type="item",
    x=tree.x,
    y=tree.y + 1,
    blocks_movement=False,
    available_interactions=["pick", "inspect"],
    state={"tile_frame": 25, "pickable": True},
    tags=["wood", "drop"],
    description="一棵树被砍倒，掉下了木头",
)

# 在 player_service / 任务回调中
engine = get_simulation_runtime().engine
engine.spawn_object(...)
```

> 当前 `NaturalEventContext` 还未直接暴露 engine 指针；如果你的事件需要 spawn，请通过 `ctx.engine = self`（在 engine.py 构造 ctx 时注入）扩展，参考 SOP-B 的"扩展 ctx 字段"流程。

---

### 3.6 前端如何"自动"消费这些事件

无需在每个新事件类型上手动加代码 —— `TownPage.tsx` 中的 `_OBJECT_LIFECYCLE_TYPES` Set 是**白名单**：

```typescript
const _OBJECT_LIFECYCLE_TYPES = new Set([
  "world.object_state_changed",
  "world.object_spawned",
  "world.object_despawned",
  // 也包含会改变 state 的自然事件：
  "nature.mushroom_appeared", "nature.mushroom_withered", "nature.mushroom_picked",
  "nature.fruit_ripened",     "nature.fruit_picked",
  "nature.flower_bloomed",    "nature.flower_withered",
  "nature.fishing_spot_appeared", "nature.fish_caught",
  "nature.puddle_formed",     "nature.puddle_dried",
  "world.fire_started",       "world.fire_extinguished",
]);
```

新增任何"会改变物品 state"的事件类型时，**必须**：
1. 把事件类型字符串加入此 Set
2. 同时加入 `eventBus.ts` 的 `GameEvent` union（payload 类型用 `ObjectLifecyclePayload`）

---

### 3.7 视觉效果层的清理责任

`TownScene` 的 `effectLayer`（火焰/萤火虫/水波等程序化动效）是和 `objectLayer` 平行的图层。

| 事件 | objectLayer 行为 | effectLayer 行为 |
|---|---|---|
| `world.object_despawned` | 自动移除该对象 | 同步调用 `removeEffect(object_id)` 清掉动效 |
| `world.object_state_changed`（picked/withered 类） | 自动重画整个 objectLayer | `nature.*_picked`/`*_withered`/`*_dried` 触发 `removeEffect` |
| `world.object_state_changed`（appeared/bloomed 类） | 自动重画 | `nature.*_appeared`/`*_bloomed` 触发 `addEffect` |

如果你新增的事件**有视觉动效**（如萤火虫闪烁），并且这个动效不该在物品消失后保留，必须把对应"消失态"事件加入 `TownScene.ts` 中的 `removeEffect` 分支：

```typescript
} else if (evt.type === "nature.mushroom_picked"
        || evt.type === "nature.fruit_picked"
        || evt.type === "your_new_consume_event"   // ← 在这里追加
        || evt.type === "world.object_despawned") {
  this.removeEffect(evt.payload.object_id ?? "");
}
```

---

## SOP-A：新增世界物品（WorldObject）

> 用于：在地图上放置一个可点击、可交互的固定物品（桌椅、告示牌、盆栽、自然景点等）。

### A-1 决定物品属性

在动手写代码前，先回答以下问题：

| 问题 | 影响的字段 |
|---|---|
| 物品放在哪个场景？ | `scene_id` |
| 物品的逻辑类型？ | `object_type`（见下表） |
| 物品会随时间变化状态吗？ | `state` 字典的初始 key |
| 玩家/NPC 能对它做什么？ | `available_interactions` 列表 |
| 它是否阻挡角色通行？ | `blocks_movement` |
| 它用哪个 Kenney tile 表示？ | `state.tile_frame`（帧序号，**必填**，见美术资源规范第 2 节） |
| Tiny Town 没有合适 tile？ | 先执行"规则 R3"下载新资源，再填 tile_frame |

**`object_type` 可选值**

| 值 | 语义 |
|---|---|
| `furniture` | 家具（椅子、桌子、书架） |
| `facility` | 设施（咖啡机、收银台） |
| `barrier` | 障碍物（护栏、围墙） |
| `plant` | 植物（树木、花草） |
| `decoration` | 装饰（木桶、告示牌） |
| `item` | 可捡取物品 |
| `nature_spot` | 自然景点（钓鱼点、水坑、蘑菇区） |

> ⚠️ 如需新增 `object_type` 值，必须同时修改 **A-3** 和 **A-4**。

**`available_interactions` 可选动词**（与 `ObjectPanel.tsx` 的 `INTERACTION_LABELS` 对应）

```
fish / pick / sit / rest / rest_under / inspect / read /
smell / water / climb / push / open / jump_over / move / make_coffee
```

> 添加新动词时，同步在 `ObjectPanel.tsx` 的 `INTERACTION_LABELS` 中加入对应中文标签。

---

### A-2 在 `seed.py` 定义物品

**文件**：`backend/app/db/seed.py`

在对应场景的 `objects.append(...)` 区块中添加：

```python
objects.append(WorldObject(
    id=_uid("obj_XXX"),           # 用简短英文 snake_case 命名，_uid 自动加随机后缀
    scene_id=outdoor.id,          # 或 cafe.id / school.id 等
    name="中文名称",
    object_type="plant",          # 从 A-1 表格选择
    position={"x": 20, "y": 15}, # 地图格坐标（从 0 开始）
    size={"width": 1, "height": 1},
    blocks_movement=False,
    available_interactions=["inspect", "smell"],  # 从 A-1 列表选择
    state={
        # ⚠️ 必须指定 tile_frame，不允许仅用 color 作为最终外观
        # 若 Tiny Town tileset 无合适帧，先执行 SOP 第 2 节"规则 R3"下载资源
        "tile_frame": 94,         # Kenney Tiny Town 帧序号（见 A-2 速查表）
        "my_dynamic_key": False,  # 自然事件会修改的 state 字段，初始值写在这里
    },
    tags=["plant", "park"],       # 语义标签，供 NaturalEventHandler 快速筛选
))
```

**`_uid` 命名规则**：同一种物品多实例时加编号，如 `_uid("obj_bench_1")`、`_uid("obj_bench_2")`；或用循环批量生成。

**Kenney Tiny Town 常用 tile_frame 速查**

| tile_frame | 外观 |
|---|---|
| 3 | 黄叶树 |
| 4 / 6 / 8 | 松树 |
| 29 | 蘑菇 |
| 81 | 横向木栅栏 |
| 83 | 告示牌 |
| 92 | 大锅/草垛 |
| 94 | 蜂巢花饰 |
| 107 | 木桶 |

---

### A-3 同步后端类型约束（仅新增 object_type 时）

**文件**：`backend/app/schemas/world.py`

```python
# 在 Literal 中追加新类型
object_type: Literal[
    "furniture", "facility", "barrier", "plant",
    "decoration", "item", "nature_spot",
    "your_new_type",   # ← 新增
]
```

---

### A-4 同步前端类型（仅新增 object_type 时）

**文件**：`frontend/src/types/domain.ts`

```typescript
object_type:
  | "furniture"
  | "facility"
  | "barrier"
  | "plant"
  | "decoration"
  | "item"
  | "nature_spot"
  | "your_new_type"; // ← 新增
```

---

### A-5 前端交互面板标签（仅新增 interaction 动词时）

**文件**：`frontend/src/components/ObjectPanel.tsx`

```typescript
const INTERACTION_LABELS: Record<string, string> = {
  // ... 现有条目 ...
  your_action: "🌟 动作中文名",  // ← 新增
};
```

---

### A-6 重新播种数据库

```bash
cd /Users/mac/virtual-town
# 使用本地端口连接（开发环境标准命令）
DATABASE_URL_SYNC="postgresql+psycopg://virtual_town:virtual_town_dev@localhost:5433/virtual_town" \
  python -m app.db.seed
# 在 backend 目录下执行，需先激活 venv：
# cd backend && source .venv/bin/activate && python -m app.db.seed
```

播种完成后验证：

```bash
curl -s "http://localhost:8000/api/world/objects?scene_id=scene_town_outdoor" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d), 'objects')"
```

---

### A-7 验证前端渲染

1. 刷新浏览器（`localhost:5173`）
2. 在地图上找到新物品的位置并点击
3. 右侧面板应切换为 **ObjectPanel**，显示物品名称、当前状态徽章和交互按钮
4. 点击交互按钮，观察反馈文字

**✅ A 完成标志**

- [ ] 物品在地图上有正确的外观（tile 图块或色块）
- [ ] 点击后右侧面板出现，显示正确名称和可用交互
- [ ] 后端 `/api/world/objects?scene_id=...` 返回该物品
- [ ] 无 Pydantic 校验报错（控制台无 `ValidationError`）

---

## SOP-B：新增自然事件（NaturalEvent）

> 用于：实现一类随时间自动触发、影响世界状态、NPC 可感知并响应的动态事件。

### B-1 设计事件

回答以下问题：

| 问题 | 决定 |
|---|---|
| 事件的触发条件是什么？ | Handler 中的触发逻辑 |
| 它修改哪些 WorldObject 的 state？ | `ctx.objects` 中的 `EngineObject.state` |
| 它影响哪些 NPC 行为？ | `rule_agent.py` / `agent_decision.py` |
| 它在前端有什么视觉效果？ | `TownScene.ts` 的 `addEffect` kind |
| 事件有"开始"和"结束"吗？ | 两个事件类型字符串 |

**事件类型命名约定**

```
world.<动词_名词>          → 全局性事件（world.fire_started）
nature.<名词_动词>         → 自然现象（nature.mushroom_appeared）
weather.<形容词/名词>      → 天气相关（weather.condition_changed）
```

---

### B-2 在 `natural_events.py` 实现 Handler

**文件**：`backend/app/domain/simulation/natural_events.py`

**模板（直接复制修改）**：

```python
class MyNewEventHandler(NaturalEventHandler):
    """
    一句话描述：XXX 事件 —— 触发条件 / 效果 / 消退条件。
    """
    event_category = "my_new_event"   # 用于日志，小写下划线

    def tick(self, ctx: NaturalEventContext) -> list[Any]:
        events: list[Any] = []

        # 1. 筛选关心的对象（用 tag 快速过滤）
        targets = [
            o for o in ctx.objects.values()
            if "my_tag" in o.tags and o.scene_id == ctx.scene_id
        ]

        for obj in targets:
            # 2. 读取对象当前 state
            already_active: bool = obj.state.get("my_state_key", False)

            # 3. 触发逻辑
            if not already_active and ctx.rng.random() < 0.05:  # 5% 概率
                obj.state["my_state_key"] = True
                obj.dirty = True   # ← 必须标记！否则不会写回数据库

                # 4. 防抖：同一 tick 内同一对象不重复触发
                if not ctx.debounce("my_new_event_started", obj.id):
                    events.append(self.make_event(
                        ctx,
                        event_type="my_new_event.started",   # 事件类型字符串
                        source="nature",
                        actor_id=obj.id,
                        description=f"{obj.name} 发生了 XXX",
                        payload={
                            "object_id": obj.id,
                            "x": obj.x,
                            "y": obj.y,
                            "name": obj.name,
                        },
                        importance=3,
                    ))

            # 5. 消退逻辑（可选）
            elif already_active and ctx.rng.random() < 0.1:
                obj.state["my_state_key"] = False
                obj.dirty = True
                if not ctx.debounce("my_new_event_ended", obj.id):
                    events.append(self.make_event(
                        ctx,
                        event_type="my_new_event.ended",
                        source="nature",
                        actor_id=obj.id,
                        description=f"{obj.name} XXX 结束了",
                        payload={"object_id": obj.id, "x": obj.x, "y": obj.y},
                        importance=2,
                    ))

        return events
```

**然后在文件末尾的列表中注册**：

```python
NATURAL_EVENT_HANDLERS: list[NaturalEventHandler] = [
    WeatherEventHandler(),
    FireEventHandler(),
    # ... 现有 handler ...
    FruitRipenEventHandler(),
    MyNewEventHandler(),   # ← 追加在末尾
]
```

**`NaturalEventContext` 常用属性速查**

| 属性 | 类型 | 用途 |
|---|---|---|
| `ctx.objects` | `dict[str, EngineObject]` | 全量内存物品（跨场景） |
| `ctx.agents` | `dict[str, EngineAgent]` | 全量内存 NPC |
| `ctx.scene_id` | `str` | 当前 tick 的主场景 ID |
| `ctx.weather` | `WeatherState` | 当前天气（.condition / .intensity） |
| `ctx.world_time` | `datetime` | 世界时间 |
| `ctx.rng` | `random.Random` | 随机数生成器 |
| `obj.dirty = True` | 必须设置 | 标记 state 需回写 DB |
| `ctx.debounce(key, id)` | `bool` | 返回 True 表示已在本 tick 触发过，跳过 |

---

### B-3 NPC 规则响应（可选，仅影响强制行为）

> 仅当事件需要**强制改变 NPC 行为**（如暴风雨强制回家）时才修改此文件。  
> 普通的"NPC 注意到了"用 **B-4** 的 LLM 感知即可。

**文件**：`backend/app/domain/simulation/rule_agent.py`

```python
def decide_human_action(agent, location, ctx, natural_ctx=None):
    # 在现有优先级判断前插入新规则
    result = _decide_my_new_event_response(agent, natural_ctx)
    if result:
        return result
    # ... 现有逻辑 ...

def _decide_my_new_event_response(agent, natural_ctx):
    """当 XXX 发生时，性格 YYY 的 NPC 会做 ZZZ。"""
    if not natural_ctx or not natural_ctx.some_condition:
        return None
    # 返回 AgentAction 或 None
    return None
```

---

### B-4 LLM 感知上下文（可选，让 LLM 做决策时"看到"事件）

**文件**：`backend/app/llm/agent_decision.py`

在 `_append_natural_context` 函数中追加描述：

```python
def _append_natural_context(lines: list[str], engine, agent_id: str) -> None:
    # ... 现有代码 ...

    # 新事件感知描述
    my_objects = [
        o for o in engine._objects.values()
        if o.state.get("my_state_key") and o.scene_id == agent_scene
    ]
    if my_objects:
        names = "、".join(o.name for o in my_objects[:3])
        lines.append(f"附近的 {names} 正在发生 XXX，你是否关注？")
```

---

### B-5 前端事件类型注册

**文件**：`frontend/src/game/eventBus.ts`

在 `GameEvent` union 中追加新类型：

```typescript
export type GameEvent =
  // ... 现有类型 ...
  | { type: "my_new_event.started"; payload: NaturalEffectPayload }
  | { type: "my_new_event.ended";   payload: NaturalEffectPayload };
```

---

### B-6 前端事件转发注册

**文件**：`frontend/src/pages/TownPage.tsx`

在文件顶部的 `_NATURAL_EVENT_TYPES` Set 中追加：

```typescript
const _NATURAL_EVENT_TYPES = new Set([
  // ... 现有类型 ...
  "my_new_event.started",
  "my_new_event.ended",
]);
```

---

### B-7 前端视觉效果

**文件**：`frontend/src/game/scenes/TownScene.ts`

**步骤一**：在 `create()` 的 `eventBus.on` 回调中添加监听：

```typescript
} else if (evt.type === "my_new_event.started") {
  this.addEffect("my_effect_kind", evt.payload);
} else if (evt.type === "my_new_event.ended") {
  this.removeEffect(evt.payload.object_id ?? "");
}
```

**步骤二**：在 `addEffect` 方法中添加渲染逻辑（新增 `else if` 分支）：

```typescript
} else if (kind === "my_effect_kind") {
  const gfx = this.add.graphics();
  // --- 程序化绘制效果 ---
  gfx.fillStyle(0xAABBCC, 0.8);
  gfx.fillCircle(cx, cy, r);
  this.effectLayer.add(gfx);

  // 可选：添加循环 tween 动画
  const tween = this.tweens.addCounter({
    from: 0,
    to: 100,
    duration: 1000,
    yoyo: true,
    repeat: -1,
    onUpdate: (t) => {
      const v = (t?.getValue() ?? 50) / 100;
      gfx.clear();
      gfx.fillStyle(0xAABBCC, v * 0.8);
      gfx.fillCircle(cx, cy, r * v);
    },
  });

  this.effectNodes.set(id, { kind, gfx, tween });
}
```

**`addEffect` 坐标约定**

```typescript
// p 是 NaturalEffectPayload
const id = p.object_id ?? `${kind}_${p.x}_${p.y}`;
const cx = (p.x ?? 0) * DISPLAY_TILE + DISPLAY_TILE / 2;  // 格子中心 x
const cy = (p.y ?? 0) * DISPLAY_TILE + DISPLAY_TILE / 2;  // 格子中心 y
const r  = DISPLAY_TILE / 2 - 2;                           // 半径留 2px 边距
```

---

### B-8 ObjectPanel 状态徽章（可选）

若事件会改变物品的某个 state 字段，需在 ObjectPanel 中展示：

**文件**：`frontend/src/components/ObjectPanel.tsx`

在 `buildStateBadges` 函数中追加：

```typescript
if ("my_state_key" in state)
  badges.push({
    label: state.my_state_key ? "🌟 XXX 活跃中" : "💤 XXX 平静",
    active: !!state.my_state_key,
    activeColor: "#3a2a6a",
    inactiveColor: "#2a3040",
  });
```

---

### B-9 验证

```bash
# 检查后端事件是否产生（观察 simulation.delta 的 events 数组）
# 在浏览器控制台临时打印：
# simulationSocket.subscribe((type, p) => { if(type==='simulation.delta') console.log(p.events) })
```

**✅ B 完成标志**

- [ ] 后端 `natural_events.py` 的 Handler 能正常 tick（日志无报错）
- [ ] `simulation.delta.events` 数组中出现新事件类型的记录
- [ ] 前端 `TownScene.ts` 的 effectLayer 出现对应视觉效果
- [ ] ObjectPanel 的状态徽章随物品 state 变化正确更新
- [ ] NPC 的决策日志（observability 面板）中出现对事件的感知描述
- [ ] **若事件改变 obj.state**：handler 内的 `_evt(...)` 必须传 `obj=obj`
- [ ] **若事件让物品消失**：必须用 `engine.despawn_object()` 而不是只改 state
- [ ] **若事件生成新物品**：必须用 `engine.spawn_object()` 而不是直接 INSERT

---

## SOP-C：新增交互动词与运行时效果

> 用于：让玩家或 NPC 通过交互动词（pick / fish / water / chop / harvest...）改变世界状态。

### C-1 设计动词的副作用

回答以下问题：

| 问题 | 决定 |
|---|---|
| 该动词修改物品的哪些 state 字段？ | `apply_object_state_change` 的 patch |
| 该动词是否让物品消失？ | 用 `despawn_object` 而不是 patch |
| 该动词是否生成新物品（如砍树掉木头）？ | 用 `spawn_object` |
| 该动词是否影响玩家/NPC 状态（饱腹、能量）？ | 修改 EngineAgent.energy/hunger |
| 玩家成功/失败时显示什么文案？ | 返回的 message 字符串 |

---

### C-2 实现交互效果

**文件**：`backend/app/services/player_service.py::_apply_interaction_effect`

```python
def _apply_interaction_effect(
    self, engine, eng_obj, action, player_eng, obj_state, obj_name,
) -> tuple[str, str | None]:
    # ... 已有动词分支 ...

    # 新增：砍树
    if action == "chop" and obj_state.get("tile_frame") in (3, 4, 6, 8):
        # 1) 把树从世界中移除
        engine.despawn_object(
            eng_obj.id,
            actor=player_eng.id,
            description=f"{player_eng.name} 砍倒了 {obj_name}",
        )
        # 2) 在原地生成一块木头
        engine.spawn_object(
            scene_id=eng_obj.scene_id,
            name="掉落的木头",
            object_type="item",
            x=eng_obj.x,
            y=eng_obj.y,
            available_interactions=["pick", "inspect"],
            state={"tile_frame": 25, "carryable": True},
            tags=["wood", "drop"],
            actor=player_eng.id,
        )
        return f"你砍倒了 {obj_name}，得到一块木头", "wood"

    return f"你对「{obj_name}」执行了「{action}」", None
```

---

### C-3 注册动词到前端 UI

**文件**：`frontend/src/components/ObjectPanel.tsx`

```typescript
const INTERACTION_LABELS: Record<string, string> = {
  // ... 现有 ...
  chop: "🪓 砍伐",   // ← 新增
};
```

---

### C-4 在 seed.py 给相关物品加上该动词

```python
state={"tile_frame": 4, "flammable": True, "burn_max_ticks": 12, "wood_yield": 1},
available_interactions=["chop", "inspect", "climb"],   # ← 加 chop
tags=["tree", "flammable", "choppable"],
```

---

**✅ C 完成标志**

- [ ] `_apply_interaction_effect` 中实现了该动词的全部副作用
- [ ] 物品 state 改变 / 消失 / 生成 全部走 engine 三件套 API
- [ ] `INTERACTION_LABELS` 中有该动词的中文标签
- [ ] `seed.py` 中相关物品 `available_interactions` 已包含该动词
- [ ] 浏览器验证：点击交互按钮 → 物品的视觉表现立刻变化（不需要刷新页面）

---

## 两者联动：带自然事件的新物品

若新物品**既需要出现在地图上，又会参与自然事件，并且能被交互**，按如下顺序执行：

```
A-1 → A-2 → [A-3] → [A-4]              ← 数据建模
  → B-1 → B-2 → [B-3] → [B-4]          ← 事件逻辑（务必 obj=obj）
  → C-1 → C-2 → C-3 → C-4              ← 交互副作用（务必走 engine 三件套）
  → B-5 → B-6 → B-7 → B-8              ← 前端事件管道
  → A-5 → A-6 → A-7                    ← 美术接入与播种验证
```

核心要点：
- 物品 `state` 字典中**必须预留**事件会修改的字段（初始值）
- 物品 `tags` 列表**必须包含**事件 Handler 用于筛选的 tag
- 任何"会让物品消失/出现"的逻辑必须走 `engine.despawn_object` / `engine.spawn_object`
- 任何"会改变物品 state"的事件，handler 中的 `_evt(...)` 必须传入 `obj=obj`
- 先定义好数据（seed），再实现事件逻辑，最后连接前端

---

## 常见错误与排查

| 错误现象 | 原因 | 解决 |
|---|---|---|
| `ValidationError: object_type` | 新 object_type 未加入 Pydantic schema | 执行 A-3 和 A-4 |
| 物品不显示 | scene_id 错误（如写了 `"outdoor"` 而非 `"scene_town_outdoor"`） | 查询 `SELECT id FROM map_scenes` |
| 点击物品无反应 | `player.click_object` 未正确绑定，或 `objectsByScene` 未加载当前场景 | 检查 TownPage useEffect 中 `store.setSceneObjects` 是否被调用 |
| 右侧面板不切换 | `store.selectObject` 未被调用，或 `selectedObjectId` 未在 store 初始化 | 确认 worldStore 中有 `selectedObjectId: null` 初始值 |
| Failed to fetch | 后端 list_objects 内部报 500 | 查看后端终端日志，常见原因是 Pydantic ValidationError（A-3） |
| 视觉效果不出现 | 事件类型字符串未加入 `_NATURAL_EVENT_TYPES` | 执行 B-6 |
| 视觉效果出现但位置错 | payload 中 x/y 单位是格坐标，`cx = x * DISPLAY_TILE + DISPLAY_TILE / 2` | 检查 B-7 中坐标计算 |
| Handler 报错但不中断仿真 | `tick_all` 吞掉了 exception | 查看后端日志中 `natural event handler XXX crashed` |
| 交互按钮一直 disabled / 反馈一直显示 | 切换物品时旧状态未重置 | `ObjectPanel` 已内置 `useEffect` 在 `objectId` 变化时清空；如仍出现，确认 `objectId` prop 正确传入 |
| 所有物品都显示上一个物品的交互结果 | 同上（React 组件状态未随 key 重置） | 无需特殊处理，`useEffect([objectId])` 已修复；若重现，给 `ObjectPanel` 加 `key={objectId}` prop 强制重挂 |
| 物品外观是纯色块 | 缺少 tile_frame，或 tileset 未加载 | 执行规则 R1/R3；检查 `TownScene.preload()` 中是否加载了该 tileset |
| **采摘后蘑菇/果实没消失** | `_interact_object` 只写日志，没改 state | 在 `_apply_interaction_effect` 用 `engine.apply_object_state_change(...)`；事件类型加入 `_OBJECT_LIFECYCLE_TYPES` |
| **天气改变了但没视觉效果** | (1) intensity=0 时 alpha=0；(2) 客户端连接前已发生过转换 | (1) `applyWeatherOverlay` 已内置 baseAlpha 兜底；(2) 引擎 `start()` 会立刻广播一次当前天气 |
| **自然事件中物品状态变了但前端不刷新** | `_evt(...)` 没传 `obj=obj`，payload 缺 state | 把所有 state-mutating 事件改为 `_evt(... obj=obj)`，前端会自动重画 objectLayer |
| **运行时 spawn 的物品刷新页面后消失** | spawn 没设置 `pending_create=True` 或没走 `_persist_tick` | 必须用 `engine.spawn_object(...)`，不要直接 `self._objects[id] = ...` |
| **despawn 后效果层（火焰/萤火虫）残留** | TownScene 没监听对应 despawned 事件类型 | 在 `eventBus.on` 中把新事件类型加入 `removeEffect` 分支；`world.object_despawned` 已默认覆盖 |
| NPC 未响应事件 | LLM context 未注入（B-4 未实现）或规则优先级太低（B-3） | 检查 `_append_natural_context` 的描述是否出现在 LLM prompt 中 |
| 数据库未更新 | 忘记设置 `obj.dirty = True` | 在修改 state 的下一行加 `obj.dirty = True` |

---

## 快速检查清单

### 新增物品（SOP-A）

```
□ 美术资源确认：物品有 Kenney tile_frame？若无，先执行"规则 R3"下载资源
□ seed.py：object_type / position / available_interactions / state.tile_frame / tags
□ 若新增 object_type → schemas/world.py Literal + domain.ts union
□ 若新增 interaction 动词 → ObjectPanel.tsx INTERACTION_LABELS
□ 重新播种：python -m app.db.seed（在 backend/ 目录，已激活 venv）
□ curl 验证：/api/world/objects?scene_id=... 返回新物品
□ 浏览器验证：物品在地图上显示真实 tile 外观（非纯色块）
□ 浏览器验证：点击物品，右侧面板显示正确内容和交互按钮
□ 浏览器验证：交互按钮点击后反馈正常，4 秒后自动消失，再次点击按钮可用
```

### 新增自然事件（SOP-B）

```
□ 美术资源确认：静态物品本体已有 tile_frame；动态效果（火焰/萤火虫等）允许程序化
□ natural_events.py：实现 Handler.tick，设置 dirty，用 debounce 防抖，注册到列表末尾
□ natural_events.py：所有改 obj.state 的 _evt(...) 调用都传了 obj=obj
□ natural_events.py：让物品消失用 engine.despawn_object，生成新物品用 engine.spawn_object
□ eventBus.ts：追加新事件类型到 GameEvent union
□ TownPage.tsx：追加类型到 _NATURAL_EVENT_TYPES Set
□ TownPage.tsx：若事件改变 state，同时追加到 _OBJECT_LIFECYCLE_TYPES Set
□ TownScene.ts：eventBus.on 中监听 → addEffect / removeEffect
□ TownScene.ts：addEffect 中实现对应 kind 的渲染分支（动态效果可程序化）
□ TownScene.ts：消失类事件加入 removeEffect 分支
□ ObjectPanel.tsx：buildStateBadges 中追加新 state 字段的徽章（若有）
□ （可选）rule_agent.py：NPC 强制响应逻辑
□ （可选）agent_decision.py：LLM 感知描述
□ 浏览器验证：等待事件触发，观察视觉效果和事件日志
□ 浏览器验证：事件开始/结束时效果层正确出现/消失，不残留
□ 浏览器验证：物品 tile / 状态徽章 在事件发生瞬间立即更新（无需刷新页面）
```

### 新增交互动词（SOP-C）

```
□ player_service.py::_apply_interaction_effect：定义动词的 state 副作用
□ 改 state 用 engine.apply_object_state_change（自动广播 world.object_state_changed）
□ 让物品消失用 engine.despawn_object
□ 生成新物品用 engine.spawn_object
□ ObjectPanel.tsx：INTERACTION_LABELS 加入中文标签
□ seed.py：相关物品的 available_interactions 加入该动词
□ 浏览器验证：点击交互按钮 → 物品视觉立刻变化（如蘑菇消失、花朵开放、树倒下）
□ 浏览器验证：操作反馈在右侧面板正确显示，4 秒后消失
```
