"""
Prompt 模板集合（阶段 17：目录结构 + 元数据）。

目录约定（``backend/app/prompts/<id>/``）：

- ``v<N>.jinja2``   — 模板主体（纯文本占位符 `{{name}}`，不依赖真实 Jinja2 包；
  见 ``render_prompt``）。
- ``v<N>.txt``     — 兼容旧格式。与 `.jinja2` 二选一即可。
- ``metadata.json`` — prompt 元数据（id / version / expected_schema /
  supported_models / last_updated / default_model_role）。
- ``examples.json`` — 合法输出 / 异常输出样例，供评测脚本
  ``scripts/eval_prompts.py`` 使用。

兼容：老的 `agent_decision_v1.txt` 等扁平文件保留可读，外层通过
``load("agent_decision_v1")`` 依旧可用；新代码请使用
``render_prompt("agent_decision", {...})`` 并将 prompt_template_id /
prompt_version 透传给 ``LLMClient``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

PROMPT_DIR = Path(__file__).parent


# ---------------------------------------------------------------------------
# 兼容入口：旧代码 load("agent_decision_v1")
# ---------------------------------------------------------------------------


@lru_cache(maxsize=64)
def load(name: str) -> str:
    """读取单个 prompt 文本。

    支持两种布局：

    - 扁平旧文件：``<name>.txt``（例：``agent_decision_v1.txt``）。
    - 新目录：若 ``name`` 形如 ``<id>_v<N>``，自动尝试
      ``<id>/v<N>.jinja2`` 然后 ``<id>/v<N>.txt``。
    """
    # 优先旧扁平路径，保持兼容
    flat = PROMPT_DIR / f"{name}.txt"
    if flat.exists():
        return flat.read_text(encoding="utf-8")

    # 解析成 <id>_v<N> 形式
    if "_v" in name:
        prompt_id, ver_suffix = name.rsplit("_v", 1)
        version = f"v{ver_suffix}"
        for ext in (".jinja2", ".txt"):
            p = PROMPT_DIR / prompt_id / f"{version}{ext}"
            if p.exists():
                return p.read_text(encoding="utf-8")

    raise FileNotFoundError(f"prompt {name} not found")


# ---------------------------------------------------------------------------
# 新入口：prompt 目录 + 元数据
# ---------------------------------------------------------------------------


@dataclass
class PromptMetadata:
    id: str
    version: str = "v1"
    expected_schema: str | None = None
    supported_models: list[str] = field(default_factory=list)
    last_updated: str | None = None
    # 默认任务角色（chat / reasoning / embedding），供模型选择策略使用
    model_role: str = "chat"
    description: str | None = None


@dataclass
class PromptTemplate:
    id: str
    version: str
    template: str
    metadata: PromptMetadata
    examples: list[dict[str, Any]] = field(default_factory=list)


@lru_cache(maxsize=64)
def get_prompt(prompt_id: str, version: str = "v1") -> PromptTemplate:
    """加载指定 prompt 的模板 + 元数据 + 示例。

    查找顺序：
    1. ``<prompt_id>/<version>.jinja2``
    2. ``<prompt_id>/<version>.txt``
    3. 扁平旧文件 ``<prompt_id>_<version>.txt``（仅模板，无元数据）
    """
    # 1 / 2：目录结构
    dir_path = PROMPT_DIR / prompt_id
    template: str | None = None
    if dir_path.exists() and dir_path.is_dir():
        for ext in (".jinja2", ".txt"):
            p = dir_path / f"{version}{ext}"
            if p.exists():
                template = p.read_text(encoding="utf-8")
                break

    # 3：扁平兼容
    if template is None:
        flat = PROMPT_DIR / f"{prompt_id}_{version}.txt"
        if flat.exists():
            template = flat.read_text(encoding="utf-8")

    if template is None:
        raise FileNotFoundError(
            f"prompt {prompt_id}@{version} not found under {dir_path}"
        )

    # 元数据（若目录有）
    metadata = PromptMetadata(id=prompt_id, version=version)
    meta_path = dir_path / "metadata.json"
    if meta_path.exists():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            metadata = PromptMetadata(
                id=data.get("id", prompt_id),
                version=data.get("version", version),
                expected_schema=data.get("expected_schema"),
                supported_models=list(data.get("supported_models", [])),
                last_updated=data.get("last_updated"),
                model_role=data.get("model_role", "chat"),
                description=data.get("description"),
            )
        except Exception:
            pass

    # 示例（若目录有）
    examples: list[dict[str, Any]] = []
    ex_path = dir_path / "examples.json"
    if ex_path.exists():
        try:
            raw = json.loads(ex_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                examples = raw
            elif isinstance(raw, dict) and "cases" in raw:
                examples = list(raw["cases"])
        except Exception:
            pass

    return PromptTemplate(
        id=prompt_id,
        version=version,
        template=template,
        metadata=metadata,
        examples=examples,
    )


def render_prompt(
    prompt_id: str,
    values: dict[str, Any],
    *,
    version: str = "v1",
) -> tuple[str, PromptMetadata]:
    """加载模板并用简易 ``{{key}}`` 占位符渲染。

    为了不引入 Jinja2 依赖，这里只做字符串替换；模板中不支持条件 / 循环。
    如需更复杂渲染，后续再接入真正的 Jinja2。
    """
    tpl = get_prompt(prompt_id, version=version)
    out = tpl.template
    for k, v in values.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out, tpl.metadata


def list_prompts() -> list[PromptMetadata]:
    """扫描 prompt 目录，返回所有带 metadata 的模板。供评测脚本枚举。"""
    out: list[PromptMetadata] = []
    for child in sorted(PROMPT_DIR.iterdir()):
        if not child.is_dir():
            continue
        meta_path = child / "metadata.json"
        if not meta_path.exists():
            continue
        try:
            tpl = get_prompt(child.name)
            out.append(tpl.metadata)
        except Exception:
            continue
    return out


__all__ = [
    "PROMPT_DIR",
    "PromptMetadata",
    "PromptTemplate",
    "get_prompt",
    "list_prompts",
    "load",
    "render_prompt",
]
