"""STT 热词管理模块。

从 HOT_WORDS.md 加载固定技术词表，生成火山引擎 BigModel ASR所要求的 ``corpus.context`` 热词格式。
火山引擎 BigModel ASR 的热词格式（双向流式模式）:
    Full Client Request JSON 中增加 ``corpus`` 顶层字段::
        {
            "user": {...},
            "audio": {...},
            "request": {...},
            "corpus": {
                "context": "{\\"hotwords\\":[{\\"word\\":\\"Agent\\",\\"level\\":10}]}"
            }
        }
限制：
- 双向流式模式最多 100 个热词 token
- 每个词支持 level 1-10，language 可选 ``zh`` / ``en`` / ``zh_en``
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# 热词文件路径
_HOT_WORDS_MD = Path(__file__).parents[2] / "docs" / "HOT_WORDS.md"

# 火山 ASR 双向流式模式热词上限
_MAX_HOT_WORDS = 100


def load_hot_words(path: Path | None = None) -> str:
    """从 HOT_WORDS.md 加载热词，返回火山引擎 ASR context 格式的 JSON 字符串。
    返回空字符串表示没有可用热词。
    """

    #热词文档路径
    path = path or _HOT_WORDS_MD
    if not path.exists():
        return ""

    #解析出的 "词汇|权重" 格式
    entries = _parse_hot_words_md(path.read_text(encoding="utf-8"))
    if not entries:
        return ""

    # 火山 ASR 双向流式模式最多 100 个热词
    if len(entries) > _MAX_HOT_WORDS:
        entries = entries[:_MAX_HOT_WORDS]

    hotwords: list[dict[str, Any]] = []
    for word, level in entries:
        hotwords.append({"word": word, "level": level})

    return json.dumps({"hotwords": hotwords}, ensure_ascii=False)


def _parse_hot_words_md(content: str) -> list[tuple[str, int]]:
    """解析 HOT_WORDS.md 中的 ``词汇|权重`` 行。
    Returns:[(word, level), ...] 按原文顺序保留，权重越高越靠前。"""

    entries: list[tuple[str, int]] = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(.+)\|(\d+)$", line)
        if not m:
            continue
        word, level = m.group(1).strip(), int(m.group(2))
        if word and 1 <= level <= 10:
            entries.append((word, level))
    return entries


def build_corpus_context(hot_words_json: str) -> dict[str, dict[str,str]]:
    """构建火山 ASR Full Client Request 的 ``corpus`` 字段。
    返回空 dict 表示无需注入热词。"""

    if not hot_words_json.strip():
        return {}
    return {"corpus": {"context": hot_words_json}}


def inject_hot_words_into_initial_request(
    initial: bytes | bytearray,
    hot_words_json: str,
) -> bytes:
    """将热词注入到火山 ASR Full Client Request 的二进制包中。
    解析首帧二进制协议包，在 JSON 负载中添加 ``corpus.context`` 后重新打包。

    Args:
        initial: ``BigModelSTTOptions.get_ws_query_params()`` 返回的首帧包。
        hot_words_json: ``load_hot_words()`` 的返回值。

    Returns:
        注入热词后的二进制包。如果 hot_words_json 为空，原样返回 initial。
    """

    if not hot_words_json:
        return bytes(initial)

    import gzip

    header_size = initial[0] & 0x0F
    # header_size * 4 → 跳过 header 部分
    offset = header_size * 4

    # sequence（4 bytes，有符号大端）
    sequence = int.from_bytes(initial[offset : offset + 4], "big", signed=True)
    offset += 4

    # payload_size（4 bytes）
    payload_size = int.from_bytes(initial[offset : offset + 4], "big", signed=False)
    offset += 4

    # gzip(JSON)
    compressed = initial[offset : offset + payload_size]
    json_bytes = gzip.decompress(bytes(compressed))
    payload = json.loads(json_bytes.decode("utf-8"))

    # 注入热词
    payload["corpus"] = {"context": hot_words_json}

    # 重新序列化并压缩
    new_json = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    new_compressed = gzip.compress(new_json)

    # 重建包
    result = bytearray(initial[: header_size * 4])
    result.extend(sequence.to_bytes(4, "big", signed=True))
    result.extend(len(new_compressed).to_bytes(4, "big"))
    result.extend(new_compressed)

    return bytes(result)


__all__ = [
    "build_corpus_context",
    "inject_hot_words_into_initial_request",
    "load_hot_words",
]
