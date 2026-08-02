"""从 study_source.md 八股文档转换为可审核的题目草稿：问题+纯文字答案。
流程：
读取 study_source.md
    ↓
清理飞书格式
    ↓
解析 Markdown 标题结构
    ↓
识别哪些标题是问题
    ↓
提取标题下面的文字答案
    ↓
识别主问题与追问
    ↓
排除非题库内容
    ↓
合并重复问题
    ↓
输出 ExtractedQuestionDraft
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from pathlib import Path

_HEADING_PATTERN = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*$")
_IMAGE_ONLY_PATTERN = re.compile(
    r"^\s*(?:>\s*)*!\[[^]]*]\([^)]*\)\s*$"
)
_MARKDOWN_LINK_PATTERN = re.compile(r"\[([^]]+)]\(https?://[^)]+\)")
_RAW_URL_PATTERN = re.compile(r"https?://\S+")
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_MARKDOWN_STYLE_PATTERN = re.compile(r"[*_`]+")
_LEADING_NUMBER_PATTERN = re.compile(r"^\d+[.、]\s*")
_FOLLOW_UP_PATTERN = re.compile(r"[（(]?\s*追问\s*[)）]?", re.IGNORECASE)
_HIGH_FREQUENCY_PATTERN = re.compile(r"[（(]?\s*高频\s*[)）]?", re.IGNORECASE)

_QUESTION_HINT_PATTERN = re.compile(
    r"[？?]|"
    r"什么|为什么|为何|如何|怎么|哪些|是否|能否|有没有|"
    r"区别|流程|原理|作用|优缺点|优劣|优势|劣势|"
    r"介绍|解释|理解|设计|实现|优化|解决|评价|评估|"
    r"架构|机制|阶段|方式|方法|组成|分类|过程|顺序|关系|"
    r"场景|策略|计算|谈谈|知道|用过|问题|特点"
)

_EXCLUDED_ROOT_SECTIONS = frozenset({"项目篇"})
_EXCLUDED_SECTION_TITLES = frozenset(
    {
        "简历撰写思路(附模板)",
        "面试话术",
        "项目介绍",
        "项目相关的问题",
    }
)

_ROOT_CATEGORY_MAP: dict[str, str] = {
    "大模型基础篇": "大模型",
    "大模型训练篇": "大模型",
    "大模型推理篇": "大模型",
    "模型与平台篇": "大模型",
    "RAG开发篇": "RAG",
    "Agent篇": "Agent",
    "AI编程篇": "AI编程",
    "场景题": "场景题",
}


@dataclass(frozen=True, slots=True)
class ExtractedQuestionDraft:
    """从 Markdown 确定性抽取、等待补全评分信息的一道题目。"""

    id: str
    question_text: str
    reference_answer: str
    category: str
    subcategory: str | None
    source_reference: str
    parent_question_id: str | None
    is_high_frequency: bool
    source_line: int


@dataclass(frozen=True, slots=True)
class ConversionStatistics:
    """记录一次转换的数量信息，便于发现解析规则是否异常。"""

    physical_lines: int
    headings: int
    extracted_questions: int
    extracted_follow_ups: int
    duplicate_questions: int
    skipped_images: int
    skipped_external_links: int
    skipped_guidance_blocks: int
    skipped_empty_answers: int


@dataclass(frozen=True, slots=True)
class QuestionBankConversionResult:
    """保存题目草稿和本次转换统计。"""

    questions: tuple[ExtractedQuestionDraft, ...]
    statistics: ConversionStatistics


@dataclass(slots=True)
class _HeadingBlock:
    """Markdown 中一个标题及其直接正文，供转换器内部使用。"""

    level: int
    title: str
    line_number: int
    ancestors: tuple[tuple[int, str], ...]
    excluded: bool
    is_high_frequency: bool
    body_lines: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _MutableStatistics:
    """转换过程中累加计数，结束后再固化为公开统计对象。"""

    physical_lines: int = 0
    headings: int = 0
    duplicate_questions: int = 0
    skipped_images: int = 0
    skipped_external_links: int = 0
    skipped_guidance_blocks: int = 0
    skipped_empty_answers: int = 0


def _clean_inline_text(text: str) -> tuple[str, int]:
    """清除飞书 HTML、Markdown 链接地址和原始 URL，保留可读文字。"""

    decoded = html.unescape(text)
    markdown_link_count = len(_MARKDOWN_LINK_PATTERN.findall(decoded))
    decoded = _MARKDOWN_LINK_PATTERN.sub(r"\1", decoded)
    raw_url_count = len(_RAW_URL_PATTERN.findall(decoded))
    decoded = _RAW_URL_PATTERN.sub("", decoded)
    decoded = _HTML_TAG_PATTERN.sub("", decoded)
    decoded = decoded.replace("\u200b", "").replace("\xa0", " ")
    cleaned = re.sub(r"[ \t]+", " ", decoded).strip()
    return cleaned, markdown_link_count + raw_url_count


def _clean_heading_title(title: str) -> str:
    """清理标题样式、编号和高频标记，保留题目本身。
    去除：Markdown 加粗符号、标题编号、高频括号、多余空格和冒号"""

    cleaned, _ = _clean_inline_text(title)
    cleaned = _MARKDOWN_STYLE_PATTERN.sub("", cleaned)
    cleaned = _LEADING_NUMBER_PATTERN.sub("", cleaned)
    cleaned = _HIGH_FREQUENCY_PATTERN.sub("", cleaned)
    return cleaned.strip(" ：:")


def _normalize_question_text(text: str) -> str:
    """生成题目去重键，不改变最终展示的原始题目文字。"""

    cleaned = _FOLLOW_UP_PATTERN.sub("", text)
    cleaned = _MARKDOWN_STYLE_PATTERN.sub("", cleaned)
    cleaned = re.sub(r"[\s：:，,。.!！？?、（）()]", "", cleaned)
    return cleaned.casefold()


def _make_question_id(question_text: str) -> str:
    """根据规范化题干生成跨重复章节保持稳定的草稿 ID。"""

    normalized = _normalize_question_text(question_text)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"draft_{digest}"


def _looks_like_question(title: str, level: int) -> bool:
    """根据标题文字和层级判断它是否是面试问题而不是章节分类。"""

    if _FOLLOW_UP_PATTERN.search(title):
        return True
    if level == 4:
        return True
    return bool(_QUESTION_HINT_PATTERN.search(title))


def _is_guidance_heading(title: str) -> bool:
    """判断标题是否属于明确排除的简历或面试话术材料。"""

    normalized = _clean_heading_title(title)
    return normalized in _EXCLUDED_SECTION_TITLES


def _category_name(title: str) -> str:
    """把章节标题转换为适合题库展示的分类名称。"""

    cleaned = _clean_heading_title(title)
    if cleaned.endswith("开发篇"):
        cleaned = cleaned[: -len("开发篇")]
    elif cleaned.endswith("基础篇"):
        cleaned = cleaned[: -len("基础篇")]
    elif cleaned.endswith("训练篇"):
        cleaned = cleaned[: -len("训练篇")]
    elif cleaned.endswith("推理篇"):
        cleaned = cleaned[: -len("推理篇")]
    elif cleaned.endswith("篇"):
        cleaned = cleaned[:-1]
    return cleaned.strip() or "未分类"


class QuestionBankMarkdownConverter:
    """将指定 Markdown 文档解析成不含图片和外链的题目草稿。"""

    def convert_file(self, path: Path) -> QuestionBankConversionResult:
        """最重要的函数：读取 UTF-8 Markdown，抽取、去重并返回题目草稿。
        Args:path: 原始 Markdown 文件路径。
        """

        expanded_path = path.expanduser()
        lines = expanded_path.read_text(encoding="utf-8").splitlines()
        statistics = _MutableStatistics(physical_lines=len(lines))
        #解析Markdown标题树
        blocks = self._parse_blocks(lines, statistics)
        drafts = self._extract_drafts(
            blocks,
            source_name=expanded_path.name,
            statistics=statistics,
        )
        follow_up_count = sum(
            1 for draft in drafts if draft.parent_question_id is not None
        )
        return QuestionBankConversionResult(
            questions=tuple(drafts),
            statistics=ConversionStatistics(
                physical_lines=statistics.physical_lines,
                headings=statistics.headings,
                extracted_questions=len(drafts),
                extracted_follow_ups=follow_up_count,
                duplicate_questions=statistics.duplicate_questions,
                skipped_images=statistics.skipped_images,
                skipped_external_links=statistics.skipped_external_links,
                skipped_guidance_blocks=statistics.skipped_guidance_blocks,
                skipped_empty_answers=statistics.skipped_empty_answers,
            ),
        )

    def _parse_blocks(
        self,
        lines: list[str],
        statistics: _MutableStatistics,
    ) -> list[_HeadingBlock]:
        """将 Markdown 拆成带祖先路径的标题块。"""

        blocks: list[_HeadingBlock] = []
        heading_stack: list[tuple[int, str, bool, bool]] = []
        current_block: _HeadingBlock | None = None

        for line_number, raw_line in enumerate(lines, start=1):
            heading_match = _HEADING_PATTERN.match(raw_line)
            if heading_match:
                statistics.headings += 1
                level = len(heading_match.group(1))
                raw_title = heading_match.group(2)
                title = _clean_heading_title(raw_title)
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()

                parent_excluded = any(item[2] for item in heading_stack)
                inherited_high_frequency = any(item[3] for item in heading_stack)
                own_high_frequency = bool(_HIGH_FREQUENCY_PATTERN.search(raw_title))
                root_excluded = (
                    level == 1 and title in _EXCLUDED_ROOT_SECTIONS
                )
                excluded = parent_excluded or root_excluded or _is_guidance_heading(title)
                ancestors = tuple((item[0], item[1]) for item in heading_stack)
                current_block = _HeadingBlock(
                    level=level,
                    title=title,
                    line_number=line_number,
                    ancestors=ancestors,
                    excluded=excluded,
                    is_high_frequency=(
                        inherited_high_frequency or own_high_frequency
                    ),
                )
                blocks.append(current_block)
                heading_stack.append(
                    (
                        level,
                        title,
                        excluded,
                        inherited_high_frequency or own_high_frequency,
                    )
                )
                continue

            if current_block is None:
                continue
            if _IMAGE_ONLY_PATTERN.match(raw_line):
                statistics.skipped_images += 1
                continue

            cleaned_line, ignored_links = _clean_inline_text(raw_line)
            statistics.skipped_external_links += ignored_links
            if cleaned_line:
                current_block.body_lines.append(cleaned_line)
        return blocks

    def _extract_drafts(
        self,
        blocks: list[_HeadingBlock],
        *,
        source_name: str,
        statistics: _MutableStatistics,
    ) -> list[ExtractedQuestionDraft]:
        """从标题块识别问题，建立追问关系，并按规范化题干去重。"""

        drafts_by_key: dict[str, ExtractedQuestionDraft] = {}
        last_main_question_id: str | None = None

        for block in blocks:
            if block.excluded:
                if block.body_lines:
                    statistics.skipped_guidance_blocks += 1
                continue
            if not _looks_like_question(block.title, block.level):
                continue
            if not block.body_lines:
                statistics.skipped_empty_answers += 1
                continue

            question_text = _FOLLOW_UP_PATTERN.sub("", block.title).strip(" ：:")
            normalized_key = _normalize_question_text(question_text)
            if not normalized_key:
                continue

            is_follow_up = bool(_FOLLOW_UP_PATTERN.search(block.title))
            question_id = _make_question_id(question_text)
            parent_question_id = last_main_question_id if is_follow_up else None
            if is_follow_up and parent_question_id is None:
                statistics.skipped_empty_answers += 1
                continue

            category, subcategory = self._resolve_classification(block)
            heading_path = [title for _, title in block.ancestors] + [block.title]
            source_reference = (
                f"{source_name}:line-{block.line_number}#" + " / ".join(heading_path)
            )
            reference_answer = "\n".join(block.body_lines).strip()
            draft = ExtractedQuestionDraft(
                id=question_id,
                question_text=question_text,
                reference_answer=reference_answer,
                category=category,
                subcategory=subcategory,
                source_reference=source_reference,
                parent_question_id=parent_question_id,
                is_high_frequency=block.is_high_frequency,
                source_line=block.line_number,
            )

            existing = drafts_by_key.get(normalized_key)
            if existing is not None:
                statistics.duplicate_questions += 1
                if len(draft.reference_answer) > len(existing.reference_answer):
                    drafts_by_key[normalized_key] = draft
                if not is_follow_up:
                    last_main_question_id = existing.id
                continue

            drafts_by_key[normalized_key] = draft
            if not is_follow_up:
                last_main_question_id = question_id

        return sorted(drafts_by_key.values(), key=lambda draft: draft.source_line)

    def _resolve_classification(
        self,
        block: _HeadingBlock,
    ) -> tuple[str, str | None]:
        """根据问题祖先标题推导一级分类和可选二级分类。"""

        ancestors = [
            (level, title)
            for level, title in block.ancestors
            if not _looks_like_question(title, level)
        ]
        root = next((title for level, title in ancestors if level == 1), None)
        section = next((title for level, title in ancestors if level == 2), None)
        subsection = next((title for level, title in ancestors if level == 3), None)

        mapped_root_category = _ROOT_CATEGORY_MAP.get(root or "")
        if mapped_root_category is not None:
            category = mapped_root_category
            subcategory = _category_name(section) if section else None
        elif section is not None:
            category = _category_name(section)
            subcategory = _category_name(subsection) if subsection else None
        elif root is not None:
            category = _category_name(root)
            subcategory = None
        else:
            category = "未分类"
            subcategory = None

        if subcategory == category:
            subcategory = None
        return category, subcategory


__all__ = [
    "ConversionStatistics",
    "ExtractedQuestionDraft",
    "QuestionBankConversionResult",
    "QuestionBankMarkdownConverter",
]
