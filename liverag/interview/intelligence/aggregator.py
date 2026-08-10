"""面试情报聚合器。

将 NormalizedInterviewExperience[] 聚合为 CompanyInterviewProfile，供 Planner 使用。
聚合规则全部确定性、可解释，不使用 LLM。

流程：
aggregate(experiences, company, role, region)
   ↓
_deduplicate()          — 两层去重：source_id + content_hash
   ↓
_compute_topic_frequencies()  — Counter 统计 topic 频次
   ↓
_extract_representative_questions() — Counter 统计 question 频次，top 10
   ↓
_analyze_round_patterns() — 按轮次分组，提取各轮次常见 topic / question
   ↓
_compute_snapshot_hash() — 确定性 SHA-256 用于缓存失效
   ↓
CompanyInterviewProfile
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from liverag.interview.intelligence.provider import (
    CompanyInterviewProfile,
    EvidenceRef,
    InterviewRound,
    NormalizedInterviewExperience,
    RoundPattern,
    TopicFrequency,
)

# ====================== 去重 ======================

def _deduplicate(
    experiences: list[NormalizedInterviewExperience],
) -> list[NormalizedInterviewExperience]:
    """两层去重。

    第一层：按 (provider, source_id) 去重，保留首次出现。
    第二层：在 source_id 不同的记录中，按 content_hash 去重，保留首次出现。
    """

    # 第一层：source_id 去重
    seen_ids: set[tuple[str, str]] = set()
    deduped: list[NormalizedInterviewExperience] = []
    for exp in experiences:
        key = (exp.provider, exp.source_id)
        if key not in seen_ids:
            seen_ids.add(key)
            deduped.append(exp)

    # 第二层：content_hash 去重（跨 source_id）
    seen_hashes: set[str] = set()
    result: list[NormalizedInterviewExperience] = []
    for exp in deduped:
        if not exp.content_hash:
            result.append(exp)
        elif exp.content_hash not in seen_hashes:
            seen_hashes.add(exp.content_hash)
            result.append(exp)

    return result


# ====================== 主题频次 ======================

def _compute_topic_frequencies(
    experiences: list[NormalizedInterviewExperience],
    max_topics: int = 15,
) -> list[TopicFrequency]:
    """统计主题频次。

    汇总所有面经的 topics 字段，按出现次数降序排列。
    ratio = 该 topic 出现次数 / 总面经数。
    """

    total = len(experiences)
    if total == 0:
        return []

    counter: Counter[str] = Counter()
    for exp in experiences:
        for topic in exp.topics:
            counter[topic] += 1

    result: list[TopicFrequency] = []
    for topic, count in counter.most_common(max_topics):
        result.append(
            TopicFrequency(
                topic=topic,
                count=count,
                ratio=round(count / total, 3),
            )
        )

    return result


# ====================== 代表性题目 ======================

def _extract_representative_questions(
    experiences: list[NormalizedInterviewExperience],
    max_questions: int = 10,
) -> list[str]:
    """提取代表性题目。
    汇总所有面经的 questions 字段，按出现次数降序，取 top N。
    """

    if not experiences:
        return []

    counter: Counter[str] = Counter()
    for exp in experiences:
        for q in exp.questions:
            counter[q] += 1

    return [q for q, _ in counter.most_common(max_questions)]


# ====================== 轮次模式分析 ======================

def _analyze_round_patterns(
    experiences: list[NormalizedInterviewExperience],
    max_topics_per_round: int = 5,
    max_questions_per_round: int = 5,
) -> list[RoundPattern]:
    """按面试轮次分组，提取各轮次常见考察模式。

    只对有明确 interview_round 的记录进行分组。
    """

    if not experiences:
        return []

    # 按轮次分组
    groups: dict[InterviewRound, list[NormalizedInterviewExperience]] = {}
    for exp in experiences:
        if exp.interview_round is not None:
            groups.setdefault(exp.interview_round, []).append(exp)

    round_order = [
        InterviewRound.FIRST,
        InterviewRound.SECOND,
        InterviewRound.THIRD,
        InterviewRound.FINAL,
        InterviewRound.HR,
    ]

    patterns: list[RoundPattern] = []
    for rd in round_order:
        exps = groups.get(rd, [])
        if not exps:
            continue

        # 统计该轮次内 topic 频次
        topic_counter: Counter[str] = Counter()
        question_counter: Counter[str] = Counter()
        for exp in exps:
            for topic in exp.topics:
                topic_counter[topic] += 1
            for q in exp.questions:
                question_counter[q] += 1

        common_topics = [t for t, _ in topic_counter.most_common(max_topics_per_round)]
        common_questions = [q for q, _ in question_counter.most_common(max_questions_per_round)]

        # 生成描述文本
        topic_desc = "、".join(common_topics) if common_topics else "暂无数据"
        question_desc = "；".join(common_questions) if common_questions else "暂无数据"
        description = f"该轮次常见考察主题: {topic_desc}。典型题目: {question_desc}"

        patterns.append(
            RoundPattern(
                round=rd,
                common_topics=common_topics,
                common_questions=common_questions,
                description=description,
            )
        )

    return patterns


# ====================== 快照哈希 ======================

def _compute_snapshot_hash(profile: CompanyInterviewProfile) -> str:
    """计算 CompanyInterviewProfile 的确定性快照哈希。

    只对影响语义的字段做哈希，排除 generated_at、snapshot_hash 自身。
    用于缓存失效判断。
    """

    # 调出重要语义字段
    canonical: dict[str, Any] = {
        "company": profile.company,
        "role": profile.role,
        "region": profile.region,
        "sample_count": profile.sample_count,
        "usable_sample_count": profile.usable_sample_count,
        #统一排序
        "top_topics": sorted(
            [
                {"topic": t.topic, "count": t.count, "ratio": t.ratio}
                for t in profile.top_topics
            ],
            key=lambda x: (-x["count"], x["topic"]),
        ),
        "representative_questions": sorted(profile.representative_questions),
        "round_patterns": sorted(
            [
                {
                    "round": p.round.value if p.round else None,
                    "common_topics": sorted(p.common_topics),
                    "common_questions": sorted(p.common_questions),
                }
                for p in profile.round_patterns
            ],
            key=lambda x: (x["round"] or ""),
        ),
        "evidence_refs": sorted(
            [
                {"provider": e.provider, "source_id": e.source_id, "content_hash": e.content_hash}
                for e in profile.evidence_refs
            ],
            key=lambda x: (x["provider"], x["source_id"]),
        ),
    }
    #转JSON字符串
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True)

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ====================== 主聚合函数 ======================

def aggregate(
    experiences: list[NormalizedInterviewExperience],
    company: str = "",
    role: str = "",
    region: str = "",
) -> CompanyInterviewProfile:
    """将 NormalizedInterviewExperience[] 聚合为 CompanyInterviewProfile。

    聚合流程：
    1. 两层去重
    2. 统计 TopicFrequency[]
    3. 提取 representative_questions
    4. 分析 round_patterns
    5. 构建 evidence_refs
    6. 计算 snapshot_hash
    """

    # 1. 去重
    deduped = _deduplicate(experiences)

    # 2. 统计主题频次
    top_topics = _compute_topic_frequencies(deduped)

    # 3. 提取代表性题目
    representative_questions = _extract_representative_questions(deduped)

    # 4. 根据轮次模式分析
    round_patterns = _analyze_round_patterns(deduped)

    # 5. 证据引用
    evidence_refs = [
        EvidenceRef(
            provider=exp.provider,
            source_id=exp.source_id,
            content_hash=exp.content_hash,
        )
        for exp in deduped
    ]

    # 6. 构建 profile（先生成不含 snapshot_hash 的实例）
    profile = CompanyInterviewProfile(
        company=company,
        role=role,
        region=region,
        sample_count=len(experiences),
        usable_sample_count=len(deduped),
        top_topics=top_topics,
        representative_questions=representative_questions,
        round_patterns=round_patterns,
        evidence_refs=evidence_refs,
        generated_at=datetime.now(timezone.utc),
        snapshot_hash="",  # 先留空，下面填充
    )

    # 7. 计算快照哈希
    profile.snapshot_hash = _compute_snapshot_hash(profile)

    return profile


__all__ = [
    "_analyze_round_patterns",
    "_compute_snapshot_hash",
    "_compute_topic_frequencies",
    "_deduplicate",
    "_extract_representative_questions",
    "aggregate",
]
