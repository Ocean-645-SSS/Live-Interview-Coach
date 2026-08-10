"""长期能力画像的稳定数据与算法契约。"""

from typing import TYPE_CHECKING, Any

from liverag.interview.skill_progress.policy import calculate_skill_progress
from liverag.interview.skill_progress.taxonomy import (
    SkillAlias,
    SkillDefinition,
    SkillNotMappedError,
    SkillTaxonomy,
    SkillTaxonomyError,
)

if TYPE_CHECKING:
    from liverag.interview.skill_progress.service import SkillProgressService


def __getattr__(name: str) -> Any:
    """延迟加载依赖题库的服务，避免策略模块与题库循环导入。"""

    if name == "SkillProgressService":
        from liverag.interview.skill_progress.service import SkillProgressService

        return SkillProgressService
    raise AttributeError(name)

__all__ = [
    "SkillAlias",
    "SkillDefinition",
    "SkillNotMappedError",
    "SkillProgressService",
    "SkillTaxonomy",
    "SkillTaxonomyError",
    "calculate_skill_progress",
]
