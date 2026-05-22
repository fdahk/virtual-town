# 新游戏初始化与存档 SOP

> 阶段 21 引入：从硬编码 `seed.py` 迁移到「模板 + 生成器 + 前端启动页」三层架构。
> 任何"新增 NPC、新增建筑、改地图布局"的需求，都先看这份文档判断该改哪层。

## 1. 总览三层

```
┌──────────────────────────────────────────────────────────────────────┐
│  StartPage (前端)                                                    │
│    新游戏 → NewGameWizard → POST /api/games/new                      │
│    读档   → <input type="file"> → POST /api/games/load               │
│    继续   → 当前 simulation.status === running 才可见                │
│  TownPage (顶栏)                                                     │
│    存档   → SaveGameButton → GET /api/games/save → Blob 下载         │
└──────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  game_service.create_new_game()                                      │
│    ① _coerce_template(): 前端 dict → AgentTemplate                   │
│    ② generate_world(GenerationConfig)                                │
│    ③ apply_plan_async(session, plan)：清空 + 写入                    │
│    ④ runtime.reload() + sim.status="running"                         │
└──────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  app/domain/world_gen/generator.py                                   │
│    - outdoor.py    120×90 三区瓦片 + BuildingPlan/HomePlan           │
│    - interiors.py  16×12 室内 + 各 interior_kind 家具                │
│    - placement.py  NPC → 住宅 / 工作场所 + schedule 占位符替换       │
│    - relationships.py  N×N 双向关系矩阵                              │
│    - apply.py      WorldPlan → DB                                    │
└──────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  app/db/templates/                                                   │
│    - npc_defaults.py        18 人类 + 4 动物 + 默认玩家              │
│    - art_catalog.py         LPC 层目录 + 职业预设 + schedule 模板    │
│    - relationship_seeds.py  显式强关系 + 主人/宠物对                 │
└──────────────────────────────────────────────────────────────────────┘
```

## 2. 常见任务 → 修改位置

| 想做什么 | 改哪个文件 | 备注 |
|---|---|---|
| **新增一个默认 NPC** | `app/db/templates/npc_defaults.py` 加一项 | 同时确认 `art.layers` 在 `LPC_LAYER_CATALOG` 中存在 |
| **给 NPC 加预设关系** | `app/db/templates/relationship_seeds.py` 加 `(from, to, fam, trust, aff, fear, summary)` | 同时双向，生成器会自动写两条 |
| **新增一个 LPC 美术层** | (1) `scripts/fetch_assets.sh` + (2) `scripts/compose_lpc.py` + (3) `art_catalog.py` 三处 | 任一缺失编辑器就拉不到该选项 |
| **新增一个默认 NPC** | (1) `compose_lpc.py` 的 `NPC_LAYERS` 加条 + (2) `sprites.manifest.json` / `portraits.manifest.json` 注册 sheet_id → 文件路径 + (3) 跑一次 `bash scripts/fetch_assets.sh`（自动合成 sprite + 兜底裁脸立绘） | 若漏了 (3)，前端会报 `Failed to process file: spritesheet "..."` |
| **新增一种公共建筑** | `app/domain/world_gen/outdoor.py` 的 `BUILDING_LAYOUT` 加一项 + `interiors.py` 的 `PUBLIC_INTERIOR_FURNITURE` 加家具模板 + `tilesets.manifest.json` 加 `_ext` / `_door` terrain 映射 + `TownScene.ts` 的 `FALLBACK_TERRAIN_COLORS` 加兜底色 |
| **改地图尺寸 / 区域分布** | `app/domain/world_gen/outdoor.py` 的 `build_outdoor_tiles`，调整道路 / 河流 / 区域边界 |
| **新增/修改职业 schedule** | `art_catalog.py` 的 `OCCUPATION_PRESETS` + `SCHEDULE_TEMPLATES` |
| **加新需求字段（仅影响 NPC 配置）** | (1) `AgentTemplate` 加字段 + (2) `placement.make_agent_row` 透传 + (3) `NPCDraft`（前端）加字段 + (4) `NPCEditor` 加表单项 |

## 3. 命令速查

### 本地起服（按 SOP 走）

```bash
./scripts/dev.sh         # 起 postgres / redis / 后端 / RQ worker / 前端
                         # 第一次会跑 seed --if-empty（默认 22 NPC）
```

启动后浏览器访问 `http://localhost:5173`，进入 StartPage：
- 点「开始新游戏」 → 三步向导 → 创建 22 NPC 世界
- 点「继续当前世界」 → （只有上次运行过的 simulation 才可见）

### 强制重置 DB 为默认 22 NPC

```bash
cd backend
python -m app.db.seed --seed 42      # 强制重建（覆盖现有数据）
```

### 校验生成器

```bash
cd backend
python -c "
from app.domain.world_gen import GenerationConfig, generate_world
plan = generate_world(GenerationConfig.default(seed=42))
print(f'scenes={len(plan.scenes)} agents={len(plan.agents)} '
      f'objs={len(plan.world_objects)} rels={len(plan.relationships)}')
"
```

预期输出：
```
scenes=23 agents=23 objs=303 rels=358
```

## 4. 数据契约

### NPC 模板（`AgentTemplate` / `NPCDraft`）

```python
@dataclass
class AgentTemplate:
    id: str                      # npc_xxx / animal_xxx / player_xxx
    name: str
    entity_type: "human" | "animal" | "player"
    age: int | None
    gender: "male" | "female" | None
    species: str | None          # 动物必填
    occupation: str | None
    personality: list[str]
    background: str
    lifestyle: str | None
    long_term_goals: list[str]
    schedule_id: str | None      # 引用 SCHEDULE_TEMPLATES 的 key

    art: dict                    # 见下
    accent_color: str            # #rrggbb
    portrait_url: str | None

    has_home: bool               # False = 住公共建筑楼上 / 与人同住
    upstairs_of: str | None      # 公共建筑 key（bakery/tavern/cafe）
    cohabits_with: str | None    # 与某 NPC 同住

    preferred_district: "north" | "center" | "south"
    owner_id: str | None         # 动物的主人 NPC ID
```

`art` 的两种形态：
- 人类：`{"kind": "lpc_human", "sheet_id": "<id>", "layers": {body, head, hair, torso, legs, feet}}`
- 动物：`{"kind": "lpc_animal", "species": "cat"|"dog", "color": "white"|"brown"|...}`

### Schedule 占位符

`SCHEDULE_TEMPLATES` 中允许的 location_id 占位符：
- `<HOME>`：NPC 实际住宅室内 location_id
- `<WORKPLACE>`：职业对应的工作地点
- `<PARK>` / `<RIVER>` / `<CAFE>`：公共锚点

由 `art_catalog.resolve_schedule()` 在 `placement` 阶段替换。

## 5. 存档 / 读档（阶段 21 落地）

### 5.1 存档（导出 JSON 快照）

- **入口**：TownPage 顶栏「存档」按钮（`SaveGameButton.tsx`）。
- **后端**：`GET /api/games/save` → `game_service.export_save()`。
- **覆盖**：simulation 元 / scenes / tiles / locations / portals / world_objects /
  agents（含 state）/ relationships / memories（**不含 embedding**）。
- **不覆盖**：`world_events` / `agent_actions` / `dialogue_messages` /
  `observability_events` / `tasks` / `task_status_log`（流式叙事 + 审计 +
  任务队列状态都是会话级数据，重启后从新世界出发即可）。
- **完整性**：payload 末尾带 `checksum`（SHA-256，计算时排除自己）+
  `counts`（每张表行数）+ `saved_at`（UTC ISO8601）。
- **文件名**：`vt-save-YYYY-MM-DD-stepN.json`，由前端 `SaveGameButton` 拼接。

### 5.2 读档（导入 JSON 快照）

- **入口**：StartPage「读取存档（上传 JSON）」→ 隐藏的 `<input type="file">`。
- **后端**：`POST /api/games/load` → `game_service.import_save()`。
- **流程**：
  1. `_validate_save(payload)`：检查 `version` 主版本号 + 必需字段
     （`scenes` / `tiles` / `locations` / 非空 `agents`），不通过返回 400 +
     中文 `detail`。
  2. 复用 `world_gen.apply.CLEAR_TABLES` 按 FK 倒序清表。
  3. 按 FK 顺序写入：scenes → tiles → locations → portals → world_objects
     → agents → agent_states → relationships → memories → simulation。
  4. `simulation_runtime.reload()` 让引擎从 DB 重读世界；status 取 payload
     里的值，默认 `paused`（避免一进入就开始烧 LLM token）。
- **memory.embedding 重建**：导入时故意置 NULL；后续 `write_memory_embedding`
  任务批量补算。期间记忆检索会退化到关键词匹配，是已知的可接受过渡态。

### 5.3 版本号 & 兼容策略

`SAVE_FORMAT_VERSION = "1.1"`（写在 `game_service.py`）。

| 改动类型 | bump 哪一位 | 旧 save 是否能被新服务接受？ |
|----------|------------|------------------------------|
| 新增字段（默认值兜底） | 次版本号（1.0 → 1.1） | ✓ 可接受，缺失字段走默认值 |
| 字段类型变更 / 删除字段 / 重命名 | 主版本号（1.x → 2.x） | ✗ 直接 400，需要写迁移脚本 |

`GET /api/games/save/format-version` 让前端在导入前主动对比版本号，
避免上传完整 2.8MB JSON 后才发现不兼容。

### 5.4 测试

`backend/tests/test_save_load_roundtrip.py` 覆盖：
- 纯校验（不依赖 DB）：版本号缺失 / 主版本号不兼容 / agents 为空 / 次版本号差异
- 集成（需要 `TEST_DATABASE_URL`）：export → import → export 两次 counts 等值；
  导入后 memory.embedding 全部 NULL（验证 backfill 契约）

## 6. 已知限制 & 待办

- **多存档并存**：当前是「单世界 + 多 JSON 快照」模式，读档会清空当前世界。
  如需多人共享/并存多个世界，需要在 DB 加 `world_id` 维度（破坏性改动，下个阶段再说）。
- **实时 LPC 预览**：`NPCEditor` 当前用 `accent_color` + 层 ID 文本提示替代真实合成图。
  完整方案是新增 `POST /api/games/preview-sprite` → 调 `compose_lpc.py` → 返回 PNG dataURL。
- **美术资源扩展**：当前只有 LPC + Tiny Town/Dungeon。如需更多农场/森林贴图，
  在 `scripts/fetch_assets.sh` 加 Kenney Tiny Farm / Tiny Forest CC0 包，
  并在 `tilesets.manifest.json` 注册新 tileset。
- **对话立绘**：项目内置两种立绘——
  (1) 主线 NPC 用 `GenerateImage` 出的高质量插画（1536×1024, ~2.5MB）；
  (2) 新加 NPC 跑 `scripts/compose_portraits.py` 自动从 sprite 头部裁切并放大成
  288×264 像素艺术兜底立绘。`fetch_assets.sh` 会跳过已存在文件，确保不会覆盖
  高质量版本；要替换只需把同名 PNG 放进 `frontend/public/assets/portraits/humans/`。
