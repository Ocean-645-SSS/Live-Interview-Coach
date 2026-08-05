"""Focused regression tests for the audited Volcengine streaming path."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import aiohttp
import pytest
from livekit import rtc
from livekit.agents import APIConnectOptions
from livekit.plugins.volcengine import bigmodel_stt

from liverag.agent.volcengine_stt import AuditedBigModelSTT


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.closed = False
        self._receive_gate = asyncio.Event()

    async def send_bytes(self, packet: bytes | bytearray) -> None:
        self.sent.append(bytes(packet))

    async def receive(self) -> SimpleNamespace:
        await self._receive_gate.wait()
        return SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data=b"")

    async def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, ws: FakeWebSocket) -> None:
        self.ws = ws

    async def ws_connect(self, *args: object, **kwargs: object) -> FakeWebSocket:
        return self.ws


async def _wait_for_packets(ws: FakeWebSocket, count: int) -> None:
    for _ in range(100):
        if len(ws.sent) >= count:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"expected {count} packets, got {len(ws.sent)}")


@pytest.mark.asyncio
async def test_pushed_audio_reaches_websocket_after_initial_packet() -> None:
    ws = FakeWebSocket()
    provider = AuditedBigModelSTT(
        app_id="app-id", access_token="token", http_session=FakeSession(ws)
    )
    stream = provider.stream(conn_options=APIConnectOptions(max_retry=0))
    frame = rtc.AudioFrame.create(sample_rate=16000, num_channels=1, samples_per_channel=1600)

    stream.push_frame(frame)
    await _wait_for_packets(ws, 2)

    assert ws.sent[0][1] >> 4 == bigmodel_stt.FULL_CLIENT_REQUEST
    assert ws.sent[1][1] >> 4 == bigmodel_stt.AUDIO_ONLY_REQUEST
    assert stream.total_frames_received == 1
    assert stream.total_frames_sent == 1
    assert stream.total_audio_bytes_sent == 3200
    await stream.aclose()


@pytest.mark.asyncio
async def test_identical_error_response_is_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    ws = FakeWebSocket()
    provider = AuditedBigModelSTT(
        app_id="app-id", access_token="token", http_session=FakeSession(ws)
    )
    stream = provider.stream(conn_options=APIConnectOptions(max_retry=0))
    message = b"Timeout waiting next packet"
    packet = bytearray(
        bigmodel_stt.generate_header(
            message_type=bigmodel_stt.SERVER_ERROR_RESPONSE,
            serial_method=bigmodel_stt.NO_SERIALIZATION,
            compression_type=bigmodel_stt.NO_COMPRESSION,
        )
    )
    packet.extend((45000081).to_bytes(4, "big"))
    packet.extend(len(message).to_bytes(4, "big"))
    packet.extend(message)

    with caplog.at_level(logging.ERROR, logger="liverag.agent.volcengine_stt"):
        stream._process_stream_event(bytes(packet))
        stream._process_stream_event(bytes(packet))

    records = [record for record in caplog.records if record.msg == "volcengine.stt.error_response"]
    assert len(records) == 1
    assert records[0].code == 45000081
    await stream.aclose()


@pytest.mark.asyncio
async def test_flush_sends_explicit_last_packet_when_audio_buffer_is_empty() -> None:
    ws = FakeWebSocket()
    provider = AuditedBigModelSTT(
        app_id="app-id", access_token="token", http_session=FakeSession(ws)
    )
    stream = provider.stream(conn_options=APIConnectOptions(max_retry=0))

    stream.flush()
    await _wait_for_packets(ws, 2)

    assert ws.sent[1][1] & 0x0F == bigmodel_stt.NEG_WITH_SEQUENCE
    assert int.from_bytes(ws.sent[1][4:8], "big", signed=True) < 0
    await stream.aclose()
