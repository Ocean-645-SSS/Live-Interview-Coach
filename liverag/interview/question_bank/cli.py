"""离线构建 Interview Coach 结构化题库的命令行入口。
cli.py -> converter.py -> enricher.py -> builder.py
catalog.py 用于在线筛选题库"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from liverag.config.settings import (
    VoiceSettings,
    load_environment,
    load_voice_settings,
)
from liverag.interview.question_bank.builder import (
    QuestionBankBuilder,
    QuestionBankBuildResult,
)
from liverag.interview.question_bank.converter import QuestionBankMarkdownConverter
from liverag.interview.question_bank.enricher import (
    OpenAIQuestionEnrichmentProvider,
    OpenAIQuestionEnrichmentSettings,
    QuestionBankEnricher,
    QuestionEnrichmentError,
)


class QuestionBankCommandError(RuntimeError):
    """题库生成命令缺少配置或无法完成构建时抛出的异常。"""


def _default_output_path() -> Path:
    """返回题库包内部的 V1 默认 JSON 输出位置。"""

    return Path(__file__).resolve().parent / "data" / "question_bank.v1.json"


def create_parser() -> argparse.ArgumentParser:
    """创建参数解析器，集中说明所有离线题库构建选项。"""

    parser = argparse.ArgumentParser(description="从 Markdown 构建结构化面试题库")
    parser.add_argument("source", type=Path, help="原始 Markdown 题库路径")
    #输出位置
    parser.add_argument(
        "--output",
        type=Path,
        default=_default_output_path(),
        help="完整校验通过后的题库 JSON 输出路径",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=Path(".question-bank-build.checkpoint.json"),
        help="逐题保存的断点文件路径",
    )
    parser.add_argument("--version", type=int, default=1, help="题库内容版本")
    parser.add_argument("--category", help="只构建指定一级分类，例如 RAG")
    parser.add_argument("--limit", type=int, help="最多构建多少道题，用于小样检查")
    return parser


async def build_from_arguments(
    arguments: argparse.Namespace,
    *,
    voice_settings: VoiceSettings | None = None,
) -> QuestionBankBuildResult:
    """生成题库的最终函数：根据命令参数创建真实模型 Provider，并执行题库构建流程。
    QuestionBankBuilder.build() 负责流程编排
    Converter.convert_file() 负责抽取原始内容
    Provider.enrich() 负责调用真实模型补全缺失字段
    Enricher.enrich_many() 负责合并成最终题目
    """

    settings = voice_settings
    if settings is None:
        load_environment()
        settings = load_voice_settings()
    if not settings.llm_api_key.strip():
        raise QuestionBankCommandError(
            "缺少 VOICE_LLM_API_KEY 或 DASHSCOPE_API_KEY，不能调用真实模型"
        )

    provider_settings = OpenAIQuestionEnrichmentSettings.from_voice_settings(settings)
    builder = QuestionBankBuilder(
        QuestionBankMarkdownConverter(),
        QuestionBankEnricher(OpenAIQuestionEnrichmentProvider(provider_settings)),
    )
    result = await builder.build(
        arguments.source,
        bank_version=arguments.version,
        checkpoint_path=arguments.checkpoint,
        category=arguments.category,
        question_limit=arguments.limit,
    )
    # 将最终题库写入指定输出路径
    builder.write_document(result.document, arguments.output)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """运行构建命令，并向终端输出不包含密钥的结果摘要。"""

    # 解析命令行参数
    arguments = create_parser().parse_args(argv)
    try:
        # 运行异步构建流程
        result = asyncio.run(build_from_arguments(arguments))
    except (QuestionBankCommandError, QuestionEnrichmentError, OSError, ValueError) as exc:
        print(f"题库构建失败：{exc}")
        return 1

    # 输出结果摘要
    statistics = result.conversion_statistics
    print(
        "题库构建完成："
        f"输出 {len(result.document.questions)} 道，"
        f"恢复 {result.resumed_question_count} 道，"
        f"Markdown 共提取 {statistics.extracted_questions} 道"
    )
    return 0


__all__ = [
    "QuestionBankCommandError",
    "build_from_arguments",
    "create_parser",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
