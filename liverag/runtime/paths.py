"""统一管理LiveRAG用户数据目录的路径。
例如：
~/.LiveRAG/
├── liverag.db                         # db_file
│
├── prompts/                           # prompts_dir
│   ├── system_prompt_template.md      # system_prompt_template_file
│   ├── SOUL.md                        # soul_file
│   ├── history_compress_prompt.md     # history_compress_prompt_file
│   └── knowledge_overview_prompt.md   # knowledge_overview_prompt_file
│
├── history/                           # history_dir
│   └── {kb_id}/
│       ├── history.jsonl
│       └── .cursor
│
├── context/                           # context_dir
│   └── {kb_id}/
│       ├── knowledge_overview.md
│       └── knowledge_overview_meta.json
│
├── session/                           # session_dir
│   ├── messages.jsonl                 # messages_file
│   ├── rag_context.jsonl              # rag_context_file
│   ├── session_system_prompt.md       # session_system_prompt_file
│   └── runtime_state.json             # runtime_state_file
│
├── model/                             # model_dir
│   ├── config.json                    # model_config_file
│   └── context_config.json            # context_model_config_file
│
├── rag/                               # rag_dir
│   └── knowledge_bases/               # rag_knowledge_bases_dir
│       └── {kb_id}/
│           ├── sources/
│           ├── storage/
│           └── logs/
│
└── logs/                              # logs_dir
"""


import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    """保存所有运行时需要读写的文件和目录的路径"""

    user_data_dir:Path  #用户数据根目录
    db_file:Path    #数据库路径
    prompts_dir:Path    #全局提示词
    system_prompt_template_file:Path    #session system prompt基础模版
    soul_file:Path  #agent的角色、人格和行为约束
    history_compress_prompt_file:Path   #原始对话压缩为长期history的提示词
    knowledge_overview_prompt_file:Path    #生成知识库概览的提示词
    history_dir:Path    #按知识库保存长期会话摘要
    context_dir:Path    #保存知识库上下文信息以及knowledge overview
    sessions_dir:Path    #当前实现中全局sessions目录
    #messages_file:Path  #当前session的user+assistant原始消息
    #rag_context_file:Path   #当前session的RAG查询和evidence记录
    #session_system_prompt_file:Path    #当前session渲染固定的system prompt
    #runtime_state_file:Path     #当前session或agent的运行状态
    model_dir:Path    #模型运行配置目录
    model_config_file:Path    #STT/LLM/TTS等模型配置
    context_model_config_file:Path    #history压缩、overview生成等上下文模型配置
    rag_dir:Path    #RAG模块根目录
    rag_knowledge_bases_dir:Path    #各知识库独立的LightRAG workspace
    logs_dir:Path   #运行日志目录


def get_user_data_dir()->Path:
    """返回用户数据根目录，默认是 ~/.LiveRAG。"""

    return Path(os.getenv("LIVERAG_USER_DATA_DIR","~/.LiveRAG")).expanduser()


def build_runtime_paths(user_data_dir:Path | None=None)->RuntimePaths:
    """根据根目录派生出所有运行文件路径"""

    root=(user_data_dir or get_user_data_dir()).expanduser()
    prompts_dir=root / "prompts"
    history_dir=root / "history"
    context_dir=root / "context"
    sessions_dir=root / "sessions"
    model_dir=root / "model"
    rag_dir=root / "rag"
    rag_knowledge_bases_dir = rag_dir / "knowledge_bases"
    logs_dir=root / "logs"

    return RuntimePaths(
        user_data_dir=root,
        db_file=root / "liverag.db",
        prompts_dir=prompts_dir,
        system_prompt_template_file=prompts_dir / "system_prompt_template.md",
        soul_file=prompts_dir / "SOUL.md",
        history_compress_prompt_file=prompts_dir / "history_compress_prompt.md",
        knowledge_overview_prompt_file=prompts_dir / "knowledge_overview_prompt.md",
        history_dir=history_dir,
        context_dir=context_dir,
        sessions_dir=sessions_dir,
        #messages_file=sessions_dir / "messages.jsonl",
        #rag_context_file=sessions_dir / "rag_context.jsonl",
        #session_system_prompt_file=sessions_dir / "session_system_prompt.md",
        #runtime_state_file=sessions_dir / "runtime_state.json",
        model_dir=model_dir,
        model_config_file=model_dir / "config.json",
        context_model_config_file=model_dir / "context_config.json",
        rag_dir=rag_dir,
        rag_knowledge_bases_dir=rag_knowledge_bases_dir,
        logs_dir=logs_dir,
    )


def ensure_runtime_dirs(paths:RuntimePaths) -> None:
    """创建运行所需要的目录"""

    for directory in (
        paths.prompts_dir,
        paths.history_dir,
        paths.context_dir,
        paths.sessions_dir,
        paths.model_dir,
        paths.rag_knowledge_bases_dir,
        paths.logs_dir
    ):
        directory.mkdir(parents=True, exist_ok=True)
