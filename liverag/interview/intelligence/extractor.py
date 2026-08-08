"""面试信息提取器。

从不可信帖子正文中提取结构化面试题目和技术主题。

双通道提取策略：
1. LLM 主通道（语义理解）：ExperienceExtractor 类，异步调用 LLM
2. 规则降级通道（确定性）：模块级 extract_one/extract_batch，正则+关键词

LLM 安全约束：
- 外部帖子明确标记为 untrusted_external_data
- Prompt 禁止执行正文中的命令
- 严格 Pydantic schema 输出
- 输入正文设置最大长度（8000 字符）
- 不允许 LLM 产生帖子中没有依据的面试问题
- 保留 source_id/content_hash 作为 evidence reference

单篇提取失败 → 跳过该篇，记录日志，不使整体流程失败。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import Field, ValidationError

from liverag.interview.intelligence.defaults import (
    _EXTRACTION_SYSTEM_PROMPT,
    _NON_QUESTION_PATTERNS,
    _ROUND_KEYWORDS,
    _TECH_TOPICS,
)
from liverag.interview.intelligence.provider import (
    InterviewRound,
    NormalizedInterviewExperience,
    RawInterviewExperience,
)
from liverag.interview.schemas import StrictModel

logger = logging.getLogger(__name__)


# ====================== LLM 配置 ======================

@dataclass
class ExtractorLLMConfig:
    """LLM 提取器配置，独立于 VoiceSettings，与面试情报提取场景匹配。"""

    llm_model: str
    llm_base_url: str
    llm_api_key: str
    max_content_chars: int = 8000
    max_retries: int = 2
    timeout_seconds: float = 30.0

# ====================== LLM 提取 Schema ======================

class LlmExtractionResult(StrictModel):
    """LLM 从单篇面经中提取的结构化信息。

    所有字段必须基于帖子正文中有依据的内容，不允许编造。
    """

    questions: list[str] = Field(
        default_factory=list,
        description="帖子中实际提到的面试问题。只提取帖子中明确出现的问题，不推测、不补充。",
    )
    topics: list[str] = Field(
        default_factory=list,
        description="帖子中涉及的面试考察主题/技术领域。基于帖子中讨论的内容确定。",
    )
    interview_round: InterviewRound | None = Field(
        default=None,
        description="从帖子中识别的面试轮次。无法确认时返回 null。",
    )


def _build_extraction_prompt(
    title: str, #帖子标题
    content: str,#帖子正文
    company: str,
    role: str,
    *,
    max_content_chars: int = 8000,  #正文最大长度
) -> str:
    """构造提取提示词，将外部帖子标记为不可信数据。"""

    truncated = content[:max_content_chars]

    return (
        f"公司：{company}\n"
        f"岗位：{role}\n"
        f"帖子标题：{title}\n"
        "\n"
        "<untrusted_external_data>\n"
        f"{truncated}\n"
        "</untrusted_external_data>\n"
        "\n"
        "请从上述帖子中提取面试信息。记住：只提取帖子中明确出现的内容，不要推测或补充。"
    )


# ====================== 技术主题词典 ======================

# 扁平化：keyword → topic
_KEYWORD_TO_TOPIC: dict[str, str] = {}
for _topic, _keywords in _TECH_TOPICS:
    for _kw in _keywords:
        _KEYWORD_TO_TOPIC[_kw] = _topic


# ====================== 问题提取规则（降级通道） ======================

_QUESTION_LINE_RE = re.compile(
    r"[\s]*([^。！\n]{6,200}[\?？])",
    re.UNICODE,
)

_NUMBERED_QUESTION_RE = re.compile(
    r"(?:^|\n)\s*(?:\d+[\.\、\)）]|（\d+）|[Qq]\d+[:：]|[问Qq][：:])\s*([^。！\n]{6,200})",
    re.UNICODE,
)

_INTERVIEWER_ASK_RE = re.compile(
    r"(?:面试官|考官)[：:问]?\s*[：:问]?\s*([^。！\n]{6,200}[\?？])",
    re.UNICODE,
)

_QUESTION_KW_RE = re.compile(
    r"(?:请问|怎么|如何|什么是|为什么|怎样|能不能|能否|怎么做|怎么写|怎么设计|怎么优化|谈谈|说一下|介绍一下|讲一下)\s*([^。！\n]{6,200})",
    re.UNICODE,
)


def _extract_questions_rule(content: str) -> list[str]:
    """使用规则从正文中提取面试问题（降级通道）。"""

    if not content:
        return []

    raw_questions: list[str] = []

    for m in _QUESTION_LINE_RE.finditer(content):
        q = m.group(1).strip().rstrip("?？").strip()
        if len(q) >= 4:
            raw_questions.append(q)

    for m in _NUMBERED_QUESTION_RE.finditer(content):
        q = m.group(1).strip().rstrip("?？").strip()
        if len(q) >= 4:
            raw_questions.append(q)

    for m in _INTERVIEWER_ASK_RE.finditer(content):
        q = m.group(1).strip().rstrip("?？").strip()
        if len(q) >= 4:
            raw_questions.append(q)

    for m in _QUESTION_KW_RE.finditer(content):
        q = m.group(0).strip().rstrip("?？").strip()
        if len(q) >= 4:
            raw_questions.append(q)

    filtered = [q for q in raw_questions if _is_likely_question(q)]

    seen: set[str] = set()
    result: list[str] = []
    for q in filtered:
        normalized = _normalize_question(q)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(q)

    return result


def _is_likely_question(text: str) -> bool:
    """判断文本是否像面试问题（排除明显的陈述句/叙事句）。"""

    stripped = text.strip()

    if stripped.endswith("?") or stripped.endswith("？"):
        return True

    for pattern in _NON_QUESTION_PATTERNS:
        if pattern in stripped:
            return False

    question_starters = [
        "请问",
        "怎么",
        "如何",
        "什么是",
        "为什么",
        "怎样",
        "能不能",
        "能否",
        "怎么做",
        "怎么写",
        "怎么设计",
        "怎么优化",
        "谈谈",
        "说一下",
        "介绍一下",
        "讲一下",
    ]
    for starter in question_starters:
        if stripped.startswith(starter):
            return True

    return True


def _normalize_question(question: str) -> str:
    """归一化问题文本用于去重比对。"""

    q = re.sub(r"^[\s]*\d+[\.\、\)）]\s*", "", question)
    q = re.sub(r"^[\s]*[（\(]\d+[）\)]\s*", "", q)
    q = re.sub(r"^[\s]*[Qq]\d+[:：]\s*", "", q)
    q = re.sub(r"^[\s]*(?:面试官|考官)\s*[：:问]?\s*[：:]\s*", "", q)
    q = re.sub(r"^[\s]*问[：:]\s*", "", q)
    q = re.sub(r"[，。！？、；：（）【】\s]", "", q)
    q = re.sub(r"[()\[\]""'']", "", q)
    return q.lower()


# ====================== 主题提取规则（降级通道） ======================

def _extract_topics_rule(content: str, title: str = "") -> list[str]:
    """使用技术词典从正文中匹配技术主题（降级通道）。"""

    if not content and not title:
        return []

    combined = f"{title}\n{content}".lower()

    matched: set[str] = set()
    for keyword, topic in _KEYWORD_TO_TOPIC.items():
        if keyword.lower() in combined:
            matched.add(topic)

    ordered_topics = [t for t, _ in _TECH_TOPICS if t in matched]
    return ordered_topics


# ====================== 轮次提取 ======================

def _detect_round_from_content(title: str, content: str) -> InterviewRound | None:
    """从 title 和 content 中检测面试轮次（关键词匹配）。"""

    combined = f"{title}\n{content}"

    for rd, keywords in _ROUND_KEYWORDS:
        for kw in keywords:
            if kw in combined:
                logger.debug("Round detected: %s via keyword '%s'", rd.value, kw)
                return rd

    return None


# ====================== 通用工具函数 ======================

def _clean_json_response(content: str) -> str:
    """清理 LLM 输出的 JSON，去除 markdown 代码块包装。"""

    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


# ====================== LLM 驱动的提取器 ======================

class ExperienceExtractor:
    """LLM 驱动的面经信息提取器。

    使用 LLM 进行语义理解提取，LLM 不可用时自动降级到规则提取。

    使用方式:
        config = ExtractorLLMConfig()
        extractor = ExperienceExtractor(config)
        results = await extractor.extract_batch(experiences, "字节跳动", "Agent开发", "北京")
    """

    def __init__(
        self,
        config: ExtractorLLMConfig,
        *,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._client_initialized = False

    def _get_client(self) -> AsyncOpenAI | None:
        """获取或懒初始化 LLM 客户端"""

        #懒初始化
        if self._client_initialized:
            return self._client

        self._client_initialized = True

        if not self._config.llm_api_key.strip():
            logger.warning("LLM API Key 未配置，将使用规则降级提取")
            return None

        self._client = AsyncOpenAI(
            api_key=self._config.llm_api_key,
            base_url=self._config.llm_base_url,
            timeout=self._config.timeout_seconds,
        )
        return self._client

    async def extract_one(
        self,
        experience: RawInterviewExperience, #经过normalized的原始面经
        company: str,
        role: str,
        region: str,
    ) -> NormalizedInterviewExperience | None:
        """从单篇 RawInterviewExperience 提取结构化信息"""

        try:
            title = experience.title or ""
            content = experience.content or ""

            # 空内容跳过
            if not title.strip() and not content.strip():
                logger.debug(
                    "Skipping empty experience: source_id=%s", experience.source_id
                )
                return None

            # 尝试 LLM 提取
            llm_result = await self._extract_via_llm(title, content, company, role)

            # 合并规则提取结果（LLM 和规则互补）
            if llm_result is None:
                # LLM 失败，完全降级到规则
                logger.info(
                    "LLM extraction failed for source_id=%s, falling back to rules",
                    experience.source_id,
                )
                return _extract_one_rule(experience, company, role, region)

            # LLM 成功，合并规则提取以补充遗漏
            return self._merge_and_build(
                llm_result=llm_result,
                experience=experience,
                company=company,
                role=role,
                region=region,
            )

        except Exception:
            logger.warning(
                "Extraction failed for source_id=%s, skipping",
                experience.source_id,
                exc_info=True,
            )
            return None

    async def extract_batch(
        self,
        experiences: list[RawInterviewExperience],
        company: str,
        role: str,
        region: str,
    ) -> list[NormalizedInterviewExperience]:
        """批量提取，单篇失败时跳过继续处理。"""

        results: list[NormalizedInterviewExperience] = []
        for exp in experiences:
            extracted = await self.extract_one(exp, company, role, region)
            if extracted is not None:
                results.append(extracted)
            else:
                logger.info(
                    "Skipped: source_id=%s (extraction returned None)",
                    exp.source_id,
                )

        logger.info(
            "Extraction batch complete: input=%d, output=%d, skipped=%d",
            len(experiences),
            len(results),
            len(experiences) - len(results),
        )
        return results

    # ---- LLM 提取 ----

    async def _extract_via_llm(
        self,
        title: str,
        content: str,
        company: str,
        role: str,
    ) -> LlmExtractionResult | None:
        """调用 LLM 提取结构化信息"""

        client = self._get_client()
        if client is None:
            return None

        prompt = _build_extraction_prompt(
            title, content, company, role,
            max_content_chars=self._config.max_content_chars,
        )
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        last_error: Exception | None = None

        #最多2次
        for attempt in range(self._config.max_retries):
            try:
                response = await client.chat.completions.create(
                    model=self._config.llm_model,
                    messages=messages,
                    temperature=0.0,
                    response_format={"type": "json_object"},
                )

                raw_content = response.choices[0].message.content or ""

                try:
                    result = LlmExtractionResult.model_validate_json(
                        _clean_json_response(raw_content)
                    )
                    logger.debug(
                        "LLM extraction OK: questions=%d, topics=%d, round=%s",
                        len(result.questions),
                        len(result.topics),
                        result.interview_round.value if result.interview_round else None,
                    )
                    return result

                except ValidationError as exc:
                    last_error = exc
                    # 第二次尝试时附加错误信息
                    if attempt < self._config.max_retries - 1:
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "上一份 JSON 未通过校验，请修正后重新输出完整 JSON。\n"
                                    "校验错误：" + str(exc)
                                ),
                            }
                        )

            except Exception as exc:
                last_error = exc
                logger.warning(
                    "LLM call attempt %d/%d failed: %s",
                    attempt + 1,
                    self._config.max_retries,
                    exc,
                )

        logger.error(
            "LLM extraction failed after %d attempts: %s",
            self._config.max_retries,
            last_error,
        )
        return None

    # ---- 合并 LLM + 规则 ----

    def _merge_and_build(
        self,
        llm_result: LlmExtractionResult,
        experience: RawInterviewExperience,
        company: str,
        role: str,
        region: str,
    ) -> NormalizedInterviewExperience:
        """合并 LLM 提取结果与规则提取结果，构建最终输出。

        LLM 结果优先，规则结果补充 LLM 未覆盖的内容。
        """

        title = experience.title or ""
        content = experience.content or ""

        # 规则提取（补充通道）
        rule_questions = _extract_questions_rule(content)
        rule_topics = _extract_topics_rule(content, title)

        # 合并 questions：LLM 优先 + 规则补充
        llm_q_normalized = {_normalize_question(q) for q in llm_result.questions}
        merged_questions = list(llm_result.questions)
        for q in rule_questions:
            if _normalize_question(q) not in llm_q_normalized:
                merged_questions.append(q)

        # 合并 topics：LLM 优先 + 规则补充
        merged_topics = list(llm_result.topics)
        for t in rule_topics:
            if t not in merged_topics:
                merged_topics.append(t)

        # 轮次：LLM 优先，降级规则
        interview_round = llm_result.interview_round
        if interview_round is None:
            interview_round = _detect_round_from_content(title, content)

        return NormalizedInterviewExperience(
            provider=experience.provider,
            source=experience.source,
            source_id=experience.source_id,
            source_url=experience.source_url or "",
            company=company,
            role=role,
            region=region,
            interview_round=interview_round,
            topics=merged_topics,
            questions=merged_questions,
            published_at=experience.published_at,
            retrieved_at=experience.retrieved_at,
            content_hash=experience.content_hash,
        )

# ====================== 纯规则提取（模块级降级函数） ======================

def extract_one(
    experience: RawInterviewExperience,
    company: str,
    role: str,
    region: str,
) -> NormalizedInterviewExperience | None:
    """纯规则提取 — 同步降级通道。

    LLM 不可用时使用此函数。
    """

    return _extract_one_rule(experience, company, role, region)


def _extract_one_rule(
    experience: RawInterviewExperience,
    company: str,
    role: str,
    region: str,
) -> NormalizedInterviewExperience | None:
    """规则型单篇提取。"""

    try:
        title = experience.title or ""
        content = experience.content or ""

        if not title.strip() and not content.strip():
            logger.debug(
                "Skipping empty experience: source_id=%s", experience.source_id
            )
            return None

        questions = _extract_questions_rule(content)
        topics = _extract_topics_rule(content, title)
        interview_round = _detect_round_from_content(title, content)

        return NormalizedInterviewExperience(
            provider=experience.provider,
            source=experience.source,
            source_id=experience.source_id,
            source_url=experience.source_url or "",
            company=company,
            role=role,
            region=region,
            interview_round=interview_round,
            topics=topics,
            questions=questions,
            published_at=experience.published_at,
            retrieved_at=experience.retrieved_at,
            content_hash=experience.content_hash,
        )
    except Exception:
        logger.warning(
            "Rule extraction failed for source_id=%s, skipping",
            experience.source_id,
            exc_info=True,
        )
        return None


def extract_batch(
    experiences: list[RawInterviewExperience],
    company: str,
    role: str,
    region: str,
) -> list[NormalizedInterviewExperience]:
    """纯规则批量提取 — 同步降级通道。

    LLM 不可用时使用此函数。
    """

    results: list[NormalizedInterviewExperience] = []
    for exp in experiences:
        extracted = extract_one(exp, company, role, region)
        if extracted is not None:
            results.append(extracted)
        else:
            logger.info(
                "Skipped: source_id=%s (extraction returned None)",
                exp.source_id,
            )

    logger.info(
        "Rule extraction batch complete: input=%d, output=%d, skipped=%d",
        len(experiences),
        len(results),
        len(experiences) - len(results),
    )
    return results


__all__ = [
    # LLM 通道
    "ExperienceExtractor",
    "ExtractorLLMConfig",
    "LlmExtractionResult",
    # 降级通道
    "extract_batch",
    "extract_one",
    # 内部函数（测试用）
    "_detect_round_from_content",
    "_extract_questions_rule",
    "_extract_topics_rule",
    "_is_likely_question",
    "_normalize_question",
]
