"""测试离线题库命令的参数和凭据保护。"""

import argparse
from pathlib import Path

import pytest

from liverag.config.settings import VoiceSettings
from liverag.interview.question_bank.cli import (
    QuestionBankCommandError,
    build_from_arguments,
    create_parser,
)


def test_parser_accepts_pilot_selection():
    """小样命令可以指定分类、数量、检查点和输出路径。"""

    arguments = create_parser().parse_args(
        [
            "study_source.md",
            "--category",
            "RAG",
            "--limit",
            "5",
            "--output",
            "rag-sample.json",
            "--checkpoint",
            "rag-sample.checkpoint.json",
        ]
    )

    assert arguments.source == Path("study_source.md")
    assert arguments.category == "RAG"
    assert arguments.limit == 5
    assert arguments.output == Path("rag-sample.json")


async def test_build_rejects_missing_real_model_key():
    """没有模型 Key 时必须停止，不能生成看似正式的假题库。"""

    arguments = argparse.Namespace(
        source=Path("study_source.md"),
        output=Path("unused.json"),
        checkpoint=Path("unused.checkpoint.json"),
        version=1,
        category="RAG",
        limit=5,
    )

    with pytest.raises(QuestionBankCommandError, match=r"缺少.*API_KEY"):
        await build_from_arguments(arguments, voice_settings=VoiceSettings())
