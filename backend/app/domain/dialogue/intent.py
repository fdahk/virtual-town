"""
意图识别（阶段 16.2）。

9 类 intent + sentiment + urgency。LLM 失败时走关键词兜底。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.core.logging import get_logger
from app.llm.client import get_llm_client
from app.prompts import render_prompt

logger = get_logger(__name__)


IntentLiteral = Literal[
    "chat",
    "ask_memory",
    "ask_location",
    "request_action",
    "give_item",
    "trade",
    "comfort",
    "threaten",
    "pet_animal",
]


class IntentOutput(BaseModel):
    intent: IntentLiteral = "chat"
    sub_intent: str | None = None
    sentiment: Literal["positive", "neutral", "negative"] = "neutral"
    urgency: Literal["low", "normal", "high"] = "normal"
    requires_action: bool = False


async def classify_intent(
    *,
    raw_text: str,
    target_entity_type: str = "human",
    rewrite_hint: str | None = None,
) -> IntentOutput:
    # 优先使用改写阶段给的 hint
    client = get_llm_client()
    if client.settings.llm_is_configured:
        user_prompt, meta = render_prompt(
            "intent_classify",
            {"raw_text": raw_text, "target_entity_type": target_entity_type},
        )
        data = await client.chat_json(
            system="你必须只输出 JSON。",
            user=user_prompt,
            model=client.settings.model_for_role(meta.model_role or "intent"),
            temperature=0.0,
            max_tokens=200,
            prompt_template_id=meta.id,
            prompt_version=meta.version,
            caller_module="dialogue.intent",
        )
        if data is not None:
            try:
                out = IntentOutput.model_validate(data)
                return out
            except ValidationError:
                logger.warning("intent schema invalid, fallback")

    return _rule_intent(raw_text, target_entity_type, rewrite_hint)


def _rule_intent(
    raw_text: str,
    target_entity_type: str,
    rewrite_hint: str | None = None,
) -> IntentOutput:
    text = raw_text.strip()
    low = text.lower()

    # 最优先：rewrite_hint 若存在
    if rewrite_hint in {
        "chat",
        "ask_memory",
        "ask_location",
        "request_action",
        "give_item",
        "trade",
        "comfort",
        "threaten",
        "pet_animal",
    }:
        return IntentOutput(
            intent=rewrite_hint,  # type: ignore[arg-type]
            sentiment="neutral",
            urgency="normal",
            requires_action=rewrite_hint in {"request_action", "give_item", "trade"},
        )

    # 关键词分类
    def contains(*words: str) -> bool:
        return any(w in text for w in words)

    if target_entity_type == "animal" and contains(
        "摸", "拍", "抚摸", "乖", "来", "过来", "pet"
    ):
        return IntentOutput(intent="pet_animal", requires_action=True)
    if contains("在哪", "怎么去", "在哪里"):
        return IntentOutput(intent="ask_location", urgency="normal")
    if contains("记得", "昨天", "以前", "上次", "之前", "当时"):
        return IntentOutput(intent="ask_memory")
    if contains("能不能", "帮我", "帮帮", "替我"):
        return IntentOutput(intent="request_action", requires_action=True)
    if contains("给你", "送你"):
        return IntentOutput(intent="give_item", requires_action=True)
    if contains("交换", "卖给我", "买一个"):
        return IntentOutput(intent="trade", requires_action=True)
    if contains("没事的", "别怕", "加油", "一切都会好"):
        return IntentOutput(intent="comfort", sentiment="positive")
    if contains("滚开", "威胁", "小心点", "别惹", "揍"):
        return IntentOutput(intent="threaten", sentiment="negative", urgency="high")

    # 情感默认
    sentiment: Literal["positive", "neutral", "negative"] = "neutral"
    if contains("谢谢", "开心", "很好", "棒", "喜欢"):
        sentiment = "positive"
    elif contains("讨厌", "糟糕", "愤怒", "难过"):
        sentiment = "negative"
    return IntentOutput(intent="chat", sentiment=sentiment)


__all__ = ["IntentOutput", "classify_intent"]
