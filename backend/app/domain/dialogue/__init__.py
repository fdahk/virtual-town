"""玩家文本处理（阶段 16）：query 改写 / 意图识别 / 多轮对话 / 结构化回复。"""

from app.domain.dialogue.conversation_store import (
    ConversationWindow,
    get_conversation_store,
)
from app.domain.dialogue.intent import (
    IntentOutput,
    classify_intent,
)
from app.domain.dialogue.query_rewrite import (
    QueryRewriteOutput,
    ResolvedEntity,
    rewrite_query,
)
from app.domain.dialogue.reply import (
    DialogueReplyOutput,
    RelationshipDelta,
    generate_structured_reply,
)

__all__ = [
    "ConversationWindow",
    "DialogueReplyOutput",
    "IntentOutput",
    "QueryRewriteOutput",
    "RelationshipDelta",
    "ResolvedEntity",
    "classify_intent",
    "generate_structured_reply",
    "get_conversation_store",
    "rewrite_query",
]
