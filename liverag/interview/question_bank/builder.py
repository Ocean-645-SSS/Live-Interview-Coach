"""把 enricher.py 生成的题目草稿构建成完整、可恢复的结构化题库。

这个模块只在离线生成题库时使用，不进入实时面试链路。构建过程中每成功
补全一道题就保存一次检查点；如果模型调用中途失败，下次可以继续处理，
不需要重新消耗已经完成题目的模型调用。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field, ValidationError, model_validator

from liverag.interview.question_bank.catalog import QuestionBankDocument
from liverag.interview.question_bank.converter import (
    ConversionStatistics,
    ExtractedQuestionDraft,
    QuestionBankMarkdownConverter,
)
from liverag.interview.question_bank.enricher import QuestionBankEnricher
from liverag.interview.schemas import InterviewQuestion, StrictModel


class QuestionBankBuildError(RuntimeError):
    """题库构建输入、检查点或输出不满足要求时抛出的统一异常。"""


class QuestionBankBuildCheckpoint(StrictModel):
    """记录一次未完成构建中已经成功补全的题目。

    `source_sha256` 用于判断 Markdown 是否在两次运行之间发生变化。源文档
    改变后不能继续使用旧检查点，否则题目顺序和父子关系可能不再一致。
    """

    source_sha256: str = Field(min_length=64, max_length=64)
    bank_version: int = Field(ge=1)
    total_questions: int = Field(ge=1)
    completed_questions: list[InterviewQuestion] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_progress(self) -> QuestionBankBuildCheckpoint:
        """保证检查点数量合理，且已完成题目的执行顺序连续。"""

        if len(self.completed_questions) > self.total_questions:
            raise ValueError("检查点中的已完成题目数不能超过题目总数")

        actual_orders = [question.order for question in self.completed_questions]
        expected_orders = list(range(1, len(self.completed_questions) + 1))
        if actual_orders != expected_orders:
            raise ValueError("检查点中的题目顺序必须从 1 开始连续递增")
        return self


@dataclass(frozen=True, slots=True)
class QuestionBankBuildResult:
    """返回构建完成的题库以及 Markdown 转换统计。"""

    document: QuestionBankDocument
    conversion_statistics: ConversionStatistics
    resumed_question_count: int


def _calculate_sha256(path: Path) -> str:
    """计算源 Markdown 的 SHA-256，用于识别源文件是否变化。"""

    digest = hashlib.sha256()
    with path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_text_atomically(path: Path, content: str) -> None:
    """先写同目录临时文件，再替换目标文件，避免留下半截 JSON。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.tmp")
    temporary_path.write_text(content, encoding="utf-8")
    temporary_path.replace(path)


class QuestionBankBuilder:
    """协调 Markdown 转换器、LLM 补全器和题库整体验证。"""

    def __init__(
        self,
        converter: QuestionBankMarkdownConverter,
        enricher: QuestionBankEnricher,
    ):
        """注入转换器和补全器，便于测试时替换其中任一环节。"""

        self._converter = converter
        self._enricher = enricher

    async def build(
        self,
        source_path: Path,
        *,
        bank_version: int,
        checkpoint_path: Path | None = None,
        category: str | None = None,
        question_limit: int | None = None,
    ) -> QuestionBankBuildResult:
        """抽取并逐题补全 Markdown，最终返回经过整库校验的题库。

        Args:
            source_path: 只包含文字题库内容的 Markdown 文件。
            bank_version: 本次生成的题库版本号。
            checkpoint_path: 可选检查点路径；提供后才启用断点保存和恢复。
            category: 可选一级分类；用于先构建一个分类的小样。
            question_limit: 可选题目上限；用于控制小样的模型调用次数。

        Returns:
            完整题库、转换统计以及本次恢复使用的题目数量。

        Raises:
            QuestionBankBuildError: 输入非法、检查点过期或整库校验失败。
            QuestionEnrichmentError: 某道题调用模型或结果校验失败。

        主流程：
        题目草稿 = converter.convert_file(Markdown)

        已经完成的题目 = 读取检查点()

        for 尚未完成的草稿 in 题目草稿:
            正式题目 = await enricher.enrich_draft(草稿)
            已经完成的题目.append(正式题目)
            保存检查点()

        完整题库 = QuestionBankDocument(已经完成的题目)

        return 完整题库
        """

        expanded_source = source_path.expanduser()

        #检查题库版本
        if bank_version < 1:
            raise QuestionBankBuildError("题库版本必须从 1 开始")
        #检查源 Markdown 文件是否存在
        if not expanded_source.is_file():
            raise QuestionBankBuildError(f"Markdown 题库文件不存在：{expanded_source}")

        #转换 Markdown 为题目草稿
        conversion = self._converter.convert_file(expanded_source)
        if not conversion.questions:
            raise QuestionBankBuildError("Markdown 中没有提取到可用题目")
        selected_drafts = self._select_drafts(
            conversion.questions,
            category=category,
            question_limit=question_limit,
        )
        if not selected_drafts:
            raise QuestionBankBuildError("当前分类和数量条件下没有可构建的题目")

        #计算一个内容指纹
        source_sha256 = _calculate_sha256(expanded_source)
        #恢复检查点
        completed_questions = self._load_completed_questions(
            checkpoint_path=checkpoint_path,
            source_sha256=source_sha256,
            bank_version=bank_version,
            total_questions=len(selected_drafts),
        )
        #已经完成的题目数量，用于返回给调用方，便于统计和日志
        resumed_question_count = len(completed_questions)

        #校验检查点题目正好对应当前草稿开头，防止错位续跑
        self._validate_checkpoint_prefix(
            completed_questions,
            draft_ids=[draft.id for draft in selected_drafts],
        )

        #逐题补全剩余草稿，遇到模型调用失败或结果校验失败会抛出异常
        for order, draft in enumerate(
            selected_drafts[resumed_question_count:],
            start=resumed_question_count + 1,
        ):
            #补全题目草稿，得到完整题目
            question = await self._enricher.enrich_draft(draft, order=order)
            completed_questions.append(question)
            #保存检查点，便于下次继续构建
            if checkpoint_path is not None:
                self._save_checkpoint(
                    checkpoint_path,
                    source_sha256=source_sha256,
                    bank_version=bank_version,
                    total_questions=len(selected_drafts),
                    completed_questions=completed_questions,
                )

        try:
            #把所有题目组合成完整题库，并进行整库校验
            document = QuestionBankDocument(
                version=bank_version,
                questions=completed_questions,
            )
        except ValidationError as exc:
            raise QuestionBankBuildError(f"完整题库校验失败：{exc}") from exc

        return QuestionBankBuildResult(
            document=document,
            conversion_statistics=conversion.statistics,
            resumed_question_count=resumed_question_count,
        )

    @staticmethod
    def _select_drafts(
        drafts: tuple[ExtractedQuestionDraft, ...],
        *,
        category: str | None,
        question_limit: int | None,
    ) -> list[ExtractedQuestionDraft]:
        """按一级分类和数量选择草稿，同时避免选出没有父题的追问题。"""

        if question_limit is not None and question_limit < 1:
            raise QuestionBankBuildError("题目上限必须大于或等于 1")

        normalized_category = category.strip().casefold() if category else None
        selected: list[ExtractedQuestionDraft] = []
        selected_ids: set[str] = set()
        for draft in drafts:
            if (
                normalized_category is not None
                and draft.category.casefold() != normalized_category
            ):
                continue
            if (
                draft.parent_question_id is not None
                and draft.parent_question_id not in selected_ids
            ):
                continue

            selected.append(draft)
            selected_ids.add(draft.id)
            if question_limit is not None and len(selected) >= question_limit:
                break
        return selected

    @staticmethod
    def write_document(document: QuestionBankDocument, output_path: Path) -> None:
        """把校验完成的题库写成 UTF-8 JSON，不接受未校验的普通字典。"""

        content = document.model_dump_json(indent=2) + "\n"
        _write_text_atomically(output_path.expanduser(), content)

    @staticmethod
    def _load_completed_questions(
        *,
        checkpoint_path: Path | None,
        source_sha256: str,
        bank_version: int,
        total_questions: int,
    ) -> list[InterviewQuestion]:
        """读取检查点，并确认它属于当前源文件和题库版本。"""

        if checkpoint_path is None:
            return []

        expanded_path = checkpoint_path.expanduser()
        if not expanded_path.exists():
            return []

        try:
            checkpoint = QuestionBankBuildCheckpoint.model_validate_json(
                expanded_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ValidationError) as exc:
            raise QuestionBankBuildError(f"无法读取题库构建检查点：{exc}") from exc

        if checkpoint.source_sha256 != source_sha256:
            raise QuestionBankBuildError("源 Markdown 已变化，不能继续使用旧检查点")
        if checkpoint.bank_version != bank_version:
            raise QuestionBankBuildError("检查点的题库版本与本次构建版本不一致")
        if checkpoint.total_questions != total_questions:
            raise QuestionBankBuildError("检查点记录的题目总数与本次转换结果不一致")
        return list(checkpoint.completed_questions)

    @staticmethod
    def _validate_checkpoint_prefix(
        completed_questions: list[InterviewQuestion],
        *,
        draft_ids: list[str],
    ) -> None:
        """保证检查点题目正好对应当前草稿开头，防止错位续跑。"""

        completed_ids = [question.id for question in completed_questions]
        if completed_ids != draft_ids[: len(completed_ids)]:
            raise QuestionBankBuildError("检查点题目与当前 Markdown 的题目顺序不一致")

    @staticmethod
    def _save_checkpoint(
        checkpoint_path: Path,
        *,
        source_sha256: str,
        bank_version: int,
        total_questions: int,
        completed_questions: list[InterviewQuestion],
    ) -> None:
        """保存当前成功进度；下一次构建会从列表末尾继续。"""

        checkpoint = QuestionBankBuildCheckpoint(
            source_sha256=source_sha256,
            bank_version=bank_version,
            total_questions=total_questions,
            completed_questions=completed_questions,
        )
        content = checkpoint.model_dump_json(indent=2) + "\n"
        _write_text_atomically(checkpoint_path.expanduser(), content)


__all__ = [
    "QuestionBankBuildCheckpoint",
    "QuestionBankBuildError",
    "QuestionBankBuildResult",
    "QuestionBankBuilder",
]
