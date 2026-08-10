"""长期技能画像的确定性聚合策略。
负责把某个候选人、某项技能的全部SkillProgressEvidence聚合成一个SkillProgress
纯算法层，不涉及LLM、不访问数据库"""

from __future__ import annotations

import statistics
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from liverag.interview.schemas import (
    SkillProgress,
    SkillProgressEvidence,
    WeakPointAggregate,
)


@dataclass
class _WeakPointAccumulator:
    """内部使用的薄弱点聚集器"""

    text: str   #展示文本
    count: int  #出现次数
    latest_at: datetime #最近出现时间
    source_evaluation_ids: list[str] = field(default_factory=list)  #对应的评价ids


def normalize_weak_point(value: str) -> tuple[str, str]:
    """返回薄弱点的稳定去重键和保留首次写法的展示文本。

    Unicode 统一、去多余空格、去末尾标点、大小写归一化"""

    display = " ".join(
        unicodedata.normalize("NFKC", value)
        .strip()
        .rstrip("，。；;,. ")
        .split()
    )
    return display.casefold(), display


def calculate_skill_progress(
    #sequence：有顺序、可按索引访问的一组元素，例如list/tuple
    evidence: Sequence[SkillProgressEvidence], *, taxonomy_version: int
) -> SkillProgress:
    """把某一个技能的所有历史 Evidence 聚合成 SkillProgress 的纯计算函数。

    1. 校验 evidence 非空
    2. 排序并确认“同一个人 + 同一个技能”
    3. 计算分数相关指标
    4. 计算时间衰减权重
    5. 计算 confidence
    6. 聚合 weak_points
    7. 组织 source_evaluation_ids 和时间范围
    8. 返回 SkillProgress"""

    if not evidence:
        raise ValueError("计算技能画像至少需要一条证据")

    #根据评价时间从旧到新排序
    ordered = sorted(evidence, key=lambda item: (item.evaluated_at, item.evaluation_id))
    #取出cp_id
    candidate_profile_id = ordered[0].candidate_profile_id
    #取出skill_key
    skill_key = ordered[0].skill_key
    #确保cp_id+skill_key都相同
    if any(
        item.candidate_profile_id != candidate_profile_id or item.skill_key != skill_key
        for item in ordered
    ):
        raise ValueError("一次聚合只能包含同一候选人的同一技能")

    #最近一次评价时间
    latest_at = ordered[-1].evaluated_at
    #取出每项score
    scores = [item.score for item in ordered]
    #计算时间衰减权重
    weights = [
        0.5 ** ((latest_at - item.evaluated_at).total_seconds() / 86400 / 90)
        for item in ordered
    ]
    #尝试次数
    attempts = len(ordered)
    #来自几场不同的面试
    distinct_sessions = len({item.session_id for item in ordered})
    #横跨时间范围
    span_days = (latest_at - ordered[0].evaluated_at).total_seconds() / 86400
    #计算稳定性
    consistency = (
        0.5
        if attempts == 1
        else max(0.0, 1.0 - statistics.pstdev(scores) / 25)
    )
    #聚合去重薄弱点
    weak: dict[str, _WeakPointAccumulator] = {}
    for item in ordered:
        for raw_value in item.weak_points:
            key, display = normalize_weak_point(raw_value)
            aggregate = weak.setdefault(
                key,
                _WeakPointAccumulator(text=display, count=0, latest_at=item.evaluated_at),
            )
            aggregate.count += 1
            aggregate.latest_at = max(aggregate.latest_at, item.evaluated_at)
            aggregate.source_evaluation_ids.append(item.evaluation_id)

    weak_points = [
        WeakPointAggregate(
            text=value.text,
            count=value.count,
            latest_at=value.latest_at,
            source_evaluation_ids=value.source_evaluation_ids,
        )
        for _, value in sorted(
            weak.items(),
            key=lambda pair: (
                -pair[1].count,
                -pair[1].latest_at.timestamp(),
                pair[0],
            ),
        )[:5]
    ]

    return SkillProgress(
        candidate_profile_id=candidate_profile_id,
        skill_key=skill_key,
        taxonomy_version=taxonomy_version,
        attempts=attempts,
        average_score=round(sum(scores) / attempts, 2),
        #时间衰减权重分数
        current_score=round(
            sum(item.score * weight for item, weight in zip(ordered, weights, strict=True))
            / sum(weights),
            2,
        ),
        latest_score=ordered[-1].score,
        #可信度：评价数量*35% + 跨多少场面试*25% + 证据跨度多久*15% + 成绩是否稳定*25%
        confidence=round(
            0.35 * min(1.0, attempts / 5)
            + 0.25 * min(1.0, distinct_sessions / 3)
            + 0.15 * (min(1.0, span_days / 90) if attempts > 1 else 0.0)
            + 0.25 * consistency,
            4,
        ),
        weak_points=weak_points,
        source_evaluation_ids=[item.evaluation_id for item in ordered],
        first_evaluated_at=ordered[0].evaluated_at,
        last_evaluated_at=latest_at,
        updated_at=latest_at,
    )
