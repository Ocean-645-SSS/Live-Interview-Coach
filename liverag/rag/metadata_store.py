"""LiveRAG 元数据存储模块，提供用于管理知识库和文档的元数据存储功能。"""
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_KB_ID = "default"
DEFAULT_KB_NAME = "默认知识库"
_KB_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$") #预编译正则


@dataclass(frozen=True)
class KnowledgeBaseMeta:
    """知识库元数据"""
    kb_id:str
    name:str
    description:str
    created_at:str
    updated_at:str
    root_dir:Path #知识库根目录

    @property
    def storage_dir(self):
        """返回该知识库独立 LightRAG storage 目录。"""
        return self.root_dir / "storage"

    @property
    def sources_dir(self):
        """返回该知识库原文件目录。"""
        return self.root_dir / "sources"

    @property
    def logs_dir(self):
        """返回该知识库日志目录。"""
        return self.root_dir / "logs"


def utc_now_iso() -> str:
    """返回字符串类型时间"""
    return datetime.now(timezone.utc).isoformat()


class MetadataStore:
    """用于管理元数据的存储类，使用 SQLite 数据库进行存储。"""

    def __init__(self, db_path: Path, knowledge_bases_dir: Path):

        """SQLite 数据库的文件路径，用于存储元数据，主要包括：
        有哪些知识库
        文档属于哪个知识库
        文档解析和索引状态
        入库任务状态
        任务与文档的关联关系"""
        self.db_path = db_path.expanduser()

        """知识库文件根目录路径，主要包括：
        用户上传的 .txt、.md等实际文件
        LightRAG 的索引和向量数据
        每个知识库独立的日志
        每个知识库自己的物理工作目录"""
        self.knowledge_bases_dir = knowledge_bases_dir.expanduser()


    def initialize(self):
        """初始化数据库，创建必要的元数据表。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.knowledge_bases_dir.mkdir(parents=True, exist_ok=True)

        """
        knowledge_bases 表：知识库的基本信息，包括kb_id，name，description，创建日期，最后更新时间。
        documents 表：文档的基本信息，包括document_id，所属知识库kb_id，原始文件名，原始文件保存路径，文件大小，SHA256哈希值，内容类型，扩展名，解析状态，LightRAG索引状态，错误信息，内容长度，块数，创建日期，最后更新时间。
        ingest_jobs 表：入库任务（上传/索引）的基本信息，包括job_id，所属知识库kb_id，任务状态，总文件数，已完成文件数，失败文件数，错误信息，创建日期，最后更新时间。
        ingest_job_documents 表：入库任务与文档的关联关系，包括job_id，document_id，任务状态，错误信息，创建日期，最后更新时间。该表的主键是(job_id, document"""
        with self._connect() as conn:
            conn.execute("pragma foreign_keys = ON")

            conn.executescript(
                """
                create table if not exists knowledge_bases (
                    kb_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                create table if not exists documents (
                    document_id TEXT PRIMARY KEY,
                    kb_id TEXT NOT NULL,
                    original_filename TEXT NOT NULL,
                    source_file_path TEXT NOT NULL,
                    source_file_size INTEGER NOT NULL DEFAULT 0,
                    source_sha256 TEXT NOT NULL DEFAULT '',
                    content_type TEXT NOT NULL DEFAULT '',
                    extension TEXT NOT NULL DEFAULT '',
                    parse_status TEXT NOT NULL DEFAULT 'pending',
                    index_status TEXT NOT NULL DEFAULT 'pending',
                    error_msg TEXT,
                    content_length INTEGER NOT NULL DEFAULT 0,
                    chunks_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (kb_id)
                        REFERENCES knowledge_bases(kb_id)
                        ON DELETE CASCADE
                );

                create table if not exists ingest_jobs (
                    job_id TEXT PRIMARY KEY,
                    kb_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    total_files INTEGER NOT NULL DEFAULT 0,
                    parsed_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    error_msg TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (kb_id)
                        REFERENCES knowledge_bases(kb_id)
                        ON DELETE CASCADE
                );

                create table if not exists ingest_job_documents (
                     job_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error_msg TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (job_id, document_id),
                    FOREIGN KEY (job_id)
                        REFERENCES ingest_jobs(job_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (document_id)
                        REFERENCES documents(document_id)
                        ON DELETE CASCADE
                );
                """
            )

        #创建默认知识库
        self.ensure_default_knowledge_base()


    def ensure_default_knowledge_base(self):
        """确保默认知识库存在"""
        try:
            return self.get_knowledge_base(DEFAULT_KB_ID)
        except KeyError:
            return self.create_knowledge_base(
                name=DEFAULT_KB_NAME,
                description="",
                kb_id=DEFAULT_KB_ID
            )

#=============knowledge_bases相关操作========================
    def create_knowledge_base(
            self,
            *,
            name:str,
            description:str="",
            kb_id:str | None=None,
    )->KnowledgeBaseMeta:
        """创建知识库元数据和目录"""

        new_id = kb_id or f"kb_{self._random_id()}"
        self.validate_kb_id(new_id)
        #测试name是否为None
        if not name.strip():
            raise ValueError("知识库名称不能为空")
        clean_name=name.strip()
        clean_description=description.strip()
        now=utc_now_iso()

        #执行插入
        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    insert into knowledge_bases (
                        kb_id,
                        name,
                        description,
                        created_at,
                        updated_at
                    )
                    values (?, ?, ?, ?, ?)
                    """,
                    (new_id,clean_name,clean_description,now,now)
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"知识库: {new_id}已经存在") from exc

        meta=self.get_knowledge_base(new_id)
        #创建知识库目录
        self.ensure_knowledge_base_dirs(meta)
        return meta



    def get_knowledge_base(self,kb_id:str)->KnowledgeBaseMeta:
        """读取单个知识库"""
        self.validate_kb_id(kb_id)  # 先测试是否是有效kb_id，防止路径注入攻击

        with self._connect() as conn:
            row=conn.execute(
                """
                select * from knowledge_bases where kb_id=?
                """,
                (kb_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"知识库 {kb_id} 不存在")

        meta = self._kb_meta_from_row(row)
        self.ensure_knowledge_base_dirs(meta) #创建知识库运行目录
        return meta

    def _kb_meta_from_row(self,row:sqlite3.Row)->KnowledgeBaseMeta:
        """把get_knowledge_base得到的行转化为元数据
        是内部业务和路径操作，因此要保留root_dir"""
        kb_id=str(row["kb_id"])

        return KnowledgeBaseMeta(
            kb_id=kb_id,
            name=str(row["name"]),
            description=str(row["description"] or ""),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            root_dir=self.knowledge_bases_dir / kb_id,
        )



    def list_knowledge_bases(self)->list[dict[str,Any]]:
        """读取全部知识库"""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT kb.*, COUNT(d.document_id) AS document_count,
                       COALESCE(SUM(d.chunks_count), 0) AS chunk_count
                FROM knowledge_bases kb
                LEFT JOIN documents d ON d.kb_id = kb.kb_id
                GROUP BY kb.kb_id
                ORDER BY CASE WHEN kb.kb_id = ? THEN 0 ELSE 1 END, kb.created_at
                """,
                (DEFAULT_KB_ID,),
            ).fetchall()

            return [self._kb_public_from_row(row) for row in rows]


    def update_knowledge_base(
            self,
            kb_id:str,
            *,
            name:str | None=None,
            description:str | None=None
        )->KnowledgeBaseMeta:
        """更新知识库名称和描述"""
        self.validate_kb_id(kb_id)

        current=self.get_knowledge_base(kb_id=kb_id)
        new_name=current.name if name is None else name.strip()
        if not new_name:
            raise ValueError("知识库名称不得为空！")
        new_description=current.description if description is None else description.strip()
        now=utc_now_iso()

        with self._connect() as conn:
           conn.execute(
                """
                update knowledge_bases set
                name=? , description=? , updated_at=?
                where kb_id=?
                """,
                (new_name,new_description,now,kb_id)
            )

        return self.get_knowledge_base(kb_id)



    def delete_knowledge_base_metadata(self,kb_id:str) -> None:
        """删除指定知识库的元数据，包括知识库信息、文档信息和入库任务信息。"""
        self.validate_kb_id(kb_id)  # 先测试是否是有效kb_id，防止路径注入攻击

        #默认知识库不可删除
        if kb_id== DEFAULT_KB_ID:
            raise ValueError("默认知识库不可删除")

        else:
            with self._connect() as conn:
                conn.execute("pragma foreign_keys = ON")
                #删除知识库元数据
                conn.execute(
                    "DELETE FROM knowledge_bases WHERE kb_id = ?",
                    (kb_id,), #，表示这是单元素元组
                )



    def public_knowledge_base_detail(self,kb_id:str)->dict[str,Any]:
        """返回单个知识库细节"""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT kb.*, COUNT(d.document_id) AS document_count,
                       COALESCE(SUM(d.chunks_count), 0) AS chunk_count
                FROM knowledge_bases kb
                LEFT JOIN documents d ON d.kb_id = kb.kb_id
                WHERE kb.kb_id = ?
                GROUP BY kb.kb_id
                """,
                (kb_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"knowledge base not found: {kb_id}")
        return self._kb_public_from_row(row)

    def _kb_public_from_row(self,row:sqlite3.Row)->dict[str,Any]:
        """把单个知识库细节转换为公开字段
        是HTTP API、前端展示，因此不暴露root_dir"""

        return {
            "kb_id": row["kb_id"],
            "name": row["name"],
            "description": row["description"] or "",
            "document_count": int(row["document_count"] or 0),
            "chunk_count": int(row["chunk_count"] or 0),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }



    def knowledge_base_dir(self, kb_id: str) -> Path:
        """获取指定知识库的物理目录路径。"""
        self.validate_kb_id(kb_id) #先测试是否是有效kb_id，防止路径注入攻击
        return self.knowledge_bases_dir / kb_id





#============document相关操作========================
    def create_document(
        self,
        *,
        document_id: str,
        kb_id: str,
        original_filename: str, #文件原始名
        source_file_path: Path, #文件原路径
        source_file_size: int, #文件大小
        source_sha256: str, #哈希编码
        content_type: str, #文件类型
        extension: str, #文件扩展名
    )->dict[str,Any]:
        """创建文档元数据记录"""

        self.validate_kb_id(kb_id)  # 先测试是否是有效kb_id，防止路径注入攻击
        self.get_knowledge_base(kb_id)  # 确保知识库存在，否则抛出 KeyError
        now = utc_now_iso()

        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO documents(
                        document_id, kb_id, original_filename, source_file_path,
                        source_file_size, source_sha256, content_type, extension,
                        parse_status, index_status, error_msg, content_length,
                        chunks_count, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'pending', NULL, 0, 0, ?, ?)
                    """,
                    (
                        document_id,
                        kb_id,
                        original_filename,
                        str(source_file_path),
                        source_file_size,
                        source_sha256,
                        content_type,
                        extension,
                        now,
                        now,
                    ),
            )
            except sqlite3.IntegrityError as e:
                raise ValueError(f"文档ID {document_id} 已存在或知识库 {kb_id} 不存在") from e

        #返回创建的文档元数据记录
        return self.get_document(kb_id, document_id)


    def get_document(self, kb_id: str, document_id: str)->dict[str,Any]:
        """读取单个文档元数据记录"""
        self.validate_kb_id(kb_id) # 先测试是否是有效kb_id，防止路径注入攻击

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT d.*,kb.name as kb_name
                from documents d
                join
                knowledge_bases kb
                on d.kb_id=kb.kb_id
                where d.document_id=? and d.kb_id=?
                """,
                (document_id, kb_id)
            ).fetchone()
        if row is None:
            raise KeyError(f"文档 {document_id} 不存在于知识库 {kb_id}")

        return self._document_public_from_row(row)

    def _document_public_from_row(self,row:sqlite3.Row)->dict[str,Any]:
        """把get_document中的row转为公开字段"""
        source_path=Path(str(row["source_file_path"] or ""))
        parse_status=str(row["parse_status"])
        index_status=str(row["index_status"])
        status=index_status #统一对外状态
        if parse_status=="failed":
            status="parse_failed"
        elif index_status=="failed":
            status="failed"
        return {
            "document_id": row["document_id"],
            "kb_id": row["kb_id"],
            "kb_name": row["kb_name"],
            "original_filename": row["original_filename"],
            "file_path": row["original_filename"],
            "source_file_path": str(source_path),
            "source_file_exists": source_path.is_file(),
            "source_file_size": int(row["source_file_size"] or 0),
            "source_sha256": row["source_sha256"] or "",
            "content_type": row["content_type"] or "",
            "extension": row["extension"] or "",
            "parse_status": parse_status,
            "index_status": index_status,
            "status": status,
            "error_msg": row["error_msg"],
            "content_summary": "",
            "content_length": int(row["content_length"] or 0),
            "chunks_count": int(row["chunks_count"] or 0),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


    def _update_document(self,document_id:str,kb_id:str,**values):
        """更新文档元数据记录"""
        if not values:
            return  # 如果没有要更新的字段，直接返回

        #允许修改的字段
        allowed_fields = {
            "parse_status", #解析状态
            "index_status", #索引状态
            "error_msg", #报错信息
            "content_length", #内容长度
            "chunks_count", #块数
        }
        #构建assignments/params
        fields={k:v for k,v in values.items() if k in allowed_fields}
        if not fields:
            return  # 如果没有允许的字段，直接返回
        fields["updated_at"] = utc_now_iso() #更新时间
        #构建参数列表
        assignments = ", ".join(f"{k}=?" for k in fields)
        params = [*fields.values(), document_id, kb_id]

        with self._connect() as conn:
            cursor=conn.execute(
                f"""
                update documents set {assignments} where document_id=? and kb_id=?
                """,
                params
            )

            if cursor.rowcount == 0:
                raise KeyError(f"文档 {document_id} 不存在于知识库 {kb_id}")

    def mark_document_parsed(self,kb_id:str,document_id:str,*,content_length:int):
        """将document中的parse_status标记为parsed，并更新content_length"""
        self._update_document(
            document_id=document_id,
            kb_id=kb_id,
            parse_status="parsed",
            error_msg=None,
            content_length=content_length
        )

    def mark_document_indexing(self,kb_id:str,document_id:str):
        """将document中的index_status标记为processing"""
        self._update_document(
            document_id=document_id,
            kb_id=kb_id,
            index_status="processing",
        )

    def update_document_index_status(self,kb_id:str,document_id:str,*,index_status:str,chunks_count:int | None=None,error_msg:str | None=None):
        """更新文档索引状态"""
        if index_status not in {"processed", "processing", "failed"}:
            raise ValueError(f"无效的索引状态:{index_status}")

        #构建更新字段
        values={
            "index_status":index_status,
            "error_msg":error_msg,
        }
        if chunks_count is not None:
            values["chunks_count"]=chunks_count

        self._update_document(
            document_id=document_id,
            kb_id=kb_id,
            **values
        )

    def mark_document_failed(self,kb_id:str,document_id:str,*,error_msg:str):
        """将document中的parse_status标记为failed，并更新error_msg"""
        self._update_document(
            document_id=document_id,
            kb_id=kb_id,
            parse_status="failed",
            index_status="failed",
            error_msg=error_msg
        )



    def clear_documents_metadata(self, kb_id: str) -> None:
        """清空知识库下全部文档和任务元数据。"""

        self.get_knowledge_base(kb_id)  #删除前确认知识库确实存在，否则抛出异常
        with self._connect() as conn:
            conn.execute("DELETE FROM documents WHERE kb_id = ?", (kb_id,))
            conn.execute("DELETE FROM ingest_jobs WHERE kb_id = ?", (kb_id,))



    def source_document_dir(self,kb_id:str,document_id:str):
        """返回文档原文件目录"""
        meta=self.get_knowledge_base(kb_id)
        return meta.sources_dir / document_id


#===================ingest_jobs方法=========================
    def create_job(self,job_id:str,*,kb_id:str,total_files:int)->None:
        """创建入库任务"""
        #确认知识库存在
        self.get_knowledge_base(kb_id)

        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ingest_jobs(
                    job_id, kb_id, status, total_files, parsed_count,
                    failed_count, created_at, updated_at, error_msg
                ) VALUES (?, ?, 'processing', ?, 0, 0, ?, ?, NULL)
                """,
                (job_id, kb_id, total_files, now, now),
            )



    def update_job(
        self,
        job_id:str,
        *,
        status:str,
        parsed_count:int | None=None,
        failed_count:int | None=None,
        error_msg:str | None=None
        )->None:
        """更新入库任务"""
        current=self.get_job(job_id)
        now=utc_now_iso()

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE ingest_jobs
                SET status = ?, parsed_count = ?, failed_count = ?, error_msg = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (status,
                 current["parsed_count"] if parsed_count is None else parsed_count,
                 current["failed_count"] if failed_count is None else failed_count,
                 error_msg,
                 now,
                 job_id),
            )



    def link_job_document(
        self,
        job_id:str,
        document_id:str,
        status:str,
        error_msg:str | None=None
        ):
        """关联入库任务和文档"""
        now = utc_now_iso()
        with self._connect() as conn:
            #写入该文档在本次任务中的状态和错误，同时保留首次相关的创建时间
            conn.execute(
                """
                INSERT OR REPLACE INTO ingest_job_documents(
                    job_id, document_id, status, error_msg, created_at, updated_at
                ) VALUES (
                    ?,
                    ?,
                    ?,
                    ?,
                    COALESCE(
                        (
                            SELECT created_at
                            FROM ingest_job_documents
                            WHERE job_id = ?
                            AND document_id = ?
                        ),
                        ?
                    ),
                    ?
                )
                """,
                (job_id, document_id, status, error_msg, job_id, document_id, now, now),
            )



    def get_job(self,job_id:str)->dict[str,Any]:
        """获取单个入库任务"""
        with self._connect() as conn:
            row=conn.execute("select * from ingest_jobs where job_id=?",(job_id,)).fetchone()
        if row is None:
            raise KeyError(f"没有找到入库任务:{job_id}")
        return dict(row)



    def job_detail(self,kb_id:str,job_id:str)->dict[str,Any]:
        """获取入库任务和关联文档"""
        with self._connect() as conn:
            #获取指定kb的入库任务
            job = conn.execute(
                "SELECT * FROM ingest_jobs WHERE kb_id = ? AND job_id = ?",
                (kb_id, job_id),
            ).fetchone()
            if job is None:
                raise KeyError(f"job not found: {job_id}")

            #获取某个入库任务包含的文档，以及每个文档在该任务中的处理状态
            rows = conn.execute(
                """
                SELECT jd.status AS job_document_status, jd.error_msg AS job_error_msg,
                       d.*, kb.name AS kb_name
                FROM ingest_job_documents jd
                JOIN documents d ON d.document_id = jd.document_id
                JOIN knowledge_bases kb ON kb.kb_id = d.kb_id
                WHERE jd.job_id = ?
                ORDER BY d.created_at
                """,
                (job_id,),
            ).fetchall()

            payload=dict(job)
            #给documents添加几条元数据
            payload["documents"]= [self._document_public_from_row(row) | {
            #合并上文档入库状态
            "job_document_status": row["job_document_status"],
            #合并上入库错误信息
            "job_error_msg": row["job_error_msg"],
            } for row in rows]
            #添加返回数量
            payload["total"]=len(rows)

            return payload





    def _connect(self):
        """连接到 SQLite 数据库，并启用外键约束。"""
        conn = sqlite3.connect(self.db_path,timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn




#================一些static方法====================
    @staticmethod
    def ensure_knowledge_base_dirs(meta:KnowledgeBaseMeta)->None:
        """创建知识库运行完整目录：source/storage/logs"""
        for directory in (meta.root_dir, meta.storage_dir, meta.sources_dir, meta.logs_dir):
            directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def validate_kb_id(kb_id: str) -> None:
        """校验知识库kb_id是否合法
        外部 kb_id
        → validate_kb_id()
        → 验证通过
        → knowledge_bases_dir / kb_id
        → 才允许创建目录
        """
        if not isinstance(kb_id, str) or _KB_ID_RE.fullmatch(kb_id) is None:
            raise ValueError(
                "kb_id 只能包含字母、数字、下划线和连字符"
            )

    @staticmethod
    def _random_id() -> str:
        """生成短随机 ID。"""

        import uuid
        return uuid.uuid4().hex[:12]
