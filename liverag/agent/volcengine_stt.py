"""Auditable Volcengine BigModel streaming STT.

This module deliberately reuses the upstream plugin's protocol builders and
response conversion.  It only owns observability and task lifecycle so the
audio path can be proven without logging PCM data.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

import aiohttp
from livekit import rtc
from livekit.agents import APIStatusError, utils
from livekit.plugins.volcengine import bigmodel_stt

logger = logging.getLogger(__name__)


def _exception_text(exc: BaseException | None) -> str:
    return "" if exc is None else f"{type(exc).__name__}: {exc}"


class AuditedSpeechStream(bigmodel_stt.SpeechStream):
    """Upstream speech stream with per-hop structured diagnostics."""

    def __init__(self, **kwargs: Any) -> None:
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
        logger.info("volcengine.stt.stream_created", extra={"stream_id": self.stream_id})

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
        await ws.send_bytes(packet)
        if audio_bytes:
            self.total_frames_sent += 1
            self.total_audio_bytes_sent += audio_bytes
        logger.info(
            "volcengine.stt.packet_sent",
            extra={"stream_id": self.stream_id, "sequence": sequence},
        )

    async def _send_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        reason = "input_channel_closed"
        failure: BaseException | None = None
        logger.info("volcengine.stt.send_loop_started", extra={"stream_id": self.stream_id})
        try:
            initial = self._opts.get_ws_query_params(uid=self._request_id)
            await self._send_packet(
                ws, initial, sequence=1, payload_bytes=len(initial), is_last=False
            )

            audio_bstream = utils.audio.AudioByteStream(
                sample_rate=self._opts.sample_rate,
                num_channels=self._opts.num_channels,
                samples_per_channel=self._opts.sample_rate // 10,
            )
            sequence = 1
            async for data in self._input_ch:
                is_flush = isinstance(data, self._FlushSentinel)
                frames = (
                    audio_bstream.flush()
                    if is_flush
                    else audio_bstream.write(data.data.tobytes())
                    if isinstance(data, rtc.AudioFrame)
                    else []
                )
                if not is_flush and not isinstance(data, rtc.AudioFrame):
                    logger.warning(
                        "volcengine.stt.input_ignored",
                        extra={
                            "stream_id": self.stream_id,
                            "input_type": f"{type(data).__module__}.{type(data).__name__}",
                        },
                    )
                if is_flush and not frames:
                    sequence += 1
                    packet = self._opts.get_chunk_request(b"", seq=-sequence, last=True)
                    await self._send_packet(
                        ws,
                        packet,
                        sequence=-sequence,
                        payload_bytes=len(packet),
                        is_last=True,
                    )
                for index, frame in enumerate(frames):
                    sequence += 1
                    is_last = is_flush and index == len(frames) - 1
                    wire_sequence = -sequence if is_last else sequence
                    audio = frame.data.tobytes()
                    packet = self._opts.get_chunk_request(audio, seq=wire_sequence, last=is_last)
                    await self._send_packet(
                        ws,
                        packet,
                        sequence=wire_sequence,
                        payload_bytes=len(packet),
                        is_last=is_last,
                        audio_bytes=len(audio),
                    )
        except asyncio.CancelledError:
            reason = "cancelled"
            raise
        except BaseException as exc:
            reason = "exception"
            failure = exc
            logger.exception(
                "volcengine.stt.send_loop_failed",
                extra={"stream_id": self.stream_id, "exception": _exception_text(exc)},
            )
            raise
        finally:
            logger.info(
                "volcengine.stt.send_loop_stopped",
                extra={
                    "stream_id": self.stream_id,
                    "reason": reason,
                    "exception": _exception_text(failure),
                },
            )

    async def _receive_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        reason = "websocket_closed"
        failure: BaseException | None = None
        logger.info("volcengine.stt.receive_loop_started", extra={"stream_id": self.stream_id})
        try:
            while True:
                msg = await ws.receive()
                if msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                ):
                    return
                self.total_responses_received += 1
                self._process_stream_event(msg.data)
        except asyncio.CancelledError:
            reason = "cancelled"
            raise
        except BaseException as exc:
            reason = "exception"
            failure = exc
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

    def _process_stream_event(self, data: bytes) -> None:
        parsed = bigmodel_stt.parse_response(data)
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
                    "response": parsed.get("payload_msg"),
                },
            )
            return
        super()._process_stream_event(data)

    async def _run(self) -> None:
        ws: aiohttp.ClientWebSocketResponse | None = None
        try:
            ws = await self._connect_ws()
            self._send_task = asyncio.create_task(
                self._send_loop(ws), name=f"volcengine-stt-send-{self.stream_id}"
            )
            self._receive_task = asyncio.create_task(
                self._receive_loop(ws), name=f"volcengine-stt-receive-{self.stream_id}"
            )
            done, pending = await asyncio.wait(
                (self._send_task, self._receive_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                task.result()
            if self._receive_task in done and self._send_task in pending:
                raise APIStatusError(message="connection closed unexpectedly")
            await asyncio.gather(*pending)
        finally:
            tasks = [task for task in (self._send_task, self._receive_task) if task]
            await utils.aio.gracefully_cancel(*tasks)
            if ws is not None:
                await ws.close()

    async def aclose(self) -> None:
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

    def stream(self, *, language: Any = None, conn_options: Any = None) -> AuditedSpeechStream:
        kwargs: dict[str, Any] = {
            "stt": self,
            "opts": self._opts,
            "http_session": self._ensure_session(),
        }
        if conn_options is None:
            from livekit.agents import DEFAULT_API_CONNECT_OPTIONS

            conn_options = DEFAULT_API_CONNECT_OPTIONS
        kwargs["conn_options"] = conn_options
        stream = AuditedSpeechStream(**kwargs)
        self._streams.add(stream)
        return stream


__all__ = ["AuditedBigModelSTT", "AuditedSpeechStream"]
