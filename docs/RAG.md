# RAG 说明

## 服务定位

`liverag/rag/` 是基于 `lightrag-hku` Core API 的内置 RAG Core Service（端口 9721），不使用官方 WebUI。Agent 和管理 API 启动时都会自动检查并拉起该服务。

通用语音助手和 Interview Coach 都通过 `RagGateway` 复用此层。通用助手用它做实时知识检索，Interview Coach 用它生成候选人画像和岗位画像。

---

## 存储模型

LiveRAG 使用 SQLite 保存产品元数据，使用每知识库独立目录保存原文件和 LightRAG 派生索引。

```text
~/.LiveRAG/
  liverag.db                                    # SQLite 元数据 (MetadataStore)
  rag/knowledge_bases/
    default/                                    # 不可删除的个人简历知识库
      sources/{document_id}/{original_filename} # 原始文件
      storage/                                  # LightRAG 向量/图谱索引
      logs/                                     # LightRAG 日志
    kb_xxx/                                     # 其他知识库（如公司岗位 JD 库）
      sources/{document_id}/{original_filename}
      storage/
      logs/
```

SQLite 只保存元数据（文档名、状态、大小、SHA256 等），不保存原文件二进制、向量、图谱或 chunk 原文。

---

## 单知识库隔离

- 每个知识库对应一个独立 LightRAG workspace。
- 通用助手一次语音通话只锁定一个 `kb_id`，通话中禁止切换。
- 查询不会同时访问多个知识库，不做多库 fan-out 或结果合并。
- 速度依赖预热后的 per-KB engine cache。
- Interview Coach 创建面试时明确指定 `candidate_kb_id`（固定为 `default` 个人简历库）和 `target_kb_id`（目标岗位资料库），两个 KB 分别检索、不混合。

---

## 上传与索引流程

文件上传后，后端按以下顺序处理：

1. 生成 `document_id`。
2. 保存原文件到 `sources/{document_id}/`。
3. 计算大小、SHA256、扩展名和 content type。
4. 写入 SQLite，状态为 `parse_status=pending`、`index_status=pending`。
5. 解析原文件（支持 UTF-8 文本、PDF、DOCX、PPTX、XLSX、Markdown 等）。
6. 解析成功后写入 LightRAG，进入异步索引队列。
7. 解析或索引失败时保留原文件，并把失败原因写入 `error_msg`。
8. 文档变更成功后，标记 `context/{kb_id}/knowledge_overview_meta.json.stale=true`。

---

## 知识库概览

每个知识库有一份固定概览：

```text
~/.LiveRAG/context/{kb_id}/knowledge_overview.md
~/.LiveRAG/context/{kb_id}/knowledge_overview_meta.json
```

概览由独立 Context Model 生成，输入包括：
- 知识库 metadata
- 文档列表
- LightRAG 提供的 topics、entities、relations、documents overview
- 当前 RAG 查询参数

概览不会在通话启动时生成，也不提供前端手动触发接口。固定生成时机是：文件索引任务完成，并且至少有一个新文档进入 `processed` 状态。

前端上传后应轮询 `GET /rag/knowledge-bases/{kb_id}/jobs/{job_id}` 等待完成。生成失败时写入降级概览，不阻断文档管理和后续通话。

---

## 语音查询参数

语音链路默认使用低延迟参数：

```bash
LIGHTRAG_QUERY_MODE=naive
LIGHTRAG_TOP_K=4
LIGHTRAG_CHUNK_TOP_K=4
LIGHTRAG_CONTEXT_MAX_CHARS=1800
LIGHTRAG_VOICE_ENABLE_RERANK=false
```

如果需要更高召回，可以提高 `top_k`、`chunk_top_k` 或切换查询模式，但会增加延迟。

---

## RAG 工具模式（通用语音助手）

只支持两种：
- `auto`：通话前把工具说明渲染进 `session_system_prompt.md`，并向 LLM 提供 `search_knowledge_base` 工具。
- `never`：通话前写入禁用说明，不向 LLM 提供知识库工具。

不支持强制每轮检索。

---

## Interview Coach 中的 RAG 使用

Interview Coach 在**面试准备阶段**使用 RAG，不在实时语音主链路中调用：

1. **候选人画像**：从 `default` 知识库（个人简历）检索，提取技能、项目经历、evidence refs → `CandidateProfile`
2. **岗位画像**：从目标岗位知识库检索，提取公司、岗位、技能要求、evidence refs → `JobProfile`
3. **画像冻结**：生成的 `CandidateProfile` 和 `JobProfile` 作为快照保存在 `InterviewPlan` 中，之后资料更新不会影响已开始的面试

实时面试链路**不调用 RAG**。所有需要的事实信息已经在 Plan 生成时冻结。

**RAG 边界约定：**

适合进入 RAG：简历、项目文档、README、技术总结、JD 等非结构化候选人材料。

不进入 RAG：固定题库、rubric、expected points、状态机规则、配置、评分权重、结构化 SkillProgress。这些需要稳定版本、精确过滤和确定性读取，向量召回无法保证完整性或唯一性。

---

## 前端接口

前端只对接管理 API（9821），不直接访问 RAG Core（9721）：

```text
GET    /rag/knowledge-bases
POST   /rag/knowledge-bases
GET    /rag/knowledge-bases/{kb_id}
PATCH  /rag/knowledge-bases/{kb_id}
DELETE /rag/knowledge-bases/{kb_id}
GET    /rag/knowledge-bases/{kb_id}/documents
POST   /rag/knowledge-bases/{kb_id}/documents/files
POST   /rag/knowledge-bases/{kb_id}/documents/text
GET    /rag/knowledge-bases/{kb_id}/documents/{document_id}
GET    /rag/knowledge-bases/{kb_id}/documents/{document_id}/source
DELETE /rag/knowledge-bases/{kb_id}/documents/{document_id}
DELETE /rag/knowledge-bases/{kb_id}/documents
GET    /rag/knowledge-bases/{kb_id}/jobs/{job_id}
GET    /rag/knowledge-bases/{kb_id}/context/overview
PUT    /rag/knowledge-bases/{kb_id}/context/overview
POST   /rag/knowledge-bases/{kb_id}/query/context
POST   /rag/knowledge-bases/{kb_id}/query/data
POST   /rag/session-query/context
POST   /rag/session-query/data
```

完整字段见 [API.md](./API.md)。
