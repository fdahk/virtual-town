# 阶段 21 hotfix — 新游戏卡 75% & worker 持续刷屏 — 根因分析与修复记录

**日期**：2026-05-02
**发现方式**：用户在前端点「新游戏」后进度条停在 75%，刷新无效；后端 RQ worker 终端每秒数十条相同栈不停刷屏，控制台几乎无法阅读。
**严重程度**：P0（功能阻塞 —— 22 NPC 新世界完全无法进入）
**关联阶段**：阶段 20（记忆系统重构）→ 阶段 21（新游戏向导 + 世界生成器 + 22 NPC 模板）

---

## 一、问题表象（症状链）

用户先后报告了三波看似不同的现象，实际是 4 个独立 bug 串联：

| 序号 | 用户看到的 | 实际触发处 |
|---|---|---|
| 1 | `Failed to process file: spritesheet "human_npc_xiaodi"`（共 12 条） | 浏览器 console / Phaser preload |
| 2 | RQ worker 每秒数十条 `UnboundLocalError: 'waited_secs'` + `TypeError: 'error_code' is an invalid keyword argument for TaskStatusLog` | 后端 `dev.sh` 终端 |
| 3 | 进度条卡 75%，前端不能进入 TownPage | 浏览器 Network 标签：`/api/world/objects?scene_id=scene_town_outdoor → 500` |

---

## 二、根因分析

### Bug A — sprite sheet PNG 缺失

**根因**：阶段 21 把人类 NPC 从 7 扩到 18 时，三处都改了：
`compose_lpc.py` 的 `NPC_LAYERS`、`fetch_assets.sh` 的 `LPC_LAYERS`、
`sprites.manifest.json`。但**最关键的执行 `bash scripts/fetch_assets.sh`
把缺失分层下载下来 + 调 `compose_lpc.py` 合成新 PNG 这一步没跑过**，
所以 `frontend/public/assets/sprites/humans/` 里只有最初 7 张图，新 12
张 manifest 引用全部 404，Phaser 把它当 spritesheet load 失败。

**修复**：

1. 跑 `bash scripts/fetch_assets.sh`（自动 fetch 缺失分层 + compose），
   在 `frontend/public/assets/sprites/humans/` 生成 19 张 sheet
   （18 NPC + `player_default`），每张约 19 KB。
2. 新增 `scripts/compose_portraits.py`：从已合成的 sprite sheet 取
   「down 行 idle 帧」头部 48×44，6 倍最近邻放大成 288×264 像素艺术
   兜底立绘。已存在的高质量 GenerateImage 立绘不会被覆盖。
3. 把 `compose_portraits.py` 接进 `fetch_assets.sh`，以后任何新 NPC
   都自动得到立绘兜底。

### Bug B — `runner.py` deadline 分支缩进错位

**严重程度**：P0（worker 完全无法处理任何任务）

**根因**：`backend/app/domain/tasks/runner.py:_execute_task` 里
deadline-exceeded 分支的 `record_task_status(...)` + early return 写在
`if task_deadline ... > task_deadline:` 块**外面**：

```python
if task_deadline is not None and now_ts > task_deadline:
    waited_secs = (now_ts - task.created_at).total_seconds() ...
    ...
    await _sess.commit()
await get_observer().record_task_status(  # ← 应该在 if 内但缩了出来
    ...
    message=f"deadline exceeded after {waited_secs:.1f}s in queue",
)
return {"status": FAILED, "error": "TASK_DEADLINE_EXCEEDED"}  # ← 同上

handler = registry.get(task.task_type)  # ← 永远不会执行（dead code）
```

后果：

1. **任何**任务都执行那段 early return → handler 调度永远不被命中，
   也就是说 22 NPC 的 `agent_decision` / `npc_dialogue` 都没真正跑过。
2. deadline **未过期**路径下 `waited_secs` 没被赋值 →
   `UnboundLocalError`，每条任务都崩，刷屏。

**修复**：把 lines 213–221（`record_task_status` + return）整体缩 4 空格
进 if 块。

### Bug C — `TaskStatusLog` ORM 缺 `error_code` 列

**严重程度**：P1（可观测性数据丢失，但不阻塞主路径）

**根因**：`Observer.record_task_status` 一直把 `error_code` 字段放进 record
（Redis 摘要 / 事件总线 payload 都用），但 `TaskStatusLog` ORM 没有该列，
每次 flush 抛 `TypeError`，整批审计被丢弃 —— 同批的
`ObservabilityEvent` / `LLMCallRecord` / `ToolCallRecord` 一并丢失。

之所以这个 bug 长期没被发现，是因为 Bug B 修好之前任务都早早 short-circuit
返回，正常的成功/失败路径（不带 `error_code`）走得多，带 `error_code` 的
失败路径只在退避重试或显式 fail 时触发，被 Bug B 的崩溃刷屏掩盖。

**修复**：

- `backend/app/db/models.py::TaskStatusLog` 加
  `error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)`
- 新建 alembic 迁移
  `backend/alembic/versions/20260502_0006_task_status_error_code.py`：
  `op.add_column("task_status_log", sa.Column("error_code", sa.String(64), nullable=True))`

### Bug D — `world_gen` 写入非法 `object_type`

**严重程度**：P0（前端 75% 卡死的真凶）

**根因**：`backend/app/domain/world_gen/outdoor.py` 在生成自然对象时写错了类型字符串：

| 用途 | 错误值 | 数量 | 应改为 |
|---|---|---|---|
| 蘑菇 / 水果 / 钓点 | `'natural'` | 26 | `'nature_spot'` |
| 河岸围栏 | `'fence'` | 115 | `'barrier'` |

`backend/app/schemas/world.py::WorldObject.object_type` 是
`Literal["furniture", "facility", "barrier", "plant", "decoration", "item", "nature_spot"]`，
ORM 入库时不卡（PostgreSQL 列是 `String`），但 `GET /api/world/objects` 返回时
Pydantic 校验失败 → 整个 endpoint 500 → 前端 TownScene preload 等不到对象列表
→ 卡 75%。

**修复**：

1. `world_gen/outdoor.py`：3 处 `'natural'` 改 `'nature_spot'`，1 处 `'fence'` 改 `'barrier'`。
2. `world_gen/apply.py::_object_orm` 加白名单守卫，未来再写错直接在
   生成阶段就 `ValueError`，不让脏数据落库：

   ```python
   _VALID_OBJECT_TYPES = {"furniture", "facility", "barrier", "plant",
                          "decoration", "item", "nature_spot"}
   if obj_type not in _VALID_OBJECT_TYPES:
       raise ValueError(f"world_gen 产生了非法 object_type={obj_type!r} ...")
   ```

3. **修复线上数据**（用户当前世界已落库 141 行垃圾）：

   ```sql
   UPDATE world_objects SET object_type='nature_spot' WHERE object_type='natural';  -- 26 行
   UPDATE world_objects SET object_type='barrier'     WHERE object_type='fence';    -- 115 行
   ```

4. 端到端自检：`generate_world(seed=42)` → 303 个对象，全部通过
   `WorldObjectSchema(...)` 校验。

### Bug E（meta-bug）— `dev.sh` worker 不会随代码自动重启

**严重程度**：P2（开发体验）但**强烈放大了 A–D 的踩坑成本**

**根因**：`scripts/dev.sh` 启动 backend 用 `uvicorn --reload` 自带 watchfiles
监视，改 `runner.py` 自动重启；但 RQ worker 启动用的是 `python -m
app.domain.tasks.worker`，**没有任何 file-watcher**。后果：开发者修代码、
保存、刷新浏览器后，backend 是新代码、worker 是旧代码，行号一致让人误以为
修复没生效（这次正好踩到 line 219 在新旧代码都是同一行 `message=f"..."`）。

**修复**：用 `watchfiles` CLI（uvicorn[standard] 的传递依赖，无需新增 pip 包）
包一层 worker 进程：

```bash
if [ "${APP_ENV:-dev}" = "dev" ] && python -c "import watchfiles" >/dev/null 2>&1; then
  exec watchfiles \
    --filter python --target-type command \
    --sigint-timeout 5 --grace-period 0 \
    "python -m app.domain.tasks.worker" \
    app
else
  exec python -m app.domain.tasks.worker
fi
```

效果：`touch backend/app/domain/tasks/runner.py` → watchfiles 立刻 SIGTERM
旧 worker，重启新 worker（PID 变化），与 uvicorn 行为对齐。
经验证生效（旧 worker 54737 → 新 worker 55014，3 秒内完成）。

---

## 三、修改文件清单

| 文件 | 改动 | 关联 Bug |
|---|---|---|
| `frontend/public/assets/sprites/humans/{12 张}.png` | 生成 | A |
| `frontend/public/assets/portraits/humans/{12 张}.png` | 生成 | A |
| `scripts/compose_portraits.py` | 新增 | A |
| `scripts/fetch_assets.sh` | 接入 compose_portraits | A |
| `backend/app/domain/tasks/runner.py` | 缩进 4 空格 | B |
| `backend/app/db/models.py::TaskStatusLog` | 加 `error_code` 列 | C |
| `backend/alembic/versions/20260502_0006_task_status_error_code.py` | 新增迁移 | C |
| `backend/tests/test_tasks_and_observer.py` | 加回归用例 `test_task_deadline_exceeded_records_error_code` | B+C |
| `backend/app/domain/world_gen/outdoor.py` | `'natural'` → `'nature_spot'`、`'fence'` → `'barrier'` | D |
| `backend/app/domain/world_gen/apply.py::_object_orm` | 白名单守卫 | D |
| `world_objects` 表 (DB) | UPDATE 26+115 行 | D |
| `scripts/dev.sh` | worker 接 watchfiles 自动重载 | E |
| `docs/开发手册/SOP/新游戏初始化与存档SOP.md` | 加新增 NPC 速查 + 立绘说明 | A |
| `docs/开发手册/SOP/本地开发热重载与进程重启SOP.md` | 新增 | E |
| `docs/开发手册/prompts/开发记录.md` §22 | 加 hotfix 记录 | B+C+D+E |

---

## 四、验证

```bash
# 1) 数据库迁移到位
cd backend && alembic current
# → 20260502_0006 (head)

# 2) world_gen 产物 100% 合规
cd backend && python -c "
from app.domain.world_gen import GenerationConfig, generate_world
from app.domain.world_gen.apply import _VALID_OBJECT_TYPES
from app.schemas.world import WorldObject
plan = generate_world(GenerationConfig.default(seed=42))
for row in plan.world_objects:
    assert row['object_type'] in _VALID_OBJECT_TYPES
    WorldObject(**{k: row.get(k) for k in (
        'id','scene_id','name','object_type','position','size',
        'blocks_movement','available_interactions','state','tags')})
print(f'ok: {len(plan.world_objects)} objects')
"
# → ok: 303 objects

# 3) /api/world/objects 不再 500
curl -s -o /dev/null -w '%{http_code}\n' \
    'http://localhost:8000/api/world/objects?scene_id=scene_town_outdoor'
# → 200

# 4) worker 不再刷屏
grep -c UnboundLocalError /tmp/vt_dev.log     # → 0
grep -c 'invalid keyword' /tmp/vt_dev.log     # → 0

# 5) 前端 19 张人物 sprite 全部就位
ls frontend/public/assets/sprites/humans/ | wc -l   # → 19

# 6) worker 热重载验证
touch backend/app/domain/tasks/runner.py
ps -eo pid,command | grep "app.domain.tasks.worker" | grep -v grep
# → PID 在 3 秒内变化；watchfiles 父进程不变
```

---

## 五、教训沉淀

### 1. 改了 manifest / 注册表，必须手动跑一次产物生成脚本

阶段 21 改了 sprites/portraits manifest 但漏跑 `fetch_assets.sh`，
manifest 与磁盘 PNG 不一致。**SOP 已加「新增一个默认 NPC」三步速查**，
明确把跑脚本作为强制步骤。

### 2. RQ worker 不会随 `uvicorn --reload` 一起重载

这是踩坑放大器：行号在新旧代码同位置时，误判「修复没生效」反而打补丁
打错地方。**dev.sh 已加 watchfiles 包装**，并新建
`docs/开发手册/SOP/本地开发热重载与进程重启SOP.md`，把开发期所有"修了
代码但没生效"的排查流程归档。

### 3. ORM 弱类型 + Pydantic 强类型的双层验证缝隙

`WorldObject.object_type` 在 ORM 是 `String`，Pydantic 是 `Literal`。
落库时不报错，读库时整页 500。**`apply.py` 已加生成阶段白名单守卫**，
确保非法值在数据离开内存前就被拒绝；同时回归测试用 `Schema(**row)`
对每个生成对象做端到端校验，避免再有第三种类型混进来。

### 4. 可观测性字段必须先有列再用

`Observer.record_task_status` 调用方跨 Redis / 事件总线 / DB 三处共享
record dict，新加字段时容易顾此失彼（Redis 用了、ORM 没有）。已通过加列
+ 回归测试解决。后续若有新字段，按顺序：
ORM → alembic → Pydantic schema → Observer record 字段。

### 5. 缩进 bug 极其难看出，需要静态分析+回归测试双保险

Bug B 的缩进错误在 code review 时几乎不可能发现，因为 8 空格 vs 4 空格
在视觉上差别小。但是：

- 任意一个测试覆盖**正常路径** + **deadline 路径** 就能立刻爆出来；
- 任何 linter 配 `unused-variable` / `unreachable-code` 也能报警
  （line 223 起的 handler 调度是 dead code）。

**已加** `test_task_deadline_exceeded_records_error_code` 回归用例覆盖
deadline 路径，并验证 `error_code` 列写入，一举防范 Bug B + C 复发。
