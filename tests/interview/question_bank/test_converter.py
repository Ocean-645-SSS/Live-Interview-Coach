"""测试 Markdown 题库转换器的确定性提取规则。"""

from pathlib import Path

from liverag.interview.question_bank.converter import QuestionBankMarkdownConverter


def _write_markdown(tmp_path: Path, content: str) -> Path:
    """写入单个测试 Markdown，并返回可传给转换器的路径。"""

    path = tmp_path / "questions.md"
    path.write_text(content, encoding="utf-8")
    return path


def test_converter_ignores_images_and_link_addresses(tmp_path: Path):
    """图片不进入答案，链接只保留可读文字而不保留地址。"""

    path = _write_markdown(
        tmp_path,
        """# RAG开发篇
## 检索
#### 什么是混合检索？
混合检索结合[关键词检索](https://example.com/search)与向量检索。
![](images/retrieval.png)
更多内容见 https://example.com/detail
""",
    )

    result = QuestionBankMarkdownConverter().convert_file(path)

    assert len(result.questions) == 1
    assert "关键词检索" in result.questions[0].reference_answer
    assert "https://" not in result.questions[0].reference_answer
    assert "images/retrieval.png" not in result.questions[0].reference_answer
    assert result.statistics.skipped_images == 1
    assert result.statistics.skipped_external_links == 2


def test_converter_excludes_guidance_deduplicates_and_links_follow_up(tmp_path: Path):
    """指导材料被排除，重复题被合并，追问题指向最近的主问题。"""

    path = _write_markdown(
        tmp_path,
        """# RAG开发篇
## 检索
#### 什么是向量检索？
短答案。
  #### （追问）为什么需要相似度？
相似度用于衡量查询和文档的接近程度。
#### 什么是向量检索？
这是更完整、更长的向量检索参考答案。
# 项目篇
## 项目相关的问题
#### 如何介绍项目？
这属于面试指导，不属于技术题库。
""",
    )

    result = QuestionBankMarkdownConverter().convert_file(path)

    assert len(result.questions) == 2
    main_question = next(
        question for question in result.questions if question.parent_question_id is None
    )
    follow_up = next(
        question for question in result.questions if question.parent_question_id is not None
    )
    assert main_question.reference_answer == "这是更完整、更长的向量检索参考答案。"
    assert follow_up.parent_question_id == main_question.id
    assert main_question.category == "RAG"
    assert main_question.subcategory == "检索"
    assert main_question.source_reference.startswith("questions.md:line-")
    assert result.statistics.duplicate_questions == 1
    assert result.statistics.extracted_follow_ups == 1


def test_study_source_extraction_statistics_remain_stable():
    """真实八股文档的提取数量变化时必须经过人工确认。"""

    repository_root = Path(__file__).resolve().parents[3]
    result = QuestionBankMarkdownConverter().convert_file(
        repository_root / "study_source.md"
    )

    assert result.statistics.extracted_questions == 421
    assert result.statistics.extracted_follow_ups == 21
    assert result.statistics.duplicate_questions == 194
    assert result.statistics.skipped_images == 105
    assert result.statistics.skipped_external_links == 43
    assert result.statistics.skipped_guidance_blocks == 4
    assert result.statistics.skipped_empty_answers == 23
