"""
Prompt 模板集合。

约束：
- 所有 prompt 必须文件化、版本化；禁止业务代码里拼字符串。
- 文件名形如 `<purpose>_v<version>.txt`；通过 `load(name)` 读取。
- 每个 prompt 必须指明其期望的输出 JSON Schema。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).parent


@lru_cache(maxsize=32)
def load(name: str) -> str:
    path = PROMPT_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"prompt {name} not found at {path}")
    return path.read_text(encoding="utf-8")
