"""Session-specific ASR hot-word selection tests."""

from __future__ import annotations

import json

from liverag.agent.hot_words import (
    HotWordEntry,
    _parse_hot_word_entries_md,
    load_hot_word_entries,
    load_hot_words,
    select_session_hot_words,
    serialize_hot_words,
)
from liverag.interview.schemas import (
    InterviewConfig,
    InterviewDifficulty,
    InterviewPlan,
    InterviewQuestion,
    QuestionRubric,
    QuestionSource,
    QuestionType,
    RubricPoint,
)


def _plan() -> InterviewPlan:
    return InterviewPlan(
        id="plan-1",
        title="后端面试",
        introduction="开始面试。",
        config=InterviewConfig(question_count=1, topic_weights={"backend": 1.0}),
        questions=[
            InterviewQuestion(
                id="question-1",
                order=1,
                type=QuestionType.TECHNICAL_KNOWLEDGE,
                source=QuestionSource.QUESTION_BANK,
                difficulty=InterviewDifficulty.INTERMEDIATE,
                category="backend",
                topics=["Kafka", "消息队列"],
                question_text="请说明 Kafka 消息积压时的处理方案。",
                objective="考察消息队列的工程实践。",
                rubric=QuestionRubric(
                    expected_points=[
                        RubricPoint(id="kafka", content="说明 Kafka 消费者扩容")
                    ]
                ),
            )
        ],
        closing_message="面试结束。",
    )


def test_parser_accepts_legacy_and_metadata_entries() -> None:
    entries = _parse_hot_word_entries_md(
        "Agent|10\nKafka|9|backend、middleware|卡夫卡|卡夫卡\n"
    )

    assert entries == [
        HotWordEntry(word="Agent", level=10),
        HotWordEntry(
            word="Kafka",
            level=9,
            domains=("backend", "middleware"),
            aliases=("卡夫卡",),
            misrecognitions=("卡夫卡",),
        ),
    ]

    cot = _parse_hot_word_entries_md("CoT|8|llm|Chain of Thought|c,o,t、cot\n")

    assert cot[0].misrecognitions == ("c,o,t", "cot")


def test_session_selection_reads_all_valid_entries_while_default_loading_stays_compatible(
    tmp_path,
) -> None:
    path = tmp_path / "hot_words.md"
    path.write_text(
        "\n".join(f"word-{index}|8" for index in range(101)),
        encoding="utf-8",
    )

    entries = load_hot_word_entries(path)
    default_payload = json.loads(load_hot_words(path))

    assert len(entries) == 101
    assert entries[-1] == HotWordEntry(word="word-100", level=8)
    assert len(default_payload["hotwords"]) == 100


def test_selector_prioritizes_plan_matches_then_appends_fixed_core_words() -> None:
    entries = [
        HotWordEntry(word="Kafka", level=10, domains=("backend",)),
        HotWordEntry(word="Redis", level=9, domains=("backend",)),
        HotWordEntry(word="Agent", level=10),
        HotWordEntry(word="LLM", level=10),
    ]

    selected = select_session_hot_words(
        _plan(),
        entries,
        min_words=2,
        max_words=3,
        fixed_core_words=("Agent", "LLM"),
    )

    assert [entry.word for entry in selected] == ["Kafka", "Agent", "LLM"]


def test_serializer_only_includes_canonical_words_and_levels() -> None:
    payload = json.loads(
        serialize_hot_words(
            [
                HotWordEntry(
                    word="Kafka",
                    level=10,
                    domains=("backend",),
                    aliases=("卡夫卡",),
                    misrecognitions=("卡夫卡",),
                )
            ]
        )
    )

    assert payload == {"hotwords": [{"word": "Kafka", "level": 10}]}
