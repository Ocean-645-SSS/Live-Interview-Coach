"""LiveKit 实时语音事件与面试业务控制器之间的适配层
接收LiveKit事件，播放TTS"""

from __future__ import annotations

import asyncio

from livekit.agents import Agent, ModelSettings, llm

from liverag.interview.controller import (
    InterviewAgentController,
    InterviewSpeech,
    InterviewSpeechKind,
)
from liverag.interview.schemas import InterviewState


class LiveKitInterviewAgent(Agent):
    """把 LiveKit 的播放和转写回调交给 InterviewAgentController。"""

    def __init__(self, controller: InterviewAgentController) -> None:
        # 面试问题由已经冻结的 InterviewPlan 决定，不让通用 LLM 自由聊天。
        super().__init__(instructions="按照面试计划逐题进行模拟面试。")
        self._controller = controller
        self._turn_lock = asyncio.Lock()  # 确保同一时间只处理一个面试流程事件

    async def on_enter(self) -> None:
        """加入房间后从数据库记录的位置开始或恢复面试。"""

        # 加锁，防止进入房间的同时受到用户回答
        async with self._turn_lock:
            # 获得旧状态
            state_before = self._controller.get_session().state
            # 决定进入房间后说的第一句话：开场白/当前题目/追问/结束语
            first_speech = self._controller.start()
            # 播放第一句话
            await self._play(first_speech)

            # 如果是第一次进入
            if first_speech.kind is InterviewSpeechKind.INTRODUCTION:
                # 返回第一题
                question = self._controller.introduction_spoken()
                # 播放第一题
                await self._play(question)
                # 进入listening状态，等待回答
                self._controller.prompt_spoken(question.kind)
            # 如果是面试准备结束
            elif first_speech.kind is InterviewSpeechKind.CLOSING:
                # 生成面试报告，面试结束
                self._controller.complete()
            # 判断用于恢复题目还是追问
            elif state_before is not InterviewState.LISTENING:
                # 进入listening状态，等待回答
                self._controller.prompt_spoken(first_speech.kind)

    async def on_user_turn_completed(
        self,
        turn_ctx: llm.ChatContext,  # 完整对话上下文
        new_message: llm.ChatMessage,  # 用户最新消息
    ) -> None:
        """用户说完一轮后，处理最终文字并播放追问、下一题或结束语。"""

        del turn_ctx
        # 获取最终转写
        transcript = (new_message.text_content or "").strip()
        if not transcript:
            return

        # 加锁
        async with self._turn_lock:
            # 保存answer，调用评价模型，根据评价结果决定下一步
            result = await self._controller.receive_final_answer(transcript)
            # 下一句话
            speech = result.next_speech
            # 播放下一句话
            await self._play(speech)

            # 如果下一句话是结束语
            if speech.kind is InterviewSpeechKind.CLOSING:
                # 生成最终报告，结束面试
                self._controller.complete()
            # 还没有结束
            else:
                # QUESTION->QUESTION_ASKED,ASKING->LISTENING
                # FOLLOW_UP->FOLLOW_IP_ASKED,FOLLOW_UP->LISTENING
                self._controller.prompt_spoken(speech.kind)

    async def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        model_settings: ModelSettings,
    ) -> None:
        """关闭默认自由回答：不让LiveKit自带的LLM发挥，要说的话已经由面试计划和评价结果确定"""

        # 不需要上下文、工具、模型配置
        del chat_ctx, tools, model_settings
        return None

    async def _play(self, speech: InterviewSpeech) -> None:
        """通过 TTS 播放一段文字，并等到声音真正播放完毕。"""

        handle = self.session.say(
            speech.text,
            allow_interruptions=False,  # 不允许被打断
            add_to_chat_ctx=True,  # 加入对话内容
        )
        await handle.wait_for_playout()


__all__ = ["LiveKitInterviewAgent"]
