"""面向前端的后端管理API启动入口：管理HTTP API、监听9821端口
管理知识库、文档、Session等

前端
  ↓ HTTP
管理 API（api/main.py，9821）
  ↓ HTTP
RAG Core（rag/cli.py，9721）

LiveKit
  ↓ 分配语音任务
语音 Agent（liverag/main.py）
  ↓ 查询
RAG Core（9721）"""

import os
import sys

import uvicorn


def main() -> None:
    """启动前端 API。"""

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
