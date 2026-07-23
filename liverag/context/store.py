"""提示词、会话、历史、知识库上下文文件存储
数据读写"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from liverag.context.defaults import (
    DEFAULT_HISTORY_COMPRESS_PROMPT,
    DEFAULT_KNOWLEDGE_OVERVIEW_FALLBACK,
    DEFAULT_KNOWLEDGE_OVERVIEW_PROMPT,
    DEFAULT_SOUL,
    DEFAULT_SYSTEM_PROMPT_TEMPLATE,
)
from liverag.runtime.paths import RuntimePaths, ensure_runtime_dirs


@dataclass(frozen=True)
class SessionPaths:
    """会话对应的四个文件路径"""

    directory: Path
    messages_file: Path
    rag_context_file: Path
    system_prompt_file: Path
    runtime_file: Path


MessageRole = Literal["system", "user", "assistant"]


class ContextStore:
    """只负责上下文文件的读写，不负责判断"""

    def __init__(self, paths: RuntimePaths):
        """绑定运行路径"""

        self.paths = paths

    def initialize(self) -> None:
        """初始化公共运行目录和默认提示词"""

        # 创建prompts,history,session,model,rag,logs对应的目录
        ensure_runtime_dirs(self.paths)

        # 创建以上目录对应的文件
        self._ensure_text_file(
            self.paths.system_prompt_template_file, DEFAULT_SYSTEM_PROMPT_TEMPLATE
        )
        self._ensure_text_file(self.paths.soul_file, DEFAULT_SOUL)
        self._ensure_text_file(
            self.paths.history_compress_prompt_file, DEFAULT_HISTORY_COMPRESS_PROMPT
        )
        self._ensure_text_file(
            self.paths.knowledge_overview_prompt_file, DEFAULT_KNOWLEDGE_OVERVIEW_PROMPT
        )

    def start_session(self, session_id: str, kb_id: str) -> None:
        """开启并初始化一段新session
        runtime.json通过write_runtime_state()创建并写入"""

        safe_session_id = self._safe_session_id(session_id)
        safe_kb_id = self._safe_kb_id(kb_id)

        paths = self._session_paths(safe_session_id)
        paths.directory.mkdir(parents=True, exist_ok=False)

        self._ensure_text_file(paths.messages_file, "")
        self._ensure_text_file(paths.rag_context_file, "")
        self._ensure_text_file(paths.system_prompt_file, "")
        self.write_runtime_state(
            safe_session_id,
            {
                "session_id": safe_session_id,
                "kb_id": safe_kb_id,
                "state": "active",
                "started_at": self._now_iso(),
                "ended_at": None,
                "retention": {
                    "cleanup_enabled": False,  # 关闭自动清理：系统不会自动删除session消息等原始数据
                },
            },
        )

    def end_session(self, session_id: str, state: str = "ended") -> None:
        """结束会话，只更新运行状态，不删除原始记录"""

        runtime = self.read_runtime_state(session_id)
        if not runtime:
            raise ValueError(f"session does not exist: {session_id}")

        ended_at = datetime.now(timezone.utc)
        duration: float | None = None
        started_at_value = runtime.get("started_at")
        if isinstance(started_at_value, str):
            try:
                started_at = datetime.fromisoformat(started_at_value)
            except ValueError:
                pass
            else:
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=timezone.utc)
                duration = max(0.0, (ended_at - started_at).total_seconds())

        runtime["state"] = state
        runtime["ended_at"] = ended_at.isoformat()
        runtime["duration"] = duration

        self.write_runtime_state(session_id, runtime)

    def read_system_prompt_template(self) -> str:
        """读取系统提示词模板。"""

        return self._read_text(
            self.paths.system_prompt_template_file, DEFAULT_SYSTEM_PROMPT_TEMPLATE
        )

    def write_system_prompt_template(self, content: str) -> None:
        """写入系统提示词模板。"""

        self.paths.system_prompt_template_file.write_text(content.rstrip() + "\n", encoding="utf-8")

    def read_soul(self) -> str:
        """读取用户定义的 Agent 角色人格。"""

        return self._read_text(self.paths.soul_file, DEFAULT_SOUL)

    def write_soul(self, content: str) -> None:
        """写入用户定义的 Agent 角色人格。"""

        self.paths.soul_file.write_text(content.rstrip() + "\n", encoding="utf-8")

    def read_knowledge_overview(self,kb_id:str)->str:
            """读取指定知识库固定概览"""
    
            self.ensure_knowledge_overview_default(kb_id)

            #防止文件存在但是内容为空
            content = self._read_text(self._overview_file(kb_id),"")
            if content.strip():
                return content
            return DEFAULT_KNOWLEDGE_OVERVIEW_FALLBACK.rstrip()+"\n"
    
    def ensure_knowledge_overview_default(self,kb_id:str)->None:
        """确保指定知识库至少有默认概览文件"""

        if self._overview_file(kb_id).is_file():
            return
        self.write_knowledge_overview(
            kb_id,
            DEFAULT_KNOWLEDGE_OVERVIEW_FALLBACK,
            stale=True,
            reason="default_created",
            source="default",
        )

    def write_knowledge_overview(
        self,
        kb_id:str,
        content:str,
        *,
        stale:bool, #概览是否过期
        reason: str | None = None, #解释为什么stale？
        source: str = "context_model", #概览来源：默认上下文模型生成
        source_job_id: str | None = None, #哪一次后台任务生成了概览
        raw_overview: dict[str, Any] | None = None,
    )->None:
        """写入指定知识库概览和元数据（状态和来源）"""

        #写入概览
        self._overview_file(kb_id).write_text(content.rstrip()+"\n",encoding="utf-8")

        #写入元数据
        self._overview_meta_file(kb_id).write_text(
            json.dumps(
                        {
                            "kb_id": kb_id,
                            "updated_at": self._now_iso(),
                            "stale": stale,
                            "reason": reason,
                            "source": source,
                            "source_job_id": source_job_id,
                            "raw_summary": self._raw_overview_summary(raw_overview),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
        )

    def read_history_compress_prompt(self) -> str:
        """读取通话历史压缩提示词。"""

        return self._read_text(
            self.paths.history_compress_prompt_file, DEFAULT_HISTORY_COMPRESS_PROMPT
        )

    def write_history_compress_prompt(self, content: str) -> None:
        """写入通话历史压缩提示词。"""

        self.paths.history_compress_prompt_file.write_text(
            content.rstrip() + "\n", encoding="utf-8"
        )

    def read_knowledge_overview_prompt(self) -> str:
        """读取知识库概览生成提示词。"""

        return self._read_text(
            self.paths.knowledge_overview_prompt_file, DEFAULT_KNOWLEDGE_OVERVIEW_PROMPT
        )

    def write_knowledge_overview_prompt(self, content: str) -> None:
        """写入知识库概览生成提示词。"""

        self.paths.knowledge_overview_prompt_file.write_text(
            content.rstrip() + "\n", encoding="utf-8"
        )

    def read_session_system_prompt(self, session_id: str) -> str:
        """读取本次通话已经锁定的系统提示词。"""

        return self._read_text(self._session_paths(session_id=session_id).system_prompt_file, "")

    def write_session_system_prompt(self, session_id: str, content: str) -> None:
        """写入本次通话已经锁定的系统提示词。"""

        self._session_paths(session_id=session_id).system_prompt_file.write_text(
            content.rstrip() + "\n", encoding="utf-8"
        )

    def lock_session_system_prompt(self,session_id:str,content:str)->str:
            """将渲染结果首次写入当前session的SessionSystemPrompt，已锁定时不覆盖"""
    
            safe_session_id=self._safe_session_id(session_id)
    
            #确认session_id已经通过start_session初始化
            self._session_kb_id(safe_session_id)

            #保证了只写一次，不覆盖
            #只要session_system_prompt.md非空，直接返回初始prompt,不重写
            existing=self.read_session_system_prompt(safe_session_id)
            if existing.strip():
                return existing

            #不存在prompt，检查并且写入content
            prompt=content.rstrip() #删除末尾的空格换行
            if not prompt:
                raise ValueError("session system prompt不能为空！")
    
            locked_prompt=prompt+"\n" #统一加一个换行符
            prompt_file=self._session_paths(safe_session_id).system_prompt_file
            prompt_file.write_text(locked_prompt,encoding="utf-8")
    
            return locked_prompt
    
    def append_message(
        self,
        *,
        session_id: str,
        role: MessageRole,
        content: str,
        turn_index: int | None = None,  # 对话轮次编号
        metadata: dict[str, Any] | None = None,
        duration: float | None = None,
    ) -> None:
        """追加当前通话消息"""

        text = content.strip()
        if not text:
            return

        safe_session_id = self._safe_session_id(session_id)
        kb_id = self._session_kb_id(safe_session_id)
        if duration is not None:
            if isinstance(duration, bool) or not isinstance(duration, (int, float)):
                raise ValueError("duration must be a number or None")
            if duration < 0:
                raise ValueError("duration cannot be negative")

        # 将记录追加到jsonl文件，每条消息占一行
        self._append_jsonl(
            self._session_paths(safe_session_id).messages_file,
            {
                "session_id": safe_session_id,
                "kb_id": kb_id,
                "timestamp": self._now_iso(),
                "role": role,
                "content": text,
                "turn_index": turn_index,
                "metadata": metadata or {},
                "duration": duration,
            },
        )

    def read_message(self, *, session_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        """读取通话消息"""

        items = self._read_jsonl(self._session_paths(session_id).messages_file)
        return items[-limit:] if limit and limit > 0 else items

    def append_rag_context(self, session_id: str, record: dict[str, Any]) -> None:
        """追加一轮RAG记录"""

        safe_session_id = self._safe_session_id(session_id)
        kb_id = self._session_kb_id(safe_session_id)
        duration = record.get("duration")
        if duration is not None:
            if isinstance(duration, bool) or not isinstance(duration, (int, float)):
                raise ValueError("RAG duration must be a number or None")
            if duration < 0:
                raise ValueError("RAG duration cannot be negative")

        self._append_jsonl(
            self._session_paths(safe_session_id).rag_context_file,
            {
                **record,
                "timestamp": self._now_iso(),
                "session_id": safe_session_id,
                "kb_id": kb_id,
                "turn_index": record.get("turn_index"),
                "duration": duration,
            },
        )

    def read_rag_context(self, session_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        """读取当前通话RAG查询事实"""

        items = self._read_jsonl(self._session_paths(session_id).rag_context_file)
        return items[-limit:] if limit and limit > 0 else items  # 保留最后limit条

    def read_session_turns(self, session_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        """按照轮次聚合当前对话的messages+rag_context
        读取消息 ─┐
                 ├─ 按 turn_index 分组 ─ 按轮次排序 ─ 截取最近 N 轮 ─ 生成 RAG 摘要
        读取RAG ─┘
        """

        turns: dict[int, dict[str, Any]] = {}

        for message in self.read_message(session_id=session_id):
            # 过滤不合法的轮次编号
            turn_index = self._coerce_turn_index(message.get("turn_index"))
            if turn_index is None:
                continue
            turn = self._ensure_turn(turns, turn_index)
            turn["messages"].append(message)
            role = message.get("role")
            if role == "user":
                turn["user_message"] = message
            elif role == "assistant":
                turn["assistant_message"] = message

        for record in self.read_rag_context(session_id):
            # 过滤不合法的轮次编号
            turn_index = self._coerce_turn_index(record.get("turn_index"))
            if turn_index is None:
                continue
            turn = self._ensure_turn(turns, turn_index)
            turn["rag_contexts"].append(record)

        items = [turns[index] for index in sorted(turns)]
        if limit and limit > 0:
            items = items[-limit:]
        for turn in items:
            contexts = turn["rag_contexts"]
            turn["rag"] = self._build_rag_summary(contexts[-1] if contexts else None)

        return items

    def read_recent_history(self, kb_id:str,limit:int=20)->list[dict[str,Any]]:
        """读取指定知识库最近limit条历史记录"""

        if isinstance(limit,bool) or not isinstance(limit,int) or limit<=0:
            raise ValueError("limit不能为负数！")

        records=self._read_jsonl(self._history_file(kb_id))
        return records[-limit:]

    def read_runtime_state(self, session_id: str) -> dict[str, Any]:
        """读取当前运行状态"""

        path = self._session_paths(session_id).runtime_file

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError:
            return {}

        return data if isinstance(data, dict) else {}

    def write_runtime_state(self, session_id: str, state: dict[str, Any]) -> None:
        """写入当前运行状态"""

        path = self._session_paths(session_id).runtime_file
        path.write_text(
            json.dumps(state, indent=2, ensure_ascii=False),  # 写入状态
            encoding="utf-8",
        )

    def _context_kb_dir(self, kb_id: str) -> Path:
            directory = self.paths.context_dir / self._safe_kb_id(kb_id)
            directory.mkdir(parents=True, exist_ok=True)
            return directory
    
    def _overview_file(self,kb_id: str) -> Path:
        """返回指定知识库概览文件路径"""

        return self._context_kb_dir(kb_id) / "knowledge_overview.md"

    def _overview_meta_file(self, kb_id: str) -> Path:
        """返回指定知识库概览元数据文件"""

        return self._context_kb_dir(kb_id) / "knowledge_overview_meta.json"

    def _history_kb_dir(self, kb_id: str) -> Path:
            directory = self.paths.history_dir / self._safe_kb_id(kb_id)
            directory.mkdir(parents=True, exist_ok=True)
            return directory
    
    def _history_file(self, kb_id: str) -> Path:
        """返回指定知识库的长期历史文件路径。"""

        return self._history_kb_dir(kb_id) / "history.jsonl"

    def _session_dir(self, session_id: str) -> Path:
        """获取安全的session_id,拼接路径
        例如：~/.LiveRAG/sessions/session_001/"""

        safe_session_id = self._safe_session_id(session_id)
        return self.paths.sessions_dir / safe_session_id

    def _session_paths(self, session_id: str) -> SessionPaths:
        """动态获取当前session对应的messages/rag_context/runtime/session_system_prompt路径"""

        directory = self._session_dir(session_id)
        return SessionPaths(
            directory=directory,
            messages_file=directory / "messages.jsonl",
            rag_context_file=directory / "rag_context.jsonl",
            system_prompt_file=directory / "session_system_prompt.md",
            runtime_file=directory / "runtime.json",
        )

    def _session_kb_id(self, session_id: str) -> str:
        """读取会话锁定的知识库 ID，并拒绝未初始化或损坏的会话。"""

        runtime = self.read_runtime_state(session_id)
        kb_id = runtime.get("kb_id")
        if not isinstance(kb_id, str) or not kb_id:
            raise ValueError(f"session has no valid kb_id: {session_id}")
        return self._safe_kb_id(kb_id)

    @staticmethod
    def _safe_kb_id(kb_id: str) -> str:
        """校验知识库ID，防止通过非法路径访问其他目录"""

        clean = kb_id.strip() or "default"
        # 只允许字母、数字、下划线、连字符
        if not all(char.isalnum() or char in {"_", "-"} for char in clean):
            raise ValueError(f"Invalid kb_id:{kb_id}")
        return clean

    @staticmethod
    def _safe_session_id(session_id: str) -> str:
        """校验会话ID，防止通过非法路径访问其他目录"""

        clean = session_id.strip()
        # 只允许数字、字母、下划线、连字符
        if not clean:
            raise ValueError("session_id cannot be empty!")
        if not all(char.isalnum() or char in {"_", "-"} for char in clean):
            raise ValueError(f"Invalid session_id:{session_id}")
        return clean

    @staticmethod
    def _ensure_turn(turns: dict[int, dict[str, Any]], turn_index: int) -> dict[str, Any]:
        """创建或者取得某一轮数据结构"""

        if turn_index not in turns:
            turns[turn_index] = {
                "turn_index": turn_index,
                "messages": [],
                "user_message": None,
                "assistant_message": None,
                "rag": ContextStore._build_rag_summary(None),
                "rag_contexts": [],  # 原始查询记录
            }
        return turns[turn_index]

    @staticmethod
    def _build_rag_summary(record: dict[str, Any] | None) -> dict[str, Any]:
        """把原始RAG查询记录转换为固定的摘要"""

        # 没有RAG记录
        if not record:
            return {
                "status": "not_queried",
                "queried": False,
                "hit": None,
                "has_context": False,
                "query": None,
                "effective_query": None,
                "request_id": None,
                "latency_ms": None,
                "cache_hit": False,
                "evidence_documents": [],
                "evidence_chunks": [],
                "evidence_count": 0,
                "no_evidence_reason": None,
                "error": None,
                "context_preview": "",
                "kb_id": None,
                "kb_name": None,
            }

        metrics = record.get("metrics")
        if not isinstance(metrics, dict):
            metrics = {}

        error = record.get("error")
        hit = record.get("hit")
        has_context = bool(record.get("has_context"))
        if error:
            status = "failed"
        elif hit is True or has_context:
            status = "hit"
        else:
            status = "miss"  # 查询但未命中

        return {
            "status": status,
            "queried": True,  # 是否发起过RAG查询
            "hit": hit,
            "has_context": has_context,
            "query": record.get("query"),
            "effective_query": record.get("effective_query"),
            "request_id": record.get("request_id"),
            "latency_ms": metrics.get("latency_ms"),
            "cache_hit": bool(metrics.get("cache_hit")),  # 本次查询结果是否直接来自缓存
            "evidence_documents": record.get("evidence_documents") or [],
            "evidence_chunks": record.get("evidence_chunks") or [],
            "evidence_count": record.get("evidence_count") or 0,
            "no_evidence_reason": record.get("no_evidence_reason"),
            "error": error,
            "context_preview": record.get("context_preview") or "",  # 检索上下文的简短预览
            "kb_id": record.get("kb_id"),
            "kb_name": record.get("kb_name"),
        }

    @staticmethod
    def _raw_overview_summary(raw_overview:dict[str,Any] | None)->dict[str,Any]:
        """提取原始overview中的summary数据"""

        if not isinstance(raw_overview,dict):
            return {}
        summary=raw_overview.get("summary")
        return summary if isinstance(summary,dict) else {}
    
    @staticmethod
    def _ensure_text_file(path: Path, default: str) -> None:
        """确保指定文本文件存在"""

        # 创建文件所在父文件
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():  # 文件不存在，写入默认内容并且创建文件；文件存在的话就不修改
            path.write_text(default.rstrip() + "\n", encoding="utf-8")

    @staticmethod
    def _read_text(path: Path, default: str) -> str:
        """读取指定文件文档"""

        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return default

    @staticmethod
    def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
        """向JSONL中追加一条记录"""

        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:  # "a"表示追加写入
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        """读取该JSONL文件中的数据"""

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        items: list[dict[str, Any]] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                items.append(value)
        return items

    @staticmethod
    def _coerce_turn_index(turn_index: Any) -> int | None:
        """把不同形式的轮次编号安全的转换成整数，无法转换返回None"""

        if isinstance(turn_index, bool):
            return None
        if isinstance(turn_index, int):
            return turn_index if turn_index >= 0 else None
        if isinstance(turn_index, str):
            value = turn_index.strip()
            if value.isdigit():
                return int(value)
        return None

    @staticmethod
    def _now_iso() -> str:
        """返回当时时间"""

        return datetime.now(timezone.utc).isoformat()
