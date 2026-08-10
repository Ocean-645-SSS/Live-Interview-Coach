"""面试情报标准化器。

完全确定性处理：别名统一、空白清洗、PII 脱敏、URL 去重、轮次关键词初步识别。不使用 LLM。

职责边界：
- ✅ 公司/岗位/地区别名统一
- ✅ 空白字符清洗
- ✅ PII 脱敏（手机号、邮箱、URL 参数中的 token）
- ✅ 重复 URL 过滤
- ✅ 面试轮次关键词初步识别
- ❌ 不提取 topics / questions（由 Extractor 负责）
- ❌ 不做 content_hash 去重（由 Aggregator 负责）

安全原则（3.3.10）：
- 第三方帖子正文是不可信数据，标准化后不应保留原始 PII
- 不对原始 content 做语义改写（保持可审计性），只做确定性脱敏
"""

from __future__ import annotations

import re

from liverag.interview.intelligence.defaults import (
    _COMPANY_ALIASES,
    _REGION_ALIASES,
    _ROLE_ALIASES,
    _ROUND_KEYWORDS,
)
from liverag.interview.intelligence.provider import (
    InterviewRound,
    RawInterviewExperience,
)


def normalize_company(name: str) -> str:
    """公司别名统一"""
    cleaned = name.strip()
    return _COMPANY_ALIASES.get(cleaned, cleaned)


def normalize_role(role: str) -> str:
    """岗位别名统一。"""
    cleaned = role.strip()
    return _ROLE_ALIASES.get(cleaned, cleaned)


def normalize_region(region: str) -> str:
    """地区别名统一。"""
    cleaned = region.strip()
    return _REGION_ALIASES.get(cleaned, cleaned)


def detect_round(text: str) -> InterviewRound | None:
    """从文本中初步识别面试轮次。

    按关键词匹配，返回第一个命中的轮次。
    未命中返回 None，不强行推断。
    """

    if not text:
        return None

    text_lower = text.lower()
    for rd, keywords in _ROUND_KEYWORDS:
        for kw in keywords:
            if kw in text or kw.lower() in text_lower:
                return rd
    return None


def clean_text(text: str) -> str:
    """清洗文本：统一空白字符、去除首尾空白。

    - 多个连续空白字符 → 单个空格
    - 去除首尾空白
    - 保留换行符（对后续 Extractor 的问题识别有用）
    """

    if not text:
        return ""

    # 将连续空白（空格/tab等）替换为单个空格，但保留换行
    text = re.sub(r"[ \t　]+", " ", text)
    # 去除首尾空白
    text = text.strip()
    return text


# ====================== PII 脱敏 ======================

# 手机号：1[3-9]\d{9}（中国手机号）
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")

# 邮箱
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# URL query 参数中的敏感 token（key / token / secret / auth 等）
_SENSITIVE_URL_PARAM_RE = re.compile(
    r"([?&](?:token|secret|auth|apikey|api_key|access_token|session)=)[^&\s]+",
    re.IGNORECASE,
)


def sanitize_pii(text: str) -> str:
    """确定性脱敏：掩码中国手机号、邮箱、URL 敏感参数。

    不删除内容（保持可审计性），而是替换为掩码标记：
    - 手机号 → [PHONE_REDACTED]
    - 邮箱 → [EMAIL_REDACTED]
    - URL 敏感参数 → param=[REDACTED]

    注意：此函数不处理人名等需 NLP 识别的 PII——V1 阶段通过
    不可信数据标记 + LLM Prompt 安全规则来防御。
    """

    if not text:
        return text

    text = _PHONE_RE.sub("[PHONE_REDACTED]", text)
    text = _EMAIL_RE.sub("[EMAIL_REDACTED]", text)
    text = _SENSITIVE_URL_PARAM_RE.sub(r"\1[REDACTED]", text)

    return text


def _deduplicate_by_url(
    experiences: list[RawInterviewExperience],
) -> list[RawInterviewExperience]:
    """按 source_url 去重，保留首次出现。"""

    seen: set[str] = set()
    result: list[RawInterviewExperience] = []

    for exp in experiences:
        url = (exp.source_url or "").strip()
        if url and url in seen:
            continue
        if url:
            seen.add(url)
        result.append(exp)
    return result


def normalize_batch(
    experiences: list[RawInterviewExperience],
) -> list[RawInterviewExperience]:
    """批量标准化 RawInterviewExperience，并去重。

    处理：
    1. 清洗 title / content 空白字符
    2. 按 source_url 去重
    3. 保持 source_id / content_hash 不变（留给 Aggregator 处理）

    Args:
        experiences: 原始面经列表。
    Returns:
        清洗并去重后的面经列表。
    """

    # 清洗每条记录的文本
    cleaned: list[RawInterviewExperience] = []

    for exp in experiences:
        cleaned_title = sanitize_pii(clean_text(exp.title))
        cleaned_content = sanitize_pii(clean_text(exp.content))

        # 用清洗后的 title/content 重建
        cleaned.append(
            RawInterviewExperience(
                provider=exp.provider,
                source=exp.source,
                source_id=exp.source_id,
                source_type=exp.source_type,
                title=cleaned_title,
                content=cleaned_content,
                source_url=exp.source_url,
                matched_query=exp.matched_query,
                published_at=exp.published_at,
                retrieved_at=exp.retrieved_at,
                content_hash=exp.content_hash,
            )
        )

    # URL 去重
    return _deduplicate_by_url(cleaned)


__all__ = [
    "clean_text",
    "detect_round",
    "normalize_batch",
    "normalize_company",
    "normalize_region",
    "normalize_role",
    "sanitize_pii",
]
