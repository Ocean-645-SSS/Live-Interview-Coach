"""长期技能画像应用服务测试。"""

from pathlib import Path

from tests.interview.test_skill_progress_policy import item

from liverag.interview.skill_progress.policy import calculate_skill_progress
from liverag.interview.skill_progress.service import SkillProgressService
from liverag.interview.skill_progress.taxonomy import SkillTaxonomy


TAXONOMY_PATH = Path("liverag/interview/skill_progress/data/skill_taxonomy.v1.json")


class FakeRepository:
    def __init__(self):
        self.evidence = []
        self.progress = []

    def get_evaluation_record(self, answer_id: str):
        return answer_id

    def apply_skill_evidence(self, evidence):
        if all(saved.evaluation_id != evidence.evaluation_id for saved in self.evidence):
            self.evidence.append(evidence)
        self.progress = [
            calculate_skill_progress(self.evidence, taxonomy_version=1)
        ]
        return self.progress[0]

    def list_skill_progress(self, candidate_profile_id: str):
        return [
            progress
            for progress in self.progress
            if progress.candidate_profile_id == candidate_profile_id
        ]


def test_apply_evaluation_is_idempotent(monkeypatch):
    repository = FakeRepository()
    service = SkillProgressService(
        repository,  # type: ignore[arg-type]
        SkillTaxonomy.from_file(TAXONOMY_PATH),
    )
    evidence = item("evaluation_1", score=70, day=0, session="s1")
    monkeypatch.setattr(service, "_to_evidence", lambda record: evidence)

    first = service.apply_evaluation("answer_1")
    second = service.apply_evaluation("answer_1")

    assert first == second
    assert second.attempts == 1
    assert second.source_evaluation_ids == ["evaluation_1"]


def test_candidates_never_share_progress():
    repository = FakeRepository()
    service = SkillProgressService(
        repository,  # type: ignore[arg-type]
        SkillTaxonomy.from_file(TAXONOMY_PATH),
    )
    repository.evidence = [item("evaluation_1", score=70, day=0, session="s1")]
    repository.progress = [
        calculate_skill_progress(repository.evidence, taxonomy_version=1)
    ]

    assert service.list_progress("candidate_2") == []
