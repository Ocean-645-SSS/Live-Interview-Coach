"""Live Interview Coach V1 的领域数据契约。

本文件只负责定义跨模块传递的数据结构和基础校验规则，不负责数据库读写、
状态迁移、模型调用或 HTTP 接口。状态机、题库、面试计划、评价服务和报告
服务都应复用这里的类型，避免同一个业务概念在不同模块中出现不同定义。
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


NonEmptyText = Annotated[str, Field(min_length=1)] #非空文本
NonNegativeInt = Annotated[int, Field(ge=0)]  #非负整数
PositiveInt = Annotated[int, Field(ge=1)]   #正整数
ScoreFromZeroToFour = Annotated[int, Field(ge=0, le=4)] #0-4分制评分


class InterviewState(str, Enum):
    """描述一次业务面试在受控流程中的当前阶段。"""

    CREATED = "CREATED"
    PREPARING = "PREPARING"
    READY = "READY"
    INTRODUCTION = "INTRODUCTION"
    ASKING = "ASKING"
    LISTENING = "LISTENING"
    EVALUATING = "EVALUATING"
    FOLLOW_UP = "FOLLOW_UP"
    NEXT_QUESTION = "NEXT_QUESTION"
    COMPLETING = "COMPLETING"
    COMPLETED = "COMPLETED"
    PAUSED = "PAUSED"     #已暂停
    ABORTED = "ABORTED"   #已终止
    FAILED = "FAILED"


class InterviewDifficulty(str, Enum):
    """限定题目和面试配置可使用的难度等级。"""

    BEGINNER = "BEGINNER"   #入门
    JUNIOR = "JUNIOR"   #初级
    INTERMEDIATE = "INTERMEDIATE"   #中级
    SENIOR = "SENIOR"   #高级
    EXPERT = "EXPERT"   #专家


class QuestionType(str, Enum):
    """标识问题的主要考察方式，供计划和前端展示使用。"""

    INTRODUCTION = "INTRODUCTION"   #自我介绍
    TECHNICAL_KNOWLEDGE = "TECHNICAL_KNOWLEDGE"   #技术知识
    PROJECT_DEEP_DIVE = "PROJECT_DEEP_DIVE"   #项目深入
    SYSTEM_DESIGN = "SYSTEM_DESIGN"   #系统设计
    SCENARIO = "SCENARIO"   #场景题
    BEHAVIORAL = "BEHAVIORAL"   #行为分析
    FOLLOW_UP = "FOLLOW_UP"   #追问


class QuestionSource(str, Enum):
    """记录一道题目的生成依据，保证计划可以审计。"""

    QUESTION_BANK = "QUESTION_BANK"   #结构化题库
    INTERVIEW_CONFIG = "INTERVIEW_CONFIG"   #面试配置
    FOLLOW_UP_GENERATED = "FOLLOW_UP_GENERATED"   #追问生成


class FollowUpAction(str, Enum):
    """评价完成后，编排器可以执行的下一步动作。"""

    FOLLOW_UP = "FOLLOW_UP"   #追问
    NEXT_QUESTION = "NEXT_QUESTION"   #下一个问题
    CLARIFY = "CLARIFY"   #理解用户的回答，消除歧义
    END = "END"   #结束

class TimeoutAction(str, Enum):
    """规定用户长时间无回答时的确定性处理方式。"""

    PAUSE = "PAUSE"   #暂停
    SKIP_QUESTION = "SKIP_QUESTION"   #跳过问题
    END_INTERVIEW = "END_INTERVIEW"   #结束面试


class StrictModel(BaseModel):
    """所有 Interview 领域模型的共同基类。

    禁止额外字段可以尽早暴露题库、数据库或 LLM 结构化输出中的拼写错误；
    去除字符串首尾空白则可以减少无意义的数据差异。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RubricPoint(StrictModel):
    """一道题中可独立核对的评分要点。"""

    id: NonEmptyText = Field(description="在单道题内保持唯一的稳定标识")
    content: NonEmptyText = Field(description="候选人的回答应覆盖的知识或推理要点")
    weight: float = Field(default=1.0, gt=0, le=10, description="该要点在完整性评价中的相对权重")
    required: bool = Field(default=False, description="缺失时是否应优先触发追问")


class QuestionRubric(StrictModel):
    """定义一道题的客观评价依据和四维评分权重。"""

    expected_points: list[RubricPoint] = Field(min_length=1)
    technical_accuracy_weight: float = Field(default=0.35, ge=0, le=1)  # 技术准确性权重
    completeness_weight: float = Field(default=0.30, ge=0, le=1)  # 完整性权重
    clarity_and_structure_weight: float = Field(default=0.15, ge=0, le=1)  # 表达结构和可理解性权重
    job_relevance_weight: float = Field(default=0.20, ge=0, le=1)  # 工作相关性权重
    notes: str | None = Field(default=None, description="供评价服务理解题目边界的补充说明")

    @field_validator("expected_points")
    @classmethod
    def validate_unique_point_ids(cls, value: list[RubricPoint]) -> list[RubricPoint]:
        """确保同一道题内的评分要点标识不会重复。"""

        point_ids = [point.id for point in value]
        if len(point_ids) != len(set(point_ids)):
            raise ValueError("同一道题中的评分要点标识不能重复")
        return value

    @model_validator(mode="after")
    def validate_weight_total(self) -> QuestionRubric:
        """确保四个评价维度的权重之和严格等于 1。"""

        total_weight = (
            self.technical_accuracy_weight
            + self.completeness_weight
            + self.clarity_and_structure_weight
            + self.job_relevance_weight
        )
        if abs(total_weight - 1.0) > 1e-9:
            raise ValueError("四个评价维度的权重之和必须等于 1")
        return self


class InterviewConfig(StrictModel):
    """描述一场 V1 模拟面试的用户配置和流程上限。"""

    duration_minutes: int = Field(default=15, ge=5, le=120) #面试时长
    difficulty: InterviewDifficulty = InterviewDifficulty.INTERMEDIATE #面试难度：默认中级
    language: NonEmptyText = "zh-CN"
    question_count: int = Field(default=5, ge=1, le=30) #问题数量
    max_follow_ups_per_question: int = Field(default=2, ge=0, le=5) #最多追问次数
    answer_timeout_seconds: int = Field(default=180, ge=15, le=900) #答案超时时间
    timeout_action: TimeoutAction = TimeoutAction.PAUSE #超时动作
    topic_weights: dict[str, float] = Field(
        default_factory=dict,
        description="主题名称到相对权重的映射；V1 用于从结构化题库选题",
    )

    @field_validator("topic_weights")
    @classmethod
    def validate_topic_weights(cls, value: dict[str, float]) -> dict[str, float]:
        """拒绝空主题名称、负权重和全部为零的主题配置。"""

        if any(not topic.strip() for topic in value):
            raise ValueError("主题名称不能为空")
        if any(weight < 0 for weight in value.values()):
            raise ValueError("主题权重不能为负数")
        if value and sum(value.values()) <= 0:
            raise ValueError("至少一个主题的权重必须大于零")
        return value


class InterviewQuestion(StrictModel):
    """面试计划中一条可执行的问题定义。"""

    id: NonEmptyText
    order: PositiveInt  #问题在计划中的顺序，从 1 开始连续编号
    type: QuestionType  #问题类型
    source: QuestionSource  #问题来源
    difficulty: InterviewDifficulty #问题难度
    topics: list[NonEmptyText] = Field(min_length=1)   #涉及的技术主题
    question_text: NonEmptyText    #面试问题
    objective: NonEmptyText   #考察目标
    rubric: QuestionRubric  #结构化评分依据
    estimated_seconds: int = Field(default=180, ge=30, le=1800) #预计回答时长
    allow_follow_up: bool = True  #是否允许追问
    follow_up_hints: list[NonEmptyText] = Field(default_factory=list)  #追问方向，供实时 Agent 生成追问时参考

    @field_validator("topics")
    @classmethod
    def validate_unique_topics(cls, value: list[str]) -> list[str]:
        """在保留原顺序的同时拒绝重复主题。"""

        if len(value) != len(set(value)):
            raise ValueError("同一道题的主题不能重复")
        return value

    @model_validator(mode="after")
    def validate_follow_up_settings(self) -> InterviewQuestion:
        """禁止在关闭追问时继续携带不可执行的追问提示。"""

        if not self.allow_follow_up and self.follow_up_hints:
            raise ValueError("不允许追问的问题不能配置追问提示")
        return self


class InterviewPlan(StrictModel):
    """面试开始前冻结、供实时 Agent 顺序执行的完整计划。"""

    id: NonEmptyText
    title: NonEmptyText #面试标题
    introduction: NonEmptyText  #面试开场白
    config: InterviewConfig #面试配置
    questions: list[InterviewQuestion] = Field(min_length=1)    #问题列表
    closing_message: NonEmptyText   #面试结束语
    plan_version: PositiveInt = 1

    @model_validator(mode="after")
    def validate_plan_consistency(self) -> InterviewPlan:
        """保证题目数量、标识和执行顺序满足实时编排要求。"""

        if len(self.questions) != self.config.question_count:
            raise ValueError("问题列表数量必须与面试配置的问题数量一致")

        question_ids = [question.id for question in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("面试计划中的问题标识不能重复")

        actual_order = [question.order for question in self.questions]
        expected_order = list(range(1, len(self.questions) + 1))
        if actual_order != expected_order:
            raise ValueError("面试问题必须从 1 开始连续排序")
        return self


class DimensionScores(StrictModel):
    """保存回答在四个固定评价维度上的锚点分数。"""

    technical_accuracy: ScoreFromZeroToFour #技术准确性
    completeness: ScoreFromZeroToFour #覆盖多少个预期知识点
    clarity_and_structure: ScoreFromZeroToFour #表达结构和可理解性
    job_relevance: ScoreFromZeroToFour #工作相关性

    def calculate_weighted_score(self, rubric: QuestionRubric) -> float:
        """按照题目评分规则计算百分制得分，并保留两位小数。"""

        weighted_score = (
            self.technical_accuracy * rubric.technical_accuracy_weight #0.35
            + self.completeness * rubric.completeness_weight  #0.3
            + self.clarity_and_structure * rubric.clarity_and_structure_weight  #0.15
            + self.job_relevance * rubric.job_relevance_weight  #0.2
        )
        return round(weighted_score / 4 * 100, 2)


class AnswerEvaluation(StrictModel):
    """一段最终回答经过 rubric 核对后的结构化评价结果。"""

    answer_id: NonEmptyText
    question_id: NonEmptyText
    scores: DimensionScores
    weighted_score: float = Field(ge=0, le=100)
    covered_points: list[NonEmptyText] = Field(default_factory=list)    #达到的点
    missing_points: list[NonEmptyText] = Field(default_factory=list)    #缺失的点
    errors: list[NonEmptyText] = Field(default_factory=list)    #错误的点
    summary: NonEmptyText
    next_action: FollowUpAction
    follow_up_target: str | None = None
    follow_up_question: str | None = None

    @model_validator(mode="after")
    def validate_follow_up_decision(self) -> AnswerEvaluation:
        """保证只有追问或澄清动作才携带完整的追问内容。"""

        actions_requiring_question = {FollowUpAction.FOLLOW_UP, FollowUpAction.CLARIFY}
        if self.next_action in actions_requiring_question:
            if not self.follow_up_target or not self.follow_up_question:
                raise ValueError("追问或澄清动作必须提供追问目标和追问问题")
        elif self.follow_up_target is not None or self.follow_up_question is not None:
            raise ValueError("下一题或结束动作不能携带追问内容")
        return self


__all__ = [
    "StrictModel",
    "InterviewState",
    "InterviewDifficulty",
    "QuestionType",
    "QuestionSource",
    "FollowUpAction",
    "TimeoutAction",
    "RubricPoint",
    "QuestionRubric",
    "InterviewConfig",
    "InterviewQuestion",
    "InterviewPlan",
    "DimensionScores",
    "AnswerEvaluation",
]
