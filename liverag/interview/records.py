"""Live Interview Coach 的持久化记录模型，和数据库表中参数一一对应

用来承接 SQLite 查询结果，只描述“数据库中保存了什么”，不执行 SQL，也不决定面试状态如何迁移。

`schemas.py/model.py 与本文件的区别：
- `schemas.py` 定义业务输入、输出和校验规则；
- `records.py` 定义从数据库读取后在 Python 中使用的不可变记录。
- `models.py` 定义数据库表和关系
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from liverag.interview.schemas import InterviewState


class AttemptState(str, Enum):
    """表示一次 LiveKit 房间连接尝试的生命周期。"""

    CREATED = "CREATED"
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    FAILED = "FAILED"


class AnswerState(str, Enum):
    """表示一份回答是否已经完成评价。"""

    RECEIVED = "RECEIVED"
    EVALUATING = "EVALUATING"
    EVALUATED = "EVALUATED"
    FAILED = "FAILED"


class ReportState(str, Enum):
    """表示面试报告的生成状态。"""

    PENDING = "PENDING"
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class JobStatus(str, Enum):
    """Background Job 的生命周期状态。"""

    PENDING = "PENDING"
    QUEUED = "QUEUED"   #入队Redis
    RUNNING = "RUNNING" #Worker抢到
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"   #attempt+1


def utc_now_iso() -> str:
    """返回带 UTC 时区信息的 ISO 8601 时间字符串。"""

    return datetime.now(timezone.utc).isoformat()


def generate_id(prefix: str) -> str:
    """生成带业务前缀的随机标识。
    prefix: 标识类型的英文前缀，只允许字母、数字和下划线。
    返回格式为 `<prefix>_<32位十六进制UUID>` 的字符串。
    """

    cleaned_prefix = prefix.strip()
    if not cleaned_prefix:
        raise ValueError("标识前缀不能为空")
    if not cleaned_prefix.replace("_", "").isalnum():
        raise ValueError("标识前缀只能包含字母、数字和下划线")
    if not cleaned_prefix.isascii():
        raise ValueError("标识前缀必须使用 ASCII 字符")
    return f"{cleaned_prefix}_{uuid4().hex}"


@dataclass(frozen=True, slots=True)
class InterviewRecord:
    """一场业务面试的顶层记录。

    该记录保存用户创建的面试配置和冻结后的计划。一次 Interview 可以因
    掉线而产生多个 Session attempt，但始终只有一个顶层 InterviewRecord。
    """

    id: str
    title: str  #面试标题
    state: InterviewState   #当前阶段
    config_json: str    #`InterviewConfig` 序列化后的 JSON
    plan_json: str | None   #`InterviewPlan` 序列化后的 JSON；计划生成前为 None
    version: int    #乐观锁版本，每次修改记录就递增
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class InterviewSessionRecord:
    """保存实时面试状态机的权威快照：真正开始语音面试的执行进度

    Session 与 LiveKit room 分离：用户断线后可以创建新的 attempt 和 room，
    但继续使用同一个 session、当前题目位置和状态版本。
    """

    id: str     #实时面试会话标识
    interview_id: str   #顶层业务面试标识
    state: InterviewState   #当前状态机状态
    resume_state: InterviewState | None   #暂停前的状态，未暂停时为 None
    current_question_index: int     #当前题目在计划中的索引
    current_question_id: str | None    #当前题目标识，尚未开始或已经结束时可为 None
    follow_up_count: int    #已经追问的次数
    version: int    #乐观锁版本，用于拒绝并发的过期更新
    started_at: str | None
    ended_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class InterviewAttemptRecord:
    """记录一次 LiveKit 房间连接尝试：描述当前网络链接如何

    一个业务 Session 可以对应多个 Attempt。例如用户网络中断后，旧 attempt
    标记为 DISCONNECTED，恢复操作会创建新 room 和新 attempt。
    """

    id: str
    session_id: str #所属实时面试会话标识
    room_name: str #本次连接使用的 LiveKit room name
    state: AttemptState #当前连接状态
    connected_at: str | None  #成功连接房间的时间
    disconnected_at: str | None #结束连接的时间
    error_message: str | None  #连接失败时保存的可诊断错误
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class InterviewAnswerRecord:
    """保存候选人对一道题的一次最终回答。

    Interim transcript 只用于前端实时展示，不写入这张权威记录。只有 STT
    确认的 final transcript 才会创建 Answer，以避免半句话被重复评价。
    """

    id: str
    session_id: str
    question_id: str
    attempt_id: str #收到回答时使用的 LiveKit attempt 标识
    answer_number: int  #同一道题的回答序号，首次回答为 1，clarify后可递增
    transcript: str #STT 确认的最终回答文本
    state: AnswerState  #回答的评价处理状态
    source_event_id: str #创建该回答的事件标识，用于幂等性重复事件消重
    started_at: str
    ended_at: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class InterviewEventRecord:
    """保存驱动状态机的一条不可变业务事件，用于幂等、恢复和审计

    状态机事件采用追加写而不是覆盖写。`id` 是全局幂等键，同一事件即使
    被 LiveKit 或客户端重复投递，也只能成功写入一次。
    """

    id: str
    session_id: str
    event_type: str #事件名称，例如 `answer_received`
    payload_json: str   #事件附带数据的 JSON 字符串
    state_before: InterviewState    #处理事件前的状态
    state_after: InterviewState     #处理成功后的状态；未改变状态时与 state_before 相同
    version_before: int     #处理事件前的 session 乐观锁版本
    version_after: int      #处理事件后的 session 乐观锁版本
    created_at: str


@dataclass(frozen=True, slots=True)
class InterviewReportRecord:
    """保存一场面试报告的生成状态和最终内容。"""

    id: str
    session_id: str
    state: ReportState  #报告生成状态
    content_json: str | None    #最终结构化报告的 JSON
    error_message: str | None   #报告生成失败时的错误信息
    created_at: str
    updated_at: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class BackgroundJobRecord:
    """保存一个后台异步任务的状态和结果。

    任务由 API 创建并写入 PostgreSQL，入队到 Redis 后由 Worker 异步消费。
    Redis 重启不会丢失已完成业务结果，最终幂等由 PostgreSQL 唯一约束保证。
    """

    id: str
    job_type: str   #任务类型，区分业务操作
    idempotency_key: str    #幂等键：保证不重复创建任务
    status: JobStatus   #任务生命周期状态
    business_resource_id: str   #关联的业务资源，方便查询此次任务由哪个对象创建
    payload_json: str   #任务携带的输入数据
    result_json: str | None #任务完成的结构化输出
    error_message: str | None   #错误信息
    attempt: int    #重试次数
    max_attempts: int   #最多重试次数
    started_at: str | None  #任务开始时间戳
    completed_at: str | None    #任务结束时间戳
    created_at: str
    updated_at: str


__all__ = [
    "AnswerState",
    "AttemptState",
    "BackgroundJobRecord",
    "InterviewAnswerRecord",
    "InterviewAttemptRecord",
    "InterviewEventRecord",
    "InterviewRecord",
    "InterviewReportRecord",
    "InterviewSessionRecord",
    "JobStatus",
    "ReportState",
    "generate_id",
    "utc_now_iso",
]
