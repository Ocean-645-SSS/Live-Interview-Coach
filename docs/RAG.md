# Interview Coach 的 RAG

`liverag-rag-service` 是知识库唯一入口，负责文档解析、LightRAG 生命周期、索引、查询和元数据管理。Interview Coach 使用它读取候选人资料和目标岗位资料，生成有证据来源的画像与面试计划。

## 知识库边界

每个 `kb_id` 拥有独立的元数据、原始文件、解析结果、索引和任务状态。候选人知识库与目标岗位知识库在面试配置中分别引用，禁止跨库混合证据。

## 导入流程

1. 通过 `/rag/knowledge-bases` 创建知识库。
2. 上传文本或文件；加密 PDF 可随请求提供 `pdf_password`。
3. RAG Core 创建索引任务并返回 `job_id`。
4. 轮询任务与知识库就绪状态，成功后再创建 prepared interview。

上传成功只代表任务已接受，不代表内容已可检索。

## 查询约定

Interview Coach 使用 `POST /rag/knowledge-bases/{kb_id}/query/context` 获取上下文和来源证据。无命中时应明确表现为无证据，不把模型自身知识冒充为知识库事实。

主要环境变量：

```dotenv
LIVERAG_RAG_LLM_MODEL=
LIVERAG_RAG_LLM_BASE_URL=
LIVERAG_RAG_LLM_API_KEY=
LIVERAG_RAG_EMBEDDING_MODEL=
LIVERAG_RAG_EMBEDDING_BASE_URL=
LIVERAG_RAG_EMBEDDING_API_KEY=
LIGHTRAG_TIMEOUT_MS=120000
```

原始上传、索引目录和数据库卷都是运行数据，不应提交到 Git。
