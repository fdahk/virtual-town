# 第三方资源与授权声明

本项目使用以下外部视觉资源。所有资源均符合其原始授权条款并在此公开署名。
如有遗漏或错误，请 issue 指出。

---

## 1. 瓦片、建筑、家具、基础环境资源

### Kenney · Tiny Town（以及按需补充的其他 Kenney 包）

- 作者：Kenney (<https://kenney.nl>)
- 授权：**CC0 1.0 Universal (Public Domain Dedication)**
- 来源：<https://kenney.nl/assets/tiny-town>
- 本项目引用位置：`frontend/public/assets/tilesets/kenney_tiny_town/`
- 引用说明：Kenney 的 CC0 授权不强制署名，但本项目出于尊重仍在此标注。

如果后续追加 Kenney 其他包（Roguelike / RPG / Pixel Platformer 等），请在此追加条目并维持 CC0 说明。

---

## 2. 人物 / 动物 walk 动画资源

### Universal LPC Spritesheet Generator

- 项目：Universal LPC Spritesheet Generator (<https://sanderfrenken.github.io/Universal-LPC-Spritesheet-Character-Generator/>)
- 仓库：<https://github.com/sanderfrenken/Universal-LPC-Spritesheet-Character-Generator>
- 授权：**CC-BY-SA 3.0 / GPL 3.0**（具体每一层有独立作者，总授权以仓库 README 与每一层 `credits.txt` 为准）
- 本项目引用位置：`frontend/public/assets/sprites/humans/`、`frontend/public/assets/sprites/animals/`
- 作者列表（非详尽，完整列表见上游 `CREDITS.md`）：
  - Stephen Challener (Redshrike)
  - Johannes Sjölund
  - Manuel Riecke (MrBeast)
  - Joe White
  - Lanea Zimmerman (Sharm)
  - Daniel Eddeland
  - Daniel Armstrong (HughSpectrum)
  - Kyran Jackson

- 衍生品说明：本项目对 LPC 素材的**分层合成结果**（位于 `frontend/public/assets/sprites/`）遵循 **CC-BY-SA 3.0** 同协议传递。其它业务代码、后端实现、产品文档**不因此受 CC-BY-SA 约束**，项目整体仍保留原授权。
- 引用要求：任何再分发 LPC 衍生 sprite 的行为，必须在分发物中同步包含本文件与上游 credits.txt。

---

## 3. NPC 对话立绘（AI 生成）

- 生成工具：Claude（Cursor Agent 中的 `GenerateImage`），2026-04-30 生成。
- 提示词摘要：见 `docs/实施方案/视觉资源与美术资产方案.md` §4.1.2 的 NPC 映射表。
- 授权：本项目自有资产（AI 基于本项目提示词创作），可随项目分发。
- 位置：`frontend/public/assets/portraits/`。
- 注意：如模型提供方未来对生成物授权政策变化，应在此追加说明。

---

## 4. 资产目录约束

- 任何进入 `frontend/public/assets/` 的文件都必须来源清晰；未登记的文件禁止入库。
- 下载脚本 `scripts/fetch_assets.sh` 是外部资源的唯一来源；prompt 版本记录在 `docs/prompts/记录.md`。
- 任何资源替换都必须更新本文件 + 对应 manifest。

---

## 5. 软件依赖

后端依赖见 `backend/pyproject.toml`，前端依赖见 `frontend/package.json`。
核心开源依赖保持各自许可证，随 npm / PyPI 常规使用。
