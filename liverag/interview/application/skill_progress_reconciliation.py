"""长期技能画像对账的共享应用层辅助函数。"""

from __future__ import annotations

import logging

from liverag.interview.persistence.repository import InterviewRepository
from liverag.interview.skill_progress.service import SkillProgressService


logger = logging.getLogger("liverag.interview.skill_progress.reconciliation")


def reconcile_skill_progress(
    repository: InterviewRepository,
    skill_progress_service: SkillProgressService | None,
    session_id: str,
) -> None:
    """报告完成后，从持久化评价重建候选人的长期技能画像。"""

    if skill_progress_service is None:
        return

    candidate_profile_id: str | None = None
    try:
        #根据session_id获得session
        session = repository.get_session(session_id)
        #获得对应的interview
        interview = repository.get_interview(session.interview_id)
        #获得interview中的cp_id
        candidate_profile_id = interview.candidate_profile_id
        #根据cp_id对应candidate的所有历史评价，生成长期画像，并写入数据库
        skill_progress_service.rebuild_candidate(candidate_profile_id)
    except Exception:
        logger.exception(
            "interview.skill_progress.reconcile_failed",
            extra={
                "session_id": session_id,
                "candidate_profile_id": candidate_profile_id,
            },
        )


__all__ = ["reconcile_skill_progress"]
