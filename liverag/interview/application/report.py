"""根据已经持久化的回答评价生成可审计的面试报告:
包括平均分 / 已覆盖知识点 / 缺失知识点+错误点 / 每道题完整评价"""

from __future__ import annotations

from typing import Any

from liverag.interview.persistence.repository import InterviewRepository


class InterviewReportBuilder:
    """只基于权威评价聚合报告，不重新评价回答。"""

    def __init__(self, repository: InterviewRepository):
        self._repository = repository

    def build(self, session_id: str) -> dict[str, Any]:

        #获得当前session
        session = self._repository.get_session(session_id)
        #列出当前session对应的评价
        evaluations = self._repository.list_evaluations(session_id)
        #所有评价分数
        scores = [item.weighted_score for item in evaluations]

        return {
            "session_id": session.id,
            "interview_id": session.interview_id,
            "evaluation_count": len(evaluations),   #评价个数
            "overall_score": round(sum(scores) / len(scores), 2) if scores else None,   #平均分
            #所有评价中候选人覆盖的知识点（去重后）
            "strengths": list(
                dict.fromkeys(
                    point
                    for evaluation in evaluations
                    for point in evaluation.covered_points
                )
            ),
            #需要改进的点，包括：回答错误的点+没有覆盖的知识点（去重后）
            "improvements": list(
                dict.fromkeys(
                    point
                    for evaluation in evaluations
                    for point in (*evaluation.missing_points, *evaluation.errors)
                )
            ),
            #完整评价结构
            "evaluations": [item.model_dump(mode="json") for item in evaluations],
        }


__all__ = ["InterviewReportBuilder"]
