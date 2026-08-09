"""从题库提取出 category+subcategory ，离线生成并写出 skill_taxonomy.v1.json
仅用于首次从题库生成 taxonomy。生成后的 taxonomy 文件作为版本化配置提交，
后续展示名变化通过 aliases 修改，不允许重新生成覆盖已有 key"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from liverag.interview.skill_progress.taxonomy import (
    SkillDefinition,
    SkillTaxonomyDocument,
    normalize_label,
)


def _stable_keys(category: str, subcategory: str) -> tuple[str, str]:
    """给一个技能分类生成稳定ID"""

    normalized_category = normalize_label(category)
    normalized_subcategory = normalize_label(subcategory)

    parent_digest = hashlib.sha256(normalized_category.encode()).hexdigest()[:12]
    skill_digest = hashlib.sha256(
        f"{normalized_category}\0{normalized_subcategory}".encode()
    ).hexdigest()[:16]

    return f"domain_{parent_digest}", f"skill_{skill_digest}"


def build_taxonomy(
    input_paths: Sequence[Path], *, version: int = 1
) -> SkillTaxonomyDocument:
    """合并题库分类，并按规范化标签生成顺序和稳定键，不写文件

    1.读取每个题库 JSON。
    2.取出每道题的 category 和 subcategory。
    3.空二级分类统一为“通用”。
    4.用规范化后的分类去重、排序。
    5.为每个唯一分类生成 SkillDefinition。
    6.返回完整的 SkillTaxonomyDocument"""

    display_by_labels: dict[tuple[str, str], tuple[str, str]] = {}
    for input_path in input_paths:
        document = json.loads(input_path.read_text(encoding="utf-8"))
        for question in document["questions"]:
            category = question["category"]
            subcategory = question.get("subcategory") or "通用"
            labels = (normalize_label(category), normalize_label(subcategory))
            display_by_labels.setdefault(labels, (category, subcategory))

    skills: list[SkillDefinition] = []
    for labels in sorted(display_by_labels):
        category, subcategory = display_by_labels[labels]
        parent_key, skill_key = _stable_keys(category, subcategory)
        skills.append(
            SkillDefinition(
                key=skill_key,
                parent_key=parent_key,
                category=category,
                subcategory=subcategory,
            )
        )
    return SkillTaxonomyDocument(version=version, skills=skills)


def write_taxonomy(input_paths: Sequence[Path], output_path: Path) -> tuple[int, str]:
    """把构建成果写入文件"""

    document = build_taxonomy(input_paths)
    #转为普通dict
    content = json.dumps(document.model_dump(), ensure_ascii=False, indent=2) + "\n"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    #格式化为utf-8，写入文件
    output_path.write_text(content, encoding="utf-8")

    return len(document.skills), hashlib.sha256(content.encode()).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口"""
    
    parser = argparse.ArgumentParser(description="从题库生成版本化技能 taxonomy")
    parser.add_argument("--input", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    count, digest = write_taxonomy(args.input, args.output)
    print(f"skills={count} sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
