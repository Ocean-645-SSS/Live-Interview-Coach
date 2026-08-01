"""Local semantic turn detector for joining incomplete STT fragments."""

from __future__ import annotations

import re
from typing import Any

_COMPLETE_PUNCTUATION = ("。", "！", "？", "?", "!", ".")
_INCOMPLETE_PUNCTUATION = ("，", ",", "：", ":", "；", ";", "、")
_TRAILING_CONNECTOR = re.compile(
    r"(?:然后|以及|还有|并且|但是|不过|因为|所以|如果|假如|关于|对于|"
    r"比如|例如|分别是|主要是|包括|涉及|需要|想要|能不能|可不可以|"
    r"的|和|与|或|是|有)$"
)
_OPENING_INTENT = re.compile(
    r"^(?:我想问(?:一下)?|我想了解(?:一下)?|我想知道|请问|关于|对于).{0,24}$"
)


class SemanticTurnDetector:
    """Choose the long endpoint delay when the latest Chinese text is incomplete.

    LiveKit interprets a probability below ``unlikely_threshold`` as an
    incomplete turn and waits ``max_endpointing_delay``. New speech cancels
    that pending commit, so subsequent STT fragments stay in the same turn.
    """

    model = "liverag-semantic-rules-v1"
    provider = "liverag"

    async def supports_language(self, language: Any | None) -> bool:
        return True

    async def unlikely_threshold(self, language: Any | None) -> float:
        return 0.5

    async def predict_end_of_turn(
        self,
        chat_ctx: Any,
        *,
        timeout: float | None = None,
    ) -> float:
        return 0.1 if self.is_incomplete(self._latest_user_text(chat_ctx)) else 0.9

    @staticmethod
    def is_incomplete(text: str) -> bool:
        clean = text.strip()
        if not clean:
            return True
        if clean.endswith(_COMPLETE_PUNCTUATION):
            return False
        if clean.endswith(_INCOMPLETE_PUNCTUATION):
            return True
        if _TRAILING_CONNECTOR.search(clean):
            return True
        return bool(_OPENING_INTENT.fullmatch(clean))

    @staticmethod
    def _latest_user_text(chat_ctx: Any) -> str:
        messages = getattr(chat_ctx, "messages", [])
        if callable(messages):
            messages = messages()
        for message in reversed(list(messages or [])):
            if getattr(message, "role", None) == "user":
                return str(getattr(message, "text_content", "") or "")
        return ""
