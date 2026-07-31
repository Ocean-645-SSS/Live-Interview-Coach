# M3-D Parser Test Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐 PDF、DOCX、PPTX、XLSX 的解析边界测试和上传失败状态测试，并据此确认 M3-D 剩余验收项。

**Architecture:** `tests/rag/test_doc_parser.py` 负责纯解析器契约，使用内存生成的最小真实文件，覆盖正常、空内容、损坏和加密边界。`tests/rag/test_client.py` 负责 RAG Core 上传集成契约，验证原文件保留、错误元数据、job 状态以及失败文件不进入索引；管理 API 转发契约继续由 `tests/api/` 覆盖。

**Tech Stack:** pytest、FastAPI TestClient、pypdf、python-docx、python-pptx、openpyxl

## Global Constraints

- 不批量删除任何文件或目录。
- 保持 `parse_file_content(file_bytes: bytes, extension: str, **kwargs) -> str` 兼容。
- 每种二进制格式至少验证正常文本、空内容、损坏文件和加密文件错误。
- 解析失败必须保留原文件与错误元数据，且不得提交 LightRAG。
- 测试不得依赖外部网络或在线模型。

---

### Task 1: Complete parser contract coverage

**Files:**
- Modify: `tests/rag/test_doc_parser.py`

**Interfaces:**
- Consumes: `parse_file_content(file_bytes: bytes, extension: str, **kwargs: object) -> str`
- Produces: PDF、DOCX、PPTX、XLSX 的完整边界回归测试

- [ ] **Step 1: Add minimal real fixtures**

使用 `BytesIO` 与各格式库生成正常、空白和加密测试内容；损坏文件使用固定无效字节。

- [ ] **Step 2: Cover format-specific extraction**

验证 DOCX 段落与表格顺序、PPTX 文本框与表格、XLSX 多工作表与空单元格、PDF 密码分类及 Docling 回退。

- [ ] **Step 3: Run parser tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\rag\test_doc_parser.py -q`

Expected: all tests pass.

### Task 2: Cover upload failure persistence and indexing isolation

**Files:**
- Modify: `tests/rag/test_client.py`

**Interfaces:**
- Consumes: `POST /v1/knowledge-bases/{kb_id}/documents/files`
- Produces: 文档、job 和 LightRAG 调用的集成断言

- [ ] **Step 1: Upload one valid and one corrupt binary document**

构造同批次上传，确认有效文档继续解析，损坏文档标记失败。

- [ ] **Step 2: Assert persistence and job metadata**

验证失败文档的原文件存在、`parse_failed`、`error_msg`、job `failed_count` 和文档关联状态。

- [ ] **Step 3: Assert failed content is not indexed**

检查 fake engine 收到的文本和文档 ID，仅包含成功文档。

- [ ] **Step 4: Run RAG HTTP tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\rag\test_client.py -q`

Expected: all tests pass.

### Task 3: Verify management upload proxy and M3-D acceptance

**Files:**
- Modify: `tests/api/test_rag_proxy.py`
- Review: `docs/superpowers/plans/2026-07-16-liverag-reproduction.md`

**Interfaces:**
- Consumes: `POST /rag/knowledge-bases/{kb_id}/documents/files`
- Produces: 文件与 `pdf_password` 转发契约、M3-D 剩余事项清单

- [ ] **Step 1: Test management API upload forwarding**

验证上传文件和可选 `pdf_password` 被传给 `RagGateway.post_files()`。

- [ ] **Step 2: Run focused M3-D suites**

Run: `.\.venv\Scripts\python.exe -m pytest tests\rag\test_doc_parser.py tests\rag\test_client.py tests\api\test_rag_proxy.py -q`

Expected: all tests pass.

- [ ] **Step 3: Run static checks**

Run: `.\.venv\Scripts\ruff.exe check liverag\rag\doc_parser.py tests\rag\test_doc_parser.py tests\rag\test_client.py tests\api\test_rag_proxy.py`

Expected: no new lint errors in modified test/parser files.

- [ ] **Step 4: Report acceptance gaps**

对照 M3-D 要求报告解析、上传、索引、状态和查询中尚未由自动化测试证明的项目。
