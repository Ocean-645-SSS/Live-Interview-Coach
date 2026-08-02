"""测试题库构建器的检查点恢复、源文件保护和 JSON 输出。"""

from pathlib import Path

import pytest

from liverag.interview.question_bank.builder import (
    QuestionBankBuilder,
    QuestionBankBuildError,
)
from liverag.interview.question_bank.catalog import QuestionBank
from liverag.interview.question_bank.converter import (
    ExtractedQuestionDraft,
    QuestionBankMarkdownConverter,
)
from liverag.interview.question_bank.enricher import (
    QuestionBankEnricher,
    QuestionEnrichment,
)
from liverag.interview.schemas import (
    InterviewDifficulty,
    QuestionType,
    RubricPoint,
)


class CountingProvider:
    """记录收到的题目，并可在指定调用次数模拟模型故障。"""

    def __init__(self, *, fail_on_call: int | None = None):
        self.fail_on_call = fail_on_call
        self.seen_ids: list[str] = []

    async def enrich(self, draft: ExtractedQuestionDraft) -> QuestionEnrichment:
        """返回与分类不冲突的固定补全结果。"""

        self.seen_ids.append(draft.id)
        if self.fail_on_call == len(self.seen_ids):
            raise RuntimeError("模拟模型调用失败")
        return QuestionEnrichment(
            difficulty=InterviewDifficulty.INTERMEDIATE,
            question_type=QuestionType.TECHNICAL_KNOWLEDGE,
            topics=["核心原理"],
            objective="验证候选人是否理解核心原理",
            expected_points=[
                RubricPoint(id="concept", content="说明核心概念", required=True),
                RubricPoint(id="reasoning", content="说明背后原因"),
            ],
        )


def _write_two_questions(tmp_path: Path) -> Path:
    """写入两道能被真实转换器识别的最小 Markdown。"""

    path = tmp_path / "questions.md"
    path.write_text(
        """# RAG开发篇
## 检索
#### 什么是召回？
召回从语料中找到候选文档。
#### 为什么需要重排？
重排进一步判断候选文档的相关性。
""",
        encoding="utf-8",
    )
    return path


async def test_builder_resumes_after_provider_failure(tmp_path: Path):
    """首轮第二题失败后，第二轮只补全尚未完成的第二题。"""

    source_path = _write_two_questions(tmp_path)
    checkpoint_path = tmp_path / "checkpoint.json"
    failing_provider = CountingProvider(fail_on_call=2)
    first_builder = QuestionBankBuilder(
        QuestionBankMarkdownConverter(),
        QuestionBankEnricher(failing_provider),
    )

    with pytest.raises(RuntimeError, match="模拟模型调用失败"):
        await first_builder.build(
            source_path,
            bank_version=1,
            checkpoint_path=checkpoint_path,
        )

    assert checkpoint_path.is_file()
    assert len(failing_provider.seen_ids) == 2

    resumed_provider = CountingProvider()
    resumed_builder = QuestionBankBuilder(
        QuestionBankMarkdownConverter(),
        QuestionBankEnricher(resumed_provider),
    )
    result = await resumed_builder.build(
        source_path,
        bank_version=1,
        checkpoint_path=checkpoint_path,
    )

    assert result.resumed_question_count == 1
    assert len(resumed_provider.seen_ids) == 1
    assert len(result.document.questions) == 2


async def test_builder_rejects_checkpoint_after_source_changes(tmp_path: Path):
    """Markdown 内容变化后禁止继续使用旧检查点。"""

    source_path = _write_two_questions(tmp_path)
    checkpoint_path = tmp_path / "checkpoint.json"
    builder = QuestionBankBuilder(
        QuestionBankMarkdownConverter(),
        QuestionBankEnricher(CountingProvider()),
    )
    await builder.build(
        source_path,
        bank_version=1,
        checkpoint_path=checkpoint_path,
    )
    source_path.write_text(
        source_path.read_text(encoding="utf-8") + "\n补充说明。\n",
        encoding="utf-8",
    )

    with pytest.raises(QuestionBankBuildError, match="源 Markdown 已变化"):
        await builder.build(
            source_path,
            bank_version=1,
            checkpoint_path=checkpoint_path,
        )


async def test_write_document_can_be_loaded_by_catalog(tmp_path: Path):
    """构建器写出的最终 JSON 必须能被运行时 Catalog 完整加载。"""

    builder = QuestionBankBuilder(
        QuestionBankMarkdownConverter(),
        QuestionBankEnricher(CountingProvider()),
    )
    result = await builder.build(
        _write_two_questions(tmp_path),
        bank_version=1,
    )
    output_path = tmp_path / "question_bank.v1.json"

    builder.write_document(result.document, output_path)
    loaded = QuestionBank.from_file(output_path)

    assert loaded.version == 1
    assert loaded.size == 2
