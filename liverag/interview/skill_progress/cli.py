"""重建候选人长期技能画像的命令行入口。"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from liverag.config.settings import load_app_settings, load_environment
from liverag.interview.persistence.db import create_database_engine, create_session_factory
from liverag.interview.persistence.sqlalchemy_repository import SQLAlchemyInterviewRepository
from liverag.interview.records import candidate_profile_id_for_kb
from liverag.interview.skill_progress.service import SkillProgressService
from liverag.interview.skill_progress.taxonomy import SkillTaxonomy


def main(argv: Sequence[str] | None = None) -> int:
    """liverag-rebuild-skill-progress 的启动入口

    相当于运行：
    liverag-rebuild-skill-progress --candidate-kb-id default --dry-run

    指定了--dry-run：跳过写入数据库，只输出计算结果
    没指定：把progress+evidence写入数据库
    """

    parser = argparse.ArgumentParser(description="重建候选人的长期技能画像")
    parser.add_argument("--candidate-kb-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    #加载环境
    load_environment()
    #获取AppSettings
    settings = load_app_settings()
    #获取数据库engine
    engine = create_database_engine(settings.interview_database.url)
    #根据数据库引擎获取对应的alchemy repository
    repository = SQLAlchemyInterviewRepository(create_session_factory(engine))
    #引入skill_taxonomy.v1.json文件作为题库
    taxonomy = SkillTaxonomy.from_file(
        Path(__file__).resolve().parent / "data" / "skill_taxonomy.v1.json"
    )
    #获取Skillprogress服务
    service = SkillProgressService(repository, taxonomy)

    #根据稳定候选人的知识库 ID 生成跨场可复用的 cp_id
    candidate_profile_id = candidate_profile_id_for_kb(args.candidate_kb_id)
    #根据一个候选人的全部历史评价，在内存里重新构造完整长期画像
    progress, evidence = service.build_candidate_snapshot(candidate_profile_id)

    #写入数据库，重建skillprogress
    if not args.dry_run:
        repository.replace_skill_progress(
            candidate_profile_id=candidate_profile_id,
            progress=progress,
            evidence=evidence,
        )

    #总共资源个数
    source_count = sum(len(item.source_evaluation_ids) for item in progress)
    print(
        f"evaluations={len(evidence)} skills={len(progress)} "
        f"source_evaluations={source_count} dry_run={args.dry_run}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
