"""读取启动配置，独立启动rag/server.py中定义的FastAPI应用
流程：
执行 liverag-rag-service
        ↓
进入 main()
        ↓
是否有 -h / --help？
   ├─ 是 → 打印帮助 → 返回
   └─ 否
        ↓
读取并校验 Settings
        ↓
Uvicorn 导入 liverag.rag.server:app
        ↓
FastAPI lifespan 初始化 RagEngineManager
        ↓
监听 settings.host:settings.port
        ↓
接收 RAG HTTP 请求
        ↓
Ctrl+C 后执行 lifespan 清理并退出
"""

import os
import sys

import uvicorn

from liverag.rag.rag_settings import RAGSettings


def main()->None:
    """启动LightRAG Core Service"""

    #用户求助用法
    if any(arg in {"--help","-h"} for arg in sys.argv[1:]): #查看用户传入的参数是否有--help/-h
        print("用法: uv run liverag-rag-service")
        print("环境变量: KB_SERVICE_HOST=127.0.0.1 KB_SERVICE_PORT=9721")
        return

    settings=RAGSettings()
    uvicorn.run(
        "liverag.rag.server:app",  #相当于from liverag.rag.server import app
        host=settings.host,
        port=settings.port,
        reload=False, #关闭代码修改之后的自动重载
        log_level=os.getenv("RAG_UVICORN_LOG_LEVEL","info") #日志等级
    )
