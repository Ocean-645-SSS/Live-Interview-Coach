"""Auditable Volcengine BigModel streaming STT.

This module deliberately reuses the upstream plugin's protocol builders and
response conversion.  It only owns observability and task lifecycle so the
audio path can be proven without logging PCM data.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import struct
import time
from typing import Any

import aiohttp
from livekit import rtc
from livekit.agents import APIStatusError, utils
from livekit.plugins.volcengine import bigmodel_stt

from liverag.agent.hot_words import inject_hot_words_into_initial_request

logger = logging.getLogger(__name__)


def _exception_text(exc: BaseException | None) -> str:
    return "" if exc is None else f"{type(exc).__name__}: {exc}"


class AuditedSpeechStream(bigmodel_stt.SpeechStream):
    """Upstream speech stream with per-hop structured diagnostics."""

    # ASR 会话在长时间无识别结果后会进入静默死状态：
    # WebSocket 连接依然存活，音频照常发送，但火山不再返回
    # 任何识别结果。只能通过重建 WebSocket（=重建 ASR 会话）
    # 来恢复。以下参数控制自动恢复行为。
    _RECONNECT_AFTER_IDLE_SECONDS = 30.0  # 无识别结果超过此时间 → 自动重连

    def __init__(self, **kwargs: Any) -> None:
        # ⚠️ 必须在 super().__init__() 之前 pop 掉自定义参数，
        # 否则父类 SpeechStream.__init__ 收到不认识的参数会炸。
        self._hot_words_json: str = kwargs.pop("hot_words_json", "")
        super().__init__(**kwargs)
        self.stream_id = utils.shortuuid()
        self.total_frames_received = 0
        self.total_frames_sent = 0
        self.total_audio_bytes_sent = 0
        self.total_responses_received = 0
        self._seen_error_responses: set[str] = set()
        self._send_task: asyncio.Task[None] | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._closed_logged = False
        self._closing_ws = False  # 对齐官方 closing_ws：阻止正常关闭时重连
        # 自愈机制：跟踪最近一次识别结果的时间
        self._last_recognition_at: float = 0.0  # loop.time()
        logger.info("volcengine.stt.stream_created", extra={"stream_id": self.stream_id})

    def trigger_reconnect(self) -> None:
        """强制触发 STT 重连，在新回答窗口开始时调用以重建 ASR 会话。"""
        self._reconnect_event.set()
        logger.info(
            "volcengine.stt.reconnect_triggered",
            extra={"stream_id": self.stream_id},
        )

    def _maybe_auto_reconnect(self) -> None:
        """如果连续无识别结果超过阈值，自动触发重连重建 ASR 会话。"""
        if self._last_recognition_at <= 0:
            return
        idle = time.monotonic() - self._last_recognition_at
        if idle > self._RECONNECT_AFTER_IDLE_SECONDS:
            logger.warning(
                "volcengine.stt.auto_reconnect_idle",
                extra={
                    "stream_id": self.stream_id,
                    "idle_seconds": round(idle, 1),
                },
            )
            self.trigger_reconnect()

    def push_frame(self, frame: rtc.AudioFrame) -> None:
        self.total_frames_received += 1
        logger.info(
            "volcengine.stt.frame_received",
            extra={
                "stream_id": self.stream_id,
                "frame_type": f"{type(frame).__module__}.{type(frame).__name__}",
                "samples_per_channel": getattr(frame, "samples_per_channel", None),
                "sample_rate": getattr(frame, "sample_rate", None),
                "num_channels": getattr(frame, "num_channels", None),
            },
        )
        super().push_frame(frame)
        logger.info(
            "volcengine.stt.frame_enqueued",
            extra={"stream_id": self.stream_id, "queue_size": self._input_ch.qsize()},
        )

    async def _send_packet(
        self,
        ws: aiohttp.ClientWebSocketResponse,
        packet: bytes | bytearray,
        *,
        sequence: int,
        payload_bytes: int,
        is_last: bool,
        audio_bytes: int = 0,
    ) -> None:
        logger.info(
            "volcengine.stt.packet_sending",
            extra={
                "stream_id": self.stream_id,
                "sequence": sequence,
                "payload_bytes": payload_bytes,
                "is_last": is_last,
            },
        )
        await ws.send_bytes(bytes(packet))
        if audio_bytes:
            self.total_frames_sent += 1
            self.total_audio_bytes_sent += audio_bytes
        logger.info(
            "volcengine.stt.packet_sent",
            extra={"stream_id": self.stream_id, "sequence": sequence},
        )

    # 火山 ASR 会话在 15-20 秒无音频输入后会超时（45000081）。
    # 面试场景中，TTS 播放和评估阶段可能持续 30+ 秒，导致 ASR 会话
    # 在下一个回答窗口开始时已失效。
    _KEEP_ALIVE_SECONDS = 10.0  # 每 10 秒发送一次静默帧保活

    async def _send_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """对齐官方 ``send_task`` 的 has_ended 信令 + 诊断日志。

        关键改动：
        1. 初始 Full Client Request 延迟到第一帧音频到达后才发送。
           避免面试场景下 TTS 播放期间（麦克风输入关闭）火山服务端会话
           因长时间无音频而超时（45000081）。
        2. 静默保活：当 ASR 会话激活后，若超过 10 秒无音频输入，
           发送 100ms 的静默 PCM 帧以防止火山会话超时。
        """
        reason = "input_channel_closed"
        failure: BaseException | None = None
        logger.info(
            "volcengine.stt.send_loop_started",
            extra={"stream_id": self.stream_id},
        )

        try:
            audio_bstream = utils.audio.AudioByteStream(
                sample_rate=self._opts.sample_rate,
                num_channels=self._opts.num_channels,
                samples_per_channel=self._opts.sample_rate // 10,
            )

            # 预计算静默保活帧（100ms 的 16-bit PCM 静音）
            _silence_samples = self._opts.sample_rate // 10  # 100ms
            _silence_pcm = (
                b"\x00" * (_silence_samples * self._opts.num_channels * 2)
            )

            # ── 音频帧循环（延迟初始化握手 + 静默保活 + 自愈重连） ──
            initial_sent = False
            sequence = 1
            ait = self._input_ch.__aiter__()
            while True:
                try:
                    data = await asyncio.wait_for(
                        ait.__anext__(),
                        timeout=self._KEEP_ALIVE_SECONDS,
                    )
                except asyncio.TimeoutError:
                    # 长时间无音频 → 发送静默帧保活
                    if not initial_sent:
                        # ASR 会话未激活，无需保活
                        self._maybe_auto_reconnect()
                        continue
                    logger.info(
                        "volcengine.stt.keep_alive_sending",
                        extra={
                            "stream_id": self.stream_id,
                            "seconds_idle": self._KEEP_ALIVE_SECONDS,
                        },
                    )
                    # ⚠️ 关键：直接构造包发送，不能通过 audio_bstream.write()
                    # 否则静音数据会残留在 audio_bstream 缓冲区中，
                    # 与后续真实音频混合，导致火山 ASR 无法识别。
                    sequence += 1
                    packet = self._opts.get_chunk_request(
                        _silence_pcm, seq=sequence, last=False
                    )
                    logger.info(
                        "volcengine.stt.keep_alive_sent",
                        extra={
                            "stream_id": self.stream_id,
                            "sequence": sequence,
                            "audio_bytes": len(_silence_pcm),
                        },
                    )
                    await self._send_packet(
                        ws,
                        packet,
                        sequence=sequence,
                        payload_bytes=len(packet),
                        is_last=False,
                        audio_bytes=len(_silence_pcm),
                    )
                    self._maybe_auto_reconnect()
                    continue
                except StopAsyncIteration:
                    # 通道关闭，正常退出
                    break
                if not initial_sent:
                    initial_sent = True
                    # 等第一帧音频到达后才发送 Full Client Request，
                    # 确保火山会话创建与音频流开始时间对齐
                    initial = self._opts.get_ws_query_params(uid=self._request_id)
                    # 注入热词（火山 ASR corpus.context）
                    initial = inject_hot_words_into_initial_request(
                        initial, self._hot_words_json
                    )
                    logger.info(
                        "volcengine.stt.initial_content",
                        extra={
                            "stream_id": self.stream_id,
                            "initial_hex": bytes(initial).hex(),
                            "initial_text": bytes(initial).decode(
                                "utf-8", errors="replace"
                            ),
                        },
                    )
                    await self._send_packet(
                        ws,
                        initial,
                        sequence=1,
                        payload_bytes=len(initial),
                        is_last=False,
                    )
                    logger.info(
                        "volcengine.stt.initial_sent_after_first_audio",
                        extra={"stream_id": self.stream_id},
                    )
                is_flush = isinstance(data, self._FlushSentinel)
                logger.info(
                    "volcengine.stt.flush_check",
                    extra={
                        "stream_id": self.stream_id,
                        "is_flush": is_flush,
                        "input_type": type(data).__name__,
                    },
                )

                frames: list[rtc.AudioFrame] = []
                if isinstance(data, rtc.AudioFrame):
                    # ── 输入音量检测 ──────────────────────────
                    raw_input_audio = data.data.tobytes()
                    if raw_input_audio:
                        samples = struct.unpack(
                            "<" + "h" * (len(raw_input_audio) // 2),
                            raw_input_audio,
                        )
                        logger.info(
                            "volcengine.stt.input_audio_level",
                            extra={
                                "stream_id": self.stream_id,
                                "bytes": len(raw_input_audio),
                                "max_amplitude": max(abs(x) for x in samples),
                                "non_zero_samples": sum(
                                    1 for x in samples if x != 0
                                ),
                            },
                        )
                    frames.extend(audio_bstream.write(raw_input_audio))
                elif is_flush:
                    frames.extend(audio_bstream.flush())
                else:
                    logger.warning(
                        "volcengine.stt.input_ignored",
                        extra={
                            "stream_id": self.stream_id,
                            "input_type": (
                                f"{type(data).__module__}."
                                f"{type(data).__name__}"
                            ),
                        },
                    )

                # ── 发送音频帧（对齐官方：flush 后所有帧都标记
                #     last=True / negative-sequence） ────────────
                # 注意：当 is_flush 且 frames 为空时 for 循环自然跳过，
                # 不发送任何帧，行为和官方一致。
                for frame in frames:
                    sequence += 1
                    if is_flush:
                        sequence = -sequence
                    audio = frame.data.tobytes()

                    # ── 输出音量检测 ──────────────────────────
                    if audio:
                        samples = struct.unpack(
                            "<" + "h" * (len(audio) // 2),
                            audio,
                        )
                        logger.info(
                            "volcengine.stt.output_audio_level",
                            extra={
                                "stream_id": self.stream_id,
                                "bytes": len(audio),
                                "max_amplitude": max(abs(x) for x in samples),
                                "non_zero_samples": sum(
                                    1 for x in samples if x != 0
                                ),
                                "sequence": sequence,
                            },
                        )

                    packet = self._opts.get_chunk_request(
                        audio, seq=sequence, last=is_flush
                    )
                    logger.info(
                        "volcengine.stt.chunk_packet",
                        extra={
                            "stream_id": self.stream_id,
                            "sequence": sequence,
                            "is_last": is_flush,
                            "packet_bytes": len(packet),
                            "audio_bytes": len(audio),
                        },
                    )
                    await self._send_packet(
                        ws,
                        packet,
                        sequence=sequence,
                        payload_bytes=len(packet),
                        is_last=is_flush,
                        audio_bytes=len(audio),
                    )

                # ── 每轮音频帧处理完后检查 ASR 会话健康 ────────
                self._maybe_auto_reconnect()

            if failure:
                raise failure
        except asyncio.CancelledError:
            reason = "cancelled"
            raise

        except BaseException as exc:
            reason = "exception"
            failure = exc

            logger.exception(
                "volcengine.stt.send_loop_failed",
                extra={
                    "stream_id": self.stream_id,
                    "exception": str(exc),
                },
            )

            raise

        finally:
            logger.info(
                "volcengine.stt.send_loop_stopped",
                extra={
                    "stream_id": self.stream_id,
                    "reason": reason,
                },
            )

    async def _receive_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """对齐官方 ``recv_task``：非预期关闭时抛出异常触发重连。"""
        reason = "websocket_closed"
        failure: BaseException | None = None
        logger.info(
            "volcengine.stt.receive_loop_started",
            extra={"stream_id": self.stream_id},
        )
        try:
            while True:
                msg = await ws.receive()
                logger.info(
                    "volcengine.stt.response_received",
                    extra={
                        "stream_id": self.stream_id,
                        "msg_type": str(msg.type),
                        "payload_type": type(msg.data).__name__,
                        "payload_bytes": (
                            len(msg.data)
                            if isinstance(msg.data, bytes)
                            else None
                        ),
                    },
                )
                if msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                ):
                    # 对齐官方：主动关闭（aclose）时正常返回；非预期关闭触发重连
                    if self._closing_ws:
                        return
                    raise APIStatusError(
                        message="connection closed unexpectedly"
                    )
                self.total_responses_received += 1
                self._process_stream_event(msg.data)
        except asyncio.CancelledError:
            reason = "cancelled"
            raise
        except BaseException as exc:
            reason = "exception"
            failure = exc
            logger.exception(
                "volcengine.stt.receive_loop_failed",
                extra={
                    "stream_id": self.stream_id,
                    "exception": _exception_text(exc),
                },
            )
            raise
        finally:
            logger.info(
                "volcengine.stt.receive_loop_stopped",
                extra={
                    "stream_id": self.stream_id,
                    "reason": reason,
                    "exception": _exception_text(failure),
                },
            )

    def _process_stream_event(self, data: Any) -> None:
        """审计火山 STT 返回事件。

        仅增加诊断日志，SpeechEvent 的生成完全委托给官方
        :meth:`bigmodel_stt.SpeechStream._process_stream_event`。
        """

        parsed = bigmodel_stt.parse_response(data)

        # ── 完整原始响应（方便确认火山实际返回格式） ──────────────
        logger.info(
            "volcengine.stt.debug_response",
            extra={
                "stream_id": self.stream_id,
                "parsed_keys": list(parsed.keys()),
                "parsed": parsed,
            },
        )

        # ── 错误响应处理（官方没有的防御逻辑） ──────────────────
        code = parsed.get("code")
        if code is not None:
            fingerprint = hashlib.sha256(data).hexdigest()
            if fingerprint in self._seen_error_responses:
                return
            self._seen_error_responses.add(fingerprint)
            logger.error(
                "volcengine.stt.error_response",
                extra={
                    "stream_id": self.stream_id,
                    "code": code,
                    "is_last": parsed.get("is_last_package", False),
                    "payload_msg": parsed.get("payload_msg"),
                },
            )
            return

        # ── 结果诊断日志（在官方处理之前记录） ──────────────────
        payload = parsed.get("payload_msg")
        if isinstance(payload, dict):
            result = payload.get("result")
            if isinstance(result, dict):
                text = result.get("text", "")
                utterances = result.get("utterances", [])
                definite = (
                    utterances[0].get("definite", False)
                    if isinstance(utterances, list) and utterances
                    else False
                )
                logger.info(
                    "volcengine.stt.result_debug",
                    extra={
                        "stream_id": self.stream_id,
                        "text": text,
                        "utterances_count": (
                            len(utterances)
                            if isinstance(utterances, list)
                            else None
                        ),
                        "definite": definite,
                        "speaking_before": self._speaking,
                        "result_keys": list(result.keys()),
                        "full_result": result,
                    },
                )

        # ── 委托官方生成 SpeechEvent（官方内部有自己的过滤逻辑） ──
        speaking_before = self._speaking
        super()._process_stream_event(data)
        speaking_after = self._speaking

        # ── 记录最近一次有效识别时间（自愈机制） ────────────────
        if isinstance(payload, dict):
            result = payload.get("result")
            if isinstance(result, dict):
                text = result.get("text", "")
                if text:
                    self._last_recognition_at = time.monotonic()

        logger.info(
            "volcengine.stt.response_processed",
            extra={
                "stream_id": self.stream_id,
                "speaking_before": speaking_before,
                "speaking_after": speaking_after,
                "speaking_changed": speaking_before != speaking_after,
            },
        )

    async def _run(self) -> None:
        """对齐官方 reconnect loop：连接意外断开时自动重连。

        关键改动：在 while 循环内部静默捕获 WebSocket 关闭异常，
        阻止其传播到 :meth:`_main_task`。否则 ``_main_task`` 会
        调用 ``_emit_error`` → ``AgentSession.on("error")`` →
        音频管线被终止 → STT 重连后无新音频输入 → 识别永久失效。
        """
        ws: aiohttp.ClientWebSocketResponse | None = None

        while True:
            try:
                # 每次重连生成新的 request_id，确保火山引擎创建全新 ASR 会话。
                # 旧的 uid 可能已在服务端被标记为 session ended（45000081），
                # 复用旧 uid 会导致新 WebSocket 也无法创建有效会话。
                self._request_id = utils.shortuuid()
                ws = await self._connect_ws()

                # 新 WebSocket 连接 = 新 ASR 会话，重置空闲计时器
                self._last_recognition_at = 0.0

                self._send_task = asyncio.create_task(
                    self._send_loop(ws),
                    name=f"volcengine-stt-send-{self.stream_id}",
                )
                self._receive_task = asyncio.create_task(
                    self._receive_loop(ws),
                    name=f"volcengine-stt-receive-{self.stream_id}",
                )
                wait_reconnect_task = asyncio.create_task(
                    self._reconnect_event.wait(),
                )

                try:
                    done, _ = await asyncio.wait(
                        [
                            asyncio.gather(self._send_task, self._receive_task),
                            wait_reconnect_task,
                        ],
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    # 传播已完成任务的异常
                    for task in done:
                        if task != wait_reconnect_task:
                            task.result()

                    # 非重连触发 → 退出 loop
                    if wait_reconnect_task not in done:
                        break

                    self._reconnect_event.clear()
                except APIStatusError:
                    # WebSocket 被服务器关闭 → 静默重连
                    # 不把异常传播给 _main_task，避免触发
                    # AgentSession.on("error") 终止整个音频管线
                    logger.warning(
                        "volcengine.stt.reconnecting_silently",
                        extra={
                            "stream_id": self.stream_id,
                            "num_retries": self._num_retries,
                        },
                    )
                    continue
                finally:
                    tasks = [
                        self._send_task,
                        self._receive_task,
                        wait_reconnect_task,
                    ]
                    await utils.aio.gracefully_cancel(*tasks)

            finally:
                if ws is not None:
                    await ws.close()

    async def aclose(self) -> None:
        self._closing_ws = True  # 阻止 _receive_loop 在正常关闭时触发重连
        try:
            await super().aclose()
        finally:
            if not self._closed_logged:
                self._closed_logged = True
                logger.info(
                    "volcengine.stt.stream_closed",
                    extra={
                        "stream_id": self.stream_id,
                        "total_frames_received": self.total_frames_received,
                        "total_frames_sent": self.total_frames_sent,
                        "total_audio_bytes_sent": self.total_audio_bytes_sent,
                        "total_responses_received": self.total_responses_received,
                    },
                )


class AuditedBigModelSTT(bigmodel_stt.BigModelSTT):
    """BigModel STT factory that creates :class:`AuditedSpeechStream`."""

    def __init__(
        self,
        *,
        hot_words_json: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._hot_words_json = hot_words_json

    def stream(self, *, language: Any = None, conn_options: Any = None) -> AuditedSpeechStream:
        kwargs: dict[str, Any] = {
            "stt": self,
            "opts": self._opts,
            "http_session": self._ensure_session(),
            "hot_words_json": self._hot_words_json,
        }
        if conn_options is None:
            from livekit.agents import DEFAULT_API_CONNECT_OPTIONS

            conn_options = DEFAULT_API_CONNECT_OPTIONS
        kwargs["conn_options"] = conn_options
        stream = AuditedSpeechStream(**kwargs)
        self._streams.add(stream)
        return stream


__all__ = ["AuditedBigModelSTT", "AuditedSpeechStream"]
