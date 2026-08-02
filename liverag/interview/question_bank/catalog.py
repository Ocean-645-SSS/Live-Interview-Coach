"""结构化面试题库的加载、查询与选题。

题库使用版本化 JSON 文件保存，本模块只负责读取和业务校验。它不会调用 LLM，
也不会把题目放入 RAG。相同题库和相同配置始终得到相同的选题结果，方便测试、复现和审计。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from pydantic import Field, ValidationError, field_validator

from liverag.interview.schemas import (
    InterviewConfig,
    InterviewDifficulty,
    InterviewQuestion,
    QuestionSource,
    QuestionType,
    StrictModel,
)


class QuestionBankError(RuntimeError):
    """题库文件无法读取、内容非法或无法满足选题要求。"""


class QuestionNotFoundError(LookupError):
    """请求的题目标识不存在于当前题库。"""


class QuestionBankDocument(StrictModel):
    """题库 JSON 文件的顶层结构。"""

    #题库版本
    version: int = Field(ge=1, description="题库内容版本，从 1 开始递增")
    #题目列表
    questions: list[InterviewQuestion] = Field(min_length=1)

    @field_validator("questions")   #生成时自动校验question字段
    @classmethod
    def validate_questions(
        cls,
        questions: list[InterviewQuestion],
    ) -> list[InterviewQuestion]:
        """校验题目唯一性、来源信息、分类标签和主问题/追问关系。"""

        question_ids = [question.id for question in questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("题库中的题目标识不能重复")

        normalized_question_texts = [
            " ".join(question.question_text.casefold().split())
            for question in questions
        ]
        if len(normalized_question_texts) != len(set(normalized_question_texts)):
            raise ValueError("题库中不能存在文字完全相同的重复问题")

        #校验题目来源
        invalid_sources = [
            question.id
            for question in questions
            if question.source is not QuestionSource.QUESTION_BANK
        ]
        if invalid_sources:
            joined_ids = ", ".join(invalid_sources)
            raise ValueError(f"题库题目的 source 必须为 QUESTION_BANK：{joined_ids}")

        #校验题目必须包含文字参考答案
        missing_reference_answers = [
            question.id for question in questions if not question.reference_answer
        ]
        if missing_reference_answers:
            joined_ids = ", ".join(missing_reference_answers)
            raise ValueError(f"题库题目必须包含文字参考答案：{joined_ids}")

        #校验题目必须包含原文位置
        missing_source_references = [
            question.id for question in questions if not question.source_reference
        ]
        if missing_source_references:
            joined_ids = ", ".join(missing_source_references)
            raise ValueError(f"题库题目必须包含原文位置：{joined_ids}")

        for question in questions:
            #校验题目分类和主题之间不能重复
            classification_names = {_normalize_topic(question.category)}
            if question.subcategory:
                classification_names.add(_normalize_topic(question.subcategory))
            duplicated_topics = [
                topic
                for topic in question.topics
                if _normalize_topic(topic) in classification_names
            ]
            if duplicated_topics:
                joined_topics = ", ".join(duplicated_topics)
                raise ValueError(
                    f"题目 {question.id} 的 topics 与分类重复：{joined_topics}"
                )

        #校验追问题的 parent_question_id 是否存在且指向主问题
        questions_by_id = {question.id: question for question in questions}
        for question in questions:
            if question.type is QuestionType.FOLLOW_UP:
                if question.parent_question_id is None:
                    raise ValueError(f"追问题必须指定 parent_question_id：{question.id}")
            elif question.parent_question_id is not None:
                raise ValueError(
                    f"只有 FOLLOW_UP 类型题目可以指定 parent_question_id：{question.id}"
                )

            if question.parent_question_id is None:
                continue
            parent = questions_by_id.get(question.parent_question_id)
            if parent is None:
                raise ValueError(
                    f"追问题 {question.id} 引用的主问题不存在："
                    f"{question.parent_question_id}"
                )
            if parent.type is QuestionType.FOLLOW_UP:
                raise ValueError(f"追问题不能继续作为其他追问题的父题：{parent.id}")
        return questions


_DIFFICULTY_ORDER: dict[InterviewDifficulty, int] = {
    InterviewDifficulty.BEGINNER: 0,
    InterviewDifficulty.JUNIOR: 1,
    InterviewDifficulty.INTERMEDIATE: 2,
    InterviewDifficulty.SENIOR: 3,
    InterviewDifficulty.EXPERT: 4,
}

_DEFAULT_SELECTABLE_TYPES: frozenset[QuestionType] = frozenset(
    {
        QuestionType.TECHNICAL_KNOWLEDGE,   #技术知识
        QuestionType.PROJECT_DEEP_DIVE,  #项目深入
        QuestionType.SYSTEM_DESIGN,  #系统设计
        QuestionType.SCENARIO,  #场景题
        QuestionType.BEHAVIORAL,  #行为分析
    }
)


def _normalize_topic(topic: str) -> str:
    """统一主题的大小写和首尾空格，供匹配使用但不修改展示文本。"""

    return topic.strip().casefold()


def _difficulty_distance(
    actual: InterviewDifficulty,
    expected: InterviewDifficulty,
) -> int:
    """计算题目难度和目标难度之间相隔的等级数量。"""

    return abs(_DIFFICULTY_ORDER[actual] - _DIFFICULTY_ORDER[expected])


class QuestionBank:
    """提供只读、确定性的结构化题库访问能力。"""

    def __init__(self, document: QuestionBankDocument):
        """保存已校验的题库，并建立按 ID 查询的内存索引。"""

        self._document = document
        self._questions_by_id = {
            question.id: question for question in document.questions
        }

    @classmethod
    def from_file(cls, path: Path) -> QuestionBank:
        """从 UTF-8 JSON 文件加载并完整校验题库。
        Args:path: 题库 JSON 文件路径。
        Returns:可供查询和选题的只读 `QuestionBank`。

        Raises:
            QuestionBankError: 文件不存在、不是合法 UTF-8/JSON，或者内容不符合
                `QuestionBankDocument` 和 `InterviewQuestion` 的规则时抛出。
        """

        expanded_path = path.expanduser()

        #读取文件内容并解析为 JSON
        try:
            raw_text = expanded_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise QuestionBankError(f"题库文件不存在：{expanded_path}") from exc
        except (OSError, UnicodeError) as exc:
            raise QuestionBankError(f"无法读取 UTF-8 题库：{expanded_path}") from exc

        #解析 JSON 并校验内容
        try:
            raw_data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise QuestionBankError(
                f"题库不是合法 JSON：第 {exc.lineno} 行，第 {exc.colno} 列"
            ) from exc

        #校验题库内容
        try:
            document = QuestionBankDocument.model_validate(raw_data)
        except ValidationError as exc:
            raise QuestionBankError(f"题库内容校验失败：{exc}") from exc

        return cls(document)

    @property
    def version(self) -> int:
        """返回题库内容版本。"""

        return self._document.version

    @property
    def size(self) -> int:
        """返回题库中的题目总数。"""

        return len(self._document.questions)

    def get_question(self, question_id: str) -> InterviewQuestion:
        """按稳定 ID 返回题目，不存在时抛出明确异常。"""

        try:
            return self._questions_by_id[question_id]
        except KeyError as exc:
            raise QuestionNotFoundError(f"题库中不存在题目：{question_id}") from exc

    def list_topics(self) -> list[str]:
        """返回题库中不区分大小写去重后的全部主题。"""

        display_by_normalized_topic: dict[str, str] = {}
        for question in self._document.questions:
            for topic in question.topics:
                normalized_topic = _normalize_topic(topic)
                display_by_normalized_topic.setdefault(normalized_topic, topic)
        return sorted(display_by_normalized_topic.values(), key=str.casefold)

    def list_categories(self) -> list[str]:
        """返回题库中不区分大小写去重后的一级分类。"""

        display_by_normalized_category: dict[str, str] = {}
        for question in self._document.questions:
            normalized_category = _normalize_topic(question.category)
            display_by_normalized_category.setdefault(
                normalized_category,
                question.category,
            )
        return sorted(display_by_normalized_category.values(), key=str.casefold)

    def list_subcategories(self, category: str | None = None) -> list[str]:
        """返回全部二级分类，指定一级分类时只返回其下的二级分类。"""

        normalized_category = _normalize_topic(category) if category else None
        display_by_normalized_subcategory: dict[str, str] = {}
        for question in self._document.questions:
            if question.subcategory is None:
                continue
            if (
                normalized_category is not None
                and _normalize_topic(question.category) != normalized_category
            ):
                continue
            normalized_subcategory = _normalize_topic(question.subcategory)
            display_by_normalized_subcategory.setdefault(
                normalized_subcategory,
                question.subcategory,
            )
        return sorted(display_by_normalized_subcategory.values(), key=str.casefold)

    def list_follow_ups(self, parent_question_id: str) -> list[InterviewQuestion]:
        """返回某道主问题预先定义的追问题，并按题库顺序排列。"""

        parent = self.get_question(parent_question_id)
        if parent.type is QuestionType.FOLLOW_UP:
            raise ValueError("不能查询追问题自身的子追问")
        return [
            question
            for question in self._document.questions
            if question.parent_question_id == parent_question_id
        ]

    def filter_questions(
        self,
        *,
        categories: Iterable[str] | None = None,
        subcategories: Iterable[str] | None = None,
        topics: Iterable[str] | None = None,
        difficulties: Iterable[InterviewDifficulty] | None = None,
        question_types: Iterable[QuestionType] | None = None,
    ) -> list[InterviewQuestion]:
        """按分类、主题、难度和类型筛选题目，多个条件之间使用 AND。

        同一条件中的多个值使用 OR。例如 categories 为 RAG 和 Agent 时，题目
        只需属于其中一个分类；如果同时指定 difficulty，则还必须匹配该难度。
        """

        normalized_categories = (
            {_normalize_topic(category) for category in categories}
            if categories is not None
            else None
        )
        normalized_subcategories = (
            {_normalize_topic(subcategory) for subcategory in subcategories}
            if subcategories is not None
            else None
        )
        normalized_topics = (
            {_normalize_topic(topic) for topic in topics}
            if topics is not None
            else None
        )
        difficulty_set = set(difficulties) if difficulties is not None else None
        question_type_set = set(question_types) if question_types is not None else None

        result: list[InterviewQuestion] = []
        for question in self._document.questions:
            #检查一级分类
            if (
                normalized_categories is not None
                and _normalize_topic(question.category) not in normalized_categories
            ):
                continue
            #检查二级分类
            if normalized_subcategories is not None:
                if question.subcategory is None:
                    continue
                if (
                    _normalize_topic(question.subcategory)
                    not in normalized_subcategories
                ):
                    continue
            #检查主题
            question_topics = {_normalize_topic(topic) for topic in question.topics}
            #如果指定了主题过滤条件，则题目至少需要包含其中一个主题才能通过筛选（&取交集）
            if normalized_topics is not None and not question_topics & normalized_topics:
                continue
            #检查难度
            if difficulty_set is not None and question.difficulty not in difficulty_set:
                continue
            #检查问题类型
            if question_type_set is not None and question.type not in question_type_set:
                continue

            #一级标题+二级标题+主题+难度+问题类型都匹配，才加入结果
            result.append(question)
        return result

    def select_questions(self, config: InterviewConfig) -> list[InterviewQuestion]:
        """根据面试配置确定性选择题目并重新生成连续执行顺序。

        排序优先级：匹配配置主题的总权重 > 接近目标难度 > 题目稳定 ID。
        开场和动态追问题不会进入固定题目列表。题目不足时明确报错，不允许
        Planner 悄悄生成少于配置数量的计划。
        """

        #先根据问题五大类型筛选出可选题目
        candidates = self.filter_questions(question_types=_DEFAULT_SELECTABLE_TYPES)
        if len(candidates) < config.question_count:
            raise QuestionBankError(
                "可选题目不足："
                f"需要 {config.question_count} 道，题库只有 {len(candidates)} 道"
            )

        # 每个主题的归一化权重，便于后续按题目匹配主题的总权重排序
        normalized_weights = {
            _normalize_topic(topic): weight
            for topic, weight in config.topic_weights.items()
        }

        def selection_key(question: InterviewQuestion) -> tuple[float, int, str]:
            """生成稳定排序键，分类/主题得分越高、难度距离越小越优先
            如果两者相同，按题目 ID 排序保证稳定性。"""

            #收集题目所有匹配标签
            selection_labels = {
                _normalize_topic(question.category),
                *(_normalize_topic(topic) for topic in question.topics),
            }

            #加入二级分类
            if question.subcategory:
                selection_labels.add(_normalize_topic(question.subcategory))

            #计算题目匹配配置主题的总权重
            topic_score = sum(
                normalized_weights.get(label, 0.0) for label in selection_labels
            )

            #计算题目难度和配置目标难度之间的等级距离
            difficulty_distance = _difficulty_distance(
                question.difficulty,
                config.difficulty,
            )

            #返回排序键：匹配主题总权重越高越优先（负值表示降序），难度距离越小越优先，题目 ID 保证稳定性
            return (-topic_score, difficulty_distance, question.id)

        #排序问题（sort默认从小到大排序），并且截断多余问题
        selected = sorted(candidates, key=selection_key)[: config.question_count]
        return [
            question.model_copy(update={"order": index})
            for index, question in enumerate(selected, start=1)
        ]


__all__ = [
    "QuestionBank",
    "QuestionBankDocument",
    "QuestionBankError",
    "QuestionNotFoundError",
]
