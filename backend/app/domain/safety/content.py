"""
内容安全（阶段 18.4）。

- ``check_player_message``：长度、空白、注入检测的统一入口。
- ``is_prompt_injection``：匹配若干常见的 prompt 越权模式。

模式为中英双语，覆盖最典型的"无视规则 / 泄露 prompt / 索要 API key"类注入。
匹配命中即拒绝处理；业务层应回退到规则回复或直接返回 403。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # 英文
        r"ignore\s+(all\s+)?(previous|above)\s+(instructions|rules)",
        r"reveal\s+(the\s+)?(system\s+)?prompt",
        r"show\s+(me\s+)?(the\s+)?(system\s+)?prompt",
        r"what\s+is\s+your\s+(system\s+)?prompt",
        r"api[\s\-_]*key",
        r"disregard\s+(the\s+)?(system|previous)",
        r"jailbreak",
        r"pretend\s+you\s+are\s+not\s+an?\s+npc",
        # 中文
        r"忽略.{0,6}(规则|指令|系统|提示|设定)",
        r"(告诉|透露|说出|泄露|展示).{0,8}(系统|prompt|指令|规则)",
        r"(API|api).{0,4}(key|密钥)",
        r"密钥",
        r"(提取|导出|打印).{0,4}(prompt|指令|系统提示)",
        r"你不再是.{0,4}(NPC|角色)",
        r"扮演.{0,8}(开发者|管理员|root)",
    )
)


@dataclass
class ContentCheckResult:
    ok: bool
    reason: str | None = None
    error_code: str | None = None
    sanitized_text: str | None = None


def is_prompt_injection(text: str) -> bool:
    if not text:
        return False
    for pat in _INJECTION_PATTERNS:
        if pat.search(text):
            return True
    return False


def check_player_message(
    text: str,
    *,
    max_length: int = 500,
) -> ContentCheckResult:
    raw = text or ""
    stripped = raw.strip()
    if not stripped:
        return ContentCheckResult(
            ok=False,
            reason="消息不能为空",
            error_code="PLAYER_INVALID_INPUT",
        )
    if len(raw) > max_length:
        return ContentCheckResult(
            ok=False,
            reason=f"消息长度超过 {max_length} 字",
            error_code="PLAYER_INVALID_INPUT",
            sanitized_text=raw[:max_length],
        )
    if is_prompt_injection(raw):
        return ContentCheckResult(
            ok=False,
            reason="检测到可能的越权/注入请求",
            error_code="PLAYER_ACTION_REJECTED",
        )
    return ContentCheckResult(ok=True, sanitized_text=stripped)


__all__ = [
    "ContentCheckResult",
    "check_player_message",
    "is_prompt_injection",
]
