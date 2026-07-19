"""定义RAG插入和查询链路的数据格式
把外部请求转换为RAGEngine能使用的规范格式
返回规范的HTTP响应"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#限制查询类型：
# local:实体局部知识；global：全体关系和主题；hybrid：结合局部和全局检索
# naive：普通文本块检索；mix：知识图谱和向量检索；bypass：不使用RAG检索
QueryMode=Literal["local","global","hybrid","naive","mix","bypass"]

#查询参数预设
# default:普通文本查询；voice：实时语音查询
# default的top_k比较大，允许references
# voice的top_k比较小，减少延迟
ProfileName=Literal["default","voice"]

#用于前端测试的QueryMode
SUPPORTED_MODES:list[str]=["local","global","hybrid","naive","mix","bypass"]



class QueryOptions(BaseModel):
    """单词查询可选的参数模型
    回答这次检索应该用的模式、去多少结果、允许多少token等"""
    mode:QueryMode | None=None #LightRAG查询模式
    top_k:int | None=Field(default=None,ge=1) #候选结果数量
    chunk_top_k:int | None=Field(default=None,ge=1) #最终选取多少个文本块

    #token限制
    max_entity_tokens:int | None= Field(default=None,ge=1) #实体上下文tokens
    max_relation_tokens:int | None= Field(default=None,ge=1) #关系上下文tokens
    max_total_tokens:int | None= Field(default=None,ge=1)

    enable_rerank:bool | None=None #是否对初步检索结果重新排序
    hl_keywords:list[str]=Field(default_factory=list) #高层、主题性关键词
    ll_keywords: list[str] = Field(default_factory=list) #低层、细节关键词
    include_references:bool = False #是否保留文档来源
    include_chunk_content:bool = False #是否保留chunk结果里的完整文本内容
    context_max_chars: int | None = Field(default=None, ge=1) #限制返回上下文的字符串
    response_type: str | None = None #传给LightRAG，控制回答形式



class ConversationOptions(BaseModel):
    """多轮查询辅助参数"""
    last_query:str | None = None #上一轮问题
    rewrite_followup:bool = True #是否允许把问题改写成完整问题



class QueryRequest(BaseModel):
    """完整的查询请求模型，对应HTTP请求体
    组合了：
    问题文本
    + 查询 profile
    + QueryOptions
    + ConversationOptions"""

    query:str=Field(min_length=1)
    profile:ProfileName="default"
    options:QueryOptions |None = None
    conversation:ConversationOptions |None=None

    #扁平化字段：用于兼容两种请求格式（嵌套+扁平）
    mode: QueryMode | None = None
    top_k: int | None = Field(default=None, ge=1)
    chunk_top_k: int | None = Field(default=None, ge=1)
    max_entity_tokens: int | None = Field(default=None, ge=1)
    max_relation_tokens: int | None = Field(default=None, ge=1)
    max_total_tokens: int | None = Field(default=None, ge=1)
    enable_rerank: bool | None = None
    hl_keywords: list[str] | None = None
    ll_keywords: list[str] | None = None
    include_references: bool | None = None
    include_chunk_content: bool | None = None
    context_max_chars: int | None = Field(default=None, ge=1)
    response_type: str | None = None
    #ConversationOptions的两个字段
    last_query: str | None = None
    rewrite_followup: bool | None = None

    @field_validator("query",mode="after")
    @classmethod
    def strip_query(cls,value:str)->str:
        """去除问题两端空白"""
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped

    def merged_options(self)->QueryOptions:
        """合并嵌套参数和扁平参数，扁平字段优先"""
        merged = self.options.model_dump() if self.options else {}
        for field_name in QueryOptions.model_fields:
            value = getattr(self, field_name, None)
            if value is not None:
                merged[field_name] = value
        return QueryOptions(**merged)

    def merged_conversation(self)->ConversationOptions:
        """合并多轮对话参数"""
        merged = self.conversation.model_dump() if self.conversation else {}
        if self.last_query is not None:
            merged["last_query"] = self.last_query
        if self.rewrite_followup is not None:
            merged["rewrite_followup"] = self.rewrite_followup
        return ConversationOptions(**merged)



class TextDocumentRequest(BaseModel):
    """直接提交纯文本的请求模型，让调用方直接传入已经解析好的文本"""
    text:str=Field(min_length=1)
    file_source:str | None = None
    document_id:str | None=None

    @field_validator("text", mode="after")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        """拒绝只包含空白字符的文档，同时保留有效正文的原始格式。"""
        if not value.strip():
            raise ValueError("text must not be blank")
        return value



class Envelope(BaseModel):
    """封装给HTTP的统一外壳->HTTP响应"""
    request_id:str
    status:Literal["ok","error"]
    data:dict[str,Any] | list[Any] | None = None #实际业务结果
    metrics:dict[str,Any]=Field(default_factory=dict) #耗时等指标，每次创建都生成一次dict()
    error:dict[str,Any] | None = None



class EvidenceDocument(BaseModel):
    model_config=ConfigDict(extra="allow")
    document_id: str | None = None
    file_path: str | None = None

class EvidenceChunk(BaseModel):
    model_config=ConfigDict(extra="allow")
    chunk_id: str | None = None
    document_id: str | None = None
    content: str | None = None
    score: float | None = None


class QueryResult(BaseModel):
    """查询结果模型"""
    kb_id: str
    hit: bool #检索是否有有效证据
    query:str
    effective_query:str
    rewritten:bool #是否重写问题
    has_context: bool #是否可以交给答案生成阶段的上下文
    context: str #整理后的检索上下文
    context_truncated:bool
    answer: str | None = None
    references: list[EvidenceDocument] = Field(default_factory=list)
    chunks: list[EvidenceChunk] = Field(default_factory=list)
    duration: float = Field(ge=0)
