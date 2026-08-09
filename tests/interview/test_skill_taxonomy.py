"""技能 taxonomy 的稳定映射与题库覆盖测试。"""

import json
from pathlib import Path

import pytest

from liverag.interview.question_bank.catalog import QuestionBank
from liverag.interview.skill_progress.taxonomy import (
    SkillAlias,
    SkillNotMappedError,
    SkillTaxonomy,
    SkillTaxonomyDocument,
    SkillTaxonomyError,
)
from liverag.interview.skill_progress.taxonomy_builder import build_taxonomy

QUESTION_BANK_DATA = Path("liverag/interview/question_bank/data")
TAXONOMY_PATH = Path(
    "liverag/interview/skill_progress/data/skill_taxonomy.v1.json"
)


def test_taxonomy_resolves_normalized_label_to_stable_skill_key():
    taxonomy = SkillTaxonomy.from_file(TAXONOMY_PATH)
    direct = taxonomy.resolve("RAG", "文档切块")
    normalized = taxonomy.resolve("ｒａｇ", " 文档切块 ")

    assert normalized.key == direct.key
    assert direct.parent_key.startswith("domain_")
    assert direct.display_name == "RAG / 文档切块"


def test_taxonomy_resolves_declared_alias(tmp_path: Path):
    document = build_taxonomy([QUESTION_BANK_DATA / "question_bank.v1.json"])
    document.skills[0].aliases = [
        SkillAlias(category="旧分类", subcategory="旧子类")
    ]
    path = tmp_path / "taxonomy.json"
    path.write_text(
        json.dumps(document.model_dump(), ensure_ascii=False), encoding="utf-8"
    )

    taxonomy = SkillTaxonomy.from_file(path)
    assert taxonomy.resolve("旧分类", "旧子类").key == document.skills[0].key


def test_taxonomy_rejects_duplicate_aliases_across_skills(tmp_path: Path):
    document = build_taxonomy([QUESTION_BANK_DATA / "question_bank.v1.json"])
    duplicate = SkillAlias(category="legacy", subcategory="shared")
    document.skills[0].aliases = [duplicate]
    document.skills[1].aliases = [duplicate]
    path = tmp_path / "taxonomy.json"
    path.write_text(
        json.dumps(document.model_dump(), ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(SkillTaxonomyError, match="alias"):
        SkillTaxonomy.from_file(path)


def test_taxonomy_rejects_unknown_classification():
    taxonomy = SkillTaxonomy.from_file(TAXONOMY_PATH)
    with pytest.raises(SkillNotMappedError, match="未映射"):
        taxonomy.resolve("不存在", None)


def test_every_runtime_question_maps_to_skill():
    bank = QuestionBank.from_file(QUESTION_BANK_DATA / "question_bank.v1.json")
    taxonomy = SkillTaxonomy.from_file(TAXONOMY_PATH)
    for question in bank.questions:
        assert taxonomy.resolve(question.category, question.subcategory).key


def test_every_reviewed_question_classification_maps_to_skill():
    reviewed = json.loads(
        (QUESTION_BANK_DATA / "question_bank.v2.reviewed.json").read_text(
            encoding="utf-8"
        )
    )
    taxonomy = SkillTaxonomy.from_file(TAXONOMY_PATH)
    for question in reviewed["questions"]:
        assert taxonomy.resolve(
            question["category"], question.get("subcategory")
        ).key


def test_builder_is_deterministic_and_matches_packaged_taxonomy():
    inputs = [
        QUESTION_BANK_DATA / "question_bank.v1.json",
        QUESTION_BANK_DATA / "question_bank.v2.reviewed.json",
    ]
    expected = SkillTaxonomyDocument.model_validate_json(
        TAXONOMY_PATH.read_text(encoding="utf-8")
    )
    assert build_taxonomy(inputs) == expected
    assert build_taxonomy(list(reversed(inputs))) == expected
