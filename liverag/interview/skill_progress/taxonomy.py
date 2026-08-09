"""版本化两级技能 category+subcategory: taxonomy 的加载、校验与解析为一个稳定的技能ID"""

from __future__ import annotations

import unicodedata
from pathlib import Path

from pydantic import Field, ValidationError

from liverag.interview.schemas import StrictModel


class SkillTaxonomyError(RuntimeError):
    """taxonomy 文件无法读取或违反稳定映射契约。"""


class SkillNotMappedError(LookupError):
    """题目分类没有对应的已登记技能。"""


def normalize_label(value: str) -> str:
    """为技能匹配生成 Unicode、空白和大小写无关的标签。

    NFKC：全角字符等兼容字符转化为统一形式
    例如：RAG -> rag"""

    return " ".join(unicodedata.normalize("NFKC", value).strip().casefold().split())


class SkillAlias(StrictModel):
    """指向一个稳定技能的历史分类名称。"""

    category: str = Field(min_length=1)
    subcategory: str = Field(min_length=1)


class SkillDefinition(StrictModel):
    """一个两级技能及其不可变稳定键。"""

    key: str = Field(pattern=r"^skill_[0-9a-f]{16}$")
    parent_key: str = Field(pattern=r"^domain_[0-9a-f]{12}$")   #身份证
    category: str = Field(min_length=1) #一级分类
    subcategory: str = Field(min_length=1) #二级分类
    aliases: list[SkillAlias] = Field(default_factory=list) #历史名称

    @property
    def display_name(self) -> str:
        """返回适合界面展示的两级技能名称。"""

        return f"{self.category} / {self.subcategory}"


class SkillTaxonomyDocument(StrictModel):
    """taxonomy JSON 顶层文档。"""

    version: int = Field(ge=1)
    skills: list[SkillDefinition] = Field(min_length=1)


class SkillTaxonomy:
    """经过完整校验的只读技能映射。"""

    def __init__(self, document: SkillTaxonomyDocument):
        self._document = document
        self._skills_by_alias: dict[tuple[str, str], SkillDefinition] = {}

        keys: set[str] = set()
        for skill in document.skills:
            if skill.key in keys:
                raise SkillTaxonomyError(f"taxonomy 包含重复 skill key：{skill.key}")
            
            keys.add(skill.key)
            #alias冲突
            labels = [SkillAlias(category=skill.category, subcategory=skill.subcategory)]
            labels.extend(skill.aliases)
            for label in labels:
                alias = (
                    normalize_label(label.category),
                    normalize_label(label.subcategory),
                )
                existing = self._skills_by_alias.get(alias)
                if existing is not None and existing.key != skill.key:
                    raise SkillTaxonomyError(
                        "taxonomy alias 映射到多个技能："
                        f"{label.category}/{label.subcategory}"
                    )
                self._skills_by_alias[alias] = skill

    @classmethod
    def from_file(cls, path: Path) -> SkillTaxonomy:
        """从 UTF-8 JSON 文件加载并校验 taxonomy。"""

        try:
            raw_text = path.expanduser().read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise SkillTaxonomyError(f"无法读取 taxonomy：{path}") from exc
        try:
            document = SkillTaxonomyDocument.model_validate_json(raw_text)
        except ValidationError as exc:
            raise SkillTaxonomyError(f"taxonomy 内容校验失败：{exc}") from exc
        return cls(document)

    @property
    def version(self) -> int:
        return self._document.version

    @property
    def skills(self) -> tuple[SkillDefinition, ...]:
        return tuple(self._document.skills)

    def resolve(self, category: str, subcategory: str | None) -> SkillDefinition:
        """整体调用关系：
        skill_taxonomy.v1.json
                ↓ from_file()
        SkillTaxonomyDocument
                ↓ 建立索引
        SkillTaxonomy
                ↓ resolve(category, subcategory)
        SkillDefinition
                ↓
        稳定的 skill_key / parent_key
        """
        
        #规范化输入
        alias = (normalize_label(category), normalize_label(subcategory or "通用"))

        try:
            return self._skills_by_alias[alias]
        except KeyError as exc:
            raise SkillNotMappedError(
                f"题目分类未映射到技能：{category}/{subcategory}"
            ) from exc
