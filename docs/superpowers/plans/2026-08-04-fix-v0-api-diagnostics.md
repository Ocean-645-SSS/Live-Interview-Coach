# Fix V0 API Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Ruff and Pyright errors from the V0 API entrypoint and RAG gateway without changing their HTTP behavior.

**Architecture:** Keep `liverag/api/main.py` as the uvicorn CLI entrypoint and `liverag/api/rag_gateway.py` as the HTTP proxy/normalization boundary. Correct public imports and type declarations locally; preserve all request, response, retry, and normalization semantics.

**Tech Stack:** Python 3.10, FastAPI, aiohttp, uvicorn, pytest, Ruff, Pyright

## Global Constraints

- Do not delete files or directories in bulk.
- Preserve V0 routes, response envelopes, and RAG Core request behavior.
- Do not change dependency versions as part of this fix.

---

### Task 1: Correct the API entrypoint diagnostics

**Files:**
- Modify: `liverag/api/main.py`

**Interfaces:**
- Consumes: `sys.argv`, `os.getenv`, and `uvicorn.run`.
- Produces: unchanged `main() -> None` CLI behavior.

- [x] **Step 1: Reproduce the diagnostics**

Run: `.venv/Scripts/ruff.exe check liverag/api/main.py --no-cache` and `.venv/Scripts/pyright.exe liverag/api/main.py --pythonpath .venv/Scripts/python.exe --pythonversion 3.10`.

Expected: Ruff reports import/whitespace/newline errors and Pyright rejects `os.sys.argv`.

- [x] **Step 2: Use the public sys module and normalize formatting**

```python
import os
import sys

import uvicorn


def main() -> None:
    if any(arg in {"--help", "-h"} for arg in sys.argv[1:]):
        print("用法: uv run liverag-api")
        print("环境变量: LIVERAG_API_HOST=127.0.0.1 LIVERAG_API_PORT=9821")
        return

    uvicorn.run(
        "liverag.api.server:app",
        host=os.getenv("LIVERAG_API_HOST", "127.0.0.1"),
        port=int(os.getenv("LIVERAG_API_PORT", "9821")),
        reload=False,
    )
```

### Task 2: Correct RAG gateway types and lint findings

**Files:**
- Modify: `liverag/api/rag_gateway.py`
- Test: `tests/api/test_rag_gateway.py`
- Test: `tests/api/test_rag_proxy.py`

**Interfaces:**
- Consumes: `aiohttp.ClientSession`, `AppSettings`, upstream JSON/text/file responses.
- Produces: unchanged `GatewayResponse`, `GatewayFileResponse`, and `RagGateway` methods.

- [x] **Step 1: Reproduce the diagnostics and establish the regression baseline**

Run:

```powershell
.venv/Scripts/ruff.exe check liverag/api/rag_gateway.py --no-cache
.venv/Scripts/pyright.exe liverag/api/rag_gateway.py --pythonpath .venv/Scripts/python.exe --pythonversion 3.10
.venv/Scripts/python.exe -m pytest tests/api/test_rag_gateway.py tests/api/test_rag_proxy.py -q
```

Expected: Ruff and Pyright report the known diagnostics; all 16 focused tests pass.

- [x] **Step 2: Correct boolean annotations and document-detail narrowing**

```python
@staticmethod
def _headers(
    api_key: str,
    *,
    has_json: bool = False,
    has_form: bool = False,
) -> dict[str, str]:
    if has_json and has_form:
        raise ValueError("JSON body and multipart form cannot be sent together")

    headers: dict[str, str] = {}
    if has_json:
        headers["Content-Type"] = "application/json"
    if has_form:
        headers.pop("Content-Type", None)
    if api_key:
        headers["X-API-Key"] = api_key
    return headers
```

```python
status_value = data.get("status")
status_payload: dict[str, Any] = status_value if isinstance(status_value, dict) else {}
summary_source = {**status_payload, **data}
```

- [x] **Step 3: Remove lint-only defects without changing behavior**

Use a combined asynchronous context manager:

```python
async with (
    aiohttp.ClientSession(timeout=timeout) as session,
    session.get(
        target_url,
        headers=headers,
        params=self._query_params(params=params),
    ) as response,
):
```

Remove the unused `content_type` local from `get_file`, sort imports, and remove trailing whitespace.

- [x] **Step 4: Run complete verification**

```powershell
.venv/Scripts/ruff.exe check liverag/api/main.py liverag/api/rag_gateway.py --no-cache
.venv/Scripts/pyright.exe liverag/api/main.py liverag/api/rag_gateway.py --pythonpath .venv/Scripts/python.exe --pythonversion 3.10
.venv/Scripts/python.exe -m pytest tests/api -q
```

Expected: Ruff and Pyright report no errors; all API tests pass.

- [ ] **Step 5: Commit the fix**

```bash
git add liverag/api/main.py liverag/api/rag_gateway.py docs/superpowers/plans/2026-08-04-fix-v0-api-diagnostics.md
git commit -m "fix: clear V0 API diagnostics"
```
