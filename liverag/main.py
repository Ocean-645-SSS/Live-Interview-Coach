"""LiveRAG 统一启动入口
读取配置
→ 等待内置 RAG Core ready
→ 初始化 ContextStore
→ 创建本次 session
→ 渲染固定 Prompt
→ 创建 RagClient
→ 创建 ContextManager
→ 创建 VoiceAssistant
→ 创建 AgentSession
→ 连接 LiveKit 房间
→ 启动语音会话
→ 挂断后结束并保存 session
"""

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import logging
from typing import Any
from urllib.parse import quote

import aiohttp
from livekit.agents import AgentServer, JobContext,cli,room_io

from liverag.agent.assistant import VoiceAssistant
from liverag.agent.providers import build_agent_session
from liverag.agent.tool.rag_client import RagClient
from liverag.agent.metrics_hooks import MetricsState,register_session_metrics_hooks,start_network_probe_task
from liverag.rag.metadata_store import MetadataStore
from liverag.rag.service import wait_for_rag_ready,start_embedded_rag_service
from liverag.config.settings import load_environment,load_app_settings,AppSettings,public_voice_config
from liverag.context.store import ContextStore
from liverag.context.renderer import SessionPromptRenderer
from liverag.context.history import HistoryCompactor
from liverag.context.manager import ContextManager
from liverag.logging.setup import setup_logging
from liverag.logging.events import EventLogger
from liverag.runtime.paths import build_runtime_paths


load_environment()  #加载环境
setup_logging() #初始化日志格式
logger=logging.getLogger("agent")
server=AgentServer()    #语音Agent的任务服务器


@server.rtc_session(agent_name="my-agent") #当LiveKit收到一个分配给my-agent的语音任务时，调用my_agent函数
async def my_agent(ctx:JobContext)->None:   #ctx:本次通话在哪个房间
    """my-agent 在线语音入口
    LiveKit分配一次语音通话任务之后，为这次通话准备：配置、知识库、语音模型、上下文，
    然后让Agent加入房间；通话结束之后，保存和整理记录"""

    #读取程序配置:AppSettings
    settings=load_app_settings()

    #初始化本地存储ContextStore
    paths=build_runtime_paths(settings.user_data_dir)
    store=ContextStore(paths=paths)
    store.initialize()
    

    #等待RAG Core服务，to_thread表示单独放到线程运行，避免阻塞LiveKit异步事件循环
    ready_state=await asyncio.to_thread(wait_for_rag_ready,timeout_ms=settings.api.rag_ready_timeout_ms)
    #未准备就绪
    if not ready_state.ready:
        raise RuntimeError(f"RAG Core 未就绪：{ready_state.error or ready_state.status}")

    #初始化知识库元数据MetaData
    metadata_store=MetadataStore(paths.db_file,paths.rag_knowledge_bases_dir)
    metadata_store.initialize()
    #读取+预热知识库
    knowledge_base=await _resolve_knowledge_base(settings,metadata_store)

    #开启一段新对话session
    session_id=ctx.job.id   #获取session_id
    store.start_session(session_id=session_id,kb_id=knowledge_base["kb_id"])

    try:
        #创建本次通话事件日志
        session_stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        #构造日志路径:通话开始时间+LiveKit Job ID+Room ID
        metrics_log_path=(paths.logs_dir) / f"{session_stamp}_metrics_{ctx.job.id}_{ctx.job.room.sid}.jsonl"
        #创建日志对象
        event_logger=EventLogger(
            log_path=metrics_log_path,
            room_id=ctx.job.room.sid if ctx.job and ctx.job.room else None,
            job_id=ctx.job.id if ctx.job else None,
            agent_name="my-agent",
        )

        #创建语音会话流水线:VAD->STT->LLM->TTS
        session=build_agent_session(settings=settings)

        #创建RAG客户端
        rag_client=RagClient(
            settings=settings.rag,
            store=store,
            user_data_dir=paths.user_data_dir,
            kb_id=knowledge_base["kb_id"],
            kb_name=knowledge_base["name"],
        )

        #生成本次通话的固定系统Prompt：只生成一次
        prompt_result=SessionPromptRenderer(store=store,history_limit=settings.history_limit).render(
            session_id=session_id,
            kb_id=knowledge_base["kb_id"],
            kb_name=knowledge_base["name"],
            rag_tool_mode=settings.rag.rag_tool_mode
        )

        #记录知识库概览状态
        overview_state={
            "generated":False,  #本次启动没有生成概览
            "fallback":False,   #没有降级内容
            "reason":"startup_read_only",   #启动阶段只读取
            "meta":store.read_knowledge_overview_meta(knowledge_base["kb_id"]),
        }

        #把当前通话状态写入磁盘
        #提取可以安全保存的语音配置
        active_voice=public_voice_config(voice=settings.voice,effective="active_session")
        #读取当前配置
        state=store.read_runtime_state(session_id)
        #更新配置：
        state.update(
            {
                "active_session": {
                "started_at": datetime.now(timezone.utc).isoformat(),
                "job_id": ctx.job.id if ctx.job else None,
                "room_id": ctx.job.room.sid if ctx.job and ctx.job.room else None,
                "room": ctx.room.name if ctx.room else None,
                "voice": active_voice,
                "knowledge_base": {
                    "kb_id": knowledge_base["kb_id"],
                    "name": knowledge_base["name"],
                    "locked_at": datetime.now(timezone.utc).isoformat(),
                    "job_id": ctx.job.id if ctx.job else None,
                    "room_id": ctx.job.room.sid if ctx.job and ctx.job.room else None,
                },
                "session_prompt_chars": prompt_result.prompt_chars,
                "history_count": prompt_result.history_count,
                "knowledge_overview": overview_state,
            },
            "active_voice_model": active_voice,
            "model_pending_reconnect": False,
            "rag_tool_mode": settings.rag.rag_tool_mode,
            }
        )
        #写入新运行配置
        store.write_runtime_state(session_id=session_id,state=state)

        #写入结构化事件日志
        event_logger.append("model.active_session",{"voice":active_voice}) #当前语音模型
        event_logger.append("knowledge_base.active_session",knowledge_base) #当前知识库
        event_logger.append(
            "context.session_prompt.rendered",
            {
                "kb_id": prompt_result.kb_id,
                "kb_name": prompt_result.kb_name,
                "prompt_chars": prompt_result.prompt_chars,
                "history_count": prompt_result.history_count,
                "rag_tool_mode": prompt_result.rag_tool_mode,
                "overview_generated": False,
                "overview_fallback": False,
                "overview_generation_timing": "index_completed_only",
            }, #prompt渲染效果
        )

        #创建ContextManager
        context_manager=ContextManager(rag_client=rag_client,session_id=session_id,rag_tool_mode=settings.rag.rag_tool_mode)

        #创建VoiceAssistant
        assistant=VoiceAssistant(
            context_manager=context_manager, #保存消息和查询知识库
            session_system_prompt=prompt_result.prompt, #告诉模型怎样回答
            rag_tool_mode=settings.rag.rag_tool_mode, #决定模型能否自动调用知识库工具
            event_logger=event_logger, #记录事件和耗时
        )

        #创建历史压缩器
        history_compactor=HistoryCompactor(store=store,settings=settings.context_model)

        #加入LiveKit房间
        await ctx.connect()

        #注册指标监控
        metrics_state=MetricsState()
        #注册session指标回调
        register_session_metrics_hooks(session,logger,event_logger,metrics_state)
        #启动网络探测
        probe_task = start_network_probe_task(
            livekit_url=settings.voice.livekit_url,
            state=metrics_state,
            logger=logger,
            metrics_logger=event_logger,
        )

        #定义通话结束处理
        async def _finalize_session(reason:str="")->None:
            """结束当前通话，压缩通话内容并且更新Session状态"""

            if probe_task is not None:
                probe_task.cancel()
                with suppress(asyncio.CancelledError):
                    await probe_task

            history_result:dict[str, Any] | None = None

            try:
                #压缩历史消息
                history_result = await history_compactor.compact_after_call(
                    session_id=session_id,
                    kb_id=knowledge_base["kb_id"],
                    kb_name=knowledge_base["name"],
                )
            
                event_logger.append(
                    "session.finalized",
                    {
                        "session_id":session_id,
                        "result":history_result,
                        "reason":reason,
                    },
                )
            finally:
                store.end_session(session_id=session_id,state="ended")

        #注册结束回调
        ctx.add_shutdown_callback(_finalize_session)

        #正式启动语音Agent
        await session.start(
            agent=assistant,
            room=ctx.room,
            room_options=room_io.RoomOptions(
                audio_input=room_io.AudioInputOptions(sample_rate=16000), #采样频率，16kHz用于语音识别
            ),
        )
    #任何步骤失败
    except Exception:
        store.end_session(session_id=session_id,state="failed")
        logger.exception("agent.session_failed",extra={"session_id":session_id})
        raise

async def _resolve_knowledge_base(settings:Any,metadata_store:MetadataStore) -> dict[str,str]:
    """读取并且预热本次会话锁定的知识库。
    1.读取之前选择的知识库。
    2.向 RAG Core 查询它是否存在。
    3.不存在则退回 default。
    4.预热最终选定的知识库。
    5.返回知识库 ID 和名称。"""

    #读取之前保存的知识库
    configured=metadata_store.get_session_config("knowledge_base")

    #清洗知识库ID
    kb_id=(str(configured.get("kb_id") or "default").strip() or "default")

    #查询知识库详情
    detail=await _fetch_knowledge_base(settings,kb_id)
    #首选知识库失败时回退
    if detail is None and kb_id!="default":
        kb_id="default"
        detail=await _fetch_knowledge_base(settings,kb_id)
    #如果RAG Core依旧查询失败
    if detail is None:
        detail={"kb_id":"default","name":"默认知识库"}
    #预热知识库
    await _preheat_knowledge_base(settings,str(detail["kb_id"]))

    #保存最终选择
    metadata_store.set_session_config(
        "knowledge_base",
        {
            "kb_id":str(detail["kb_id"]),
            "name":str(detail["name"]),
        }
    )

    #返回结果
    return {"kb_id":str(detail["kb_id"]),"name":str(detail["name"])}


async def _fetch_knowledge_base(settings:Any,kb_id:str) -> dict[str,Any]|None:
    """从内部RAG服务查询知识库详情"""

    response=await _rag_get(settings,f"/v1/knowledge-bases/{quote(kb_id,safe='')}")
    if not isinstance(response,dict) or response.get("status")!="ok":
        return None
    payload=response.get("data")
    return payload if isinstance(payload,dict) else None


async def _preheat_knowledge_base(settings:Any,kb_id:str)->None:
    """预热知识库
    路径举例：/v1/knowledge-bases/default/ready
    最终：http://127.0.0.1:9721/v1/knowledge-bases/default/ready"""

    await _rag_get(settings, f"/v1/knowledge-bases/{quote(kb_id, safe='')}/ready")

async def _rag_get(settings:AppSettings,path:str)->dict[str,Any]|None:
    """输入接口路径，自动补上RAG Core地址、API Key和超时时间，
    然后发送一次GET请求，返回成功字典"""

    #请求头
    headers=({"X-API-Key":settings.rag.api_key} if settings.rag.api_key else {})
    #拼接完整url
    url=f"{settings.rag.base_url.rstrip('/')}{path}"
    #设置请求超时
    timeout=aiohttp.ClientTimeout(total=max(1000,settings.api.rag_ready_timeout_ms)/1000.0)

    try:
        #创建HTTP客户端并发送GET请求
        async with aiohttp.ClientSession(timeout=timeout) as session, session.get(url,headers=headers) as response:
            if response.status!=200:
                return None
            #正常响应
            payload=await response.json()
    except Exception as e:
        #记录错误日志
        logger.warning(
            "knowledge_base.resolve_failed",
            extra={
                "path":path,
                "error":str(e)
            }
        )
        return None

    return payload if isinstance(payload, dict) else None


def main():
    cli.run_app(server)

if __name__ == "__main__":
    main()
