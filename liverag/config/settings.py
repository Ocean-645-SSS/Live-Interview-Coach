"""统一读取 LiveRAG Agent 配置文件。"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

RagToolMode = Literal["auto", "never"]

def load_environment() -> None:
    """按本地优先级加载环境变量。"""

    load_dotenv(".env.local", override=True)
    load_dotenv()
    
def _str_env(name: str, default: str = "") -> str:
    """读取字符串环境变量。"""

    return os.getenv(name, default).strip()


def _int_env(name: str, default: int) -> int:
    """读取整数环境变量。"""

    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _float_env(name: str, default: float) -> float:
    """读取浮点数环境变量。"""

    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


def _bool_env(name: str, default: bool) -> bool:
    """读取布尔环境变量。"""

    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _rag_tool_mode_env() -> RagToolMode:
    """读取 RAG 工具调用模式。"""

    value = _str_env("LIGHTRAG_TOOL_MODE", "auto")
    return cast(RagToolMode, value if value in {"auto", "never"} else "auto")


_RAG_RUNTIME_FIELDS = {
    "enabled",
    "base_url",
    "api_key",
    "query_mode",
    "timeout_ms",
    "top_k",
    "chunk_top_k",
    "context_max_chars",
    "cache_ttl_s",
    "enable_rerank",
    "rag_tool_mode",
}

_MODEL_RUNTIME_FIELDS = {
    "voice": {
        "stt": {"provider", "model", "app_id", "access_token"},
        "llm": {"model", "base_url", "api_key"},
        "tts": {"provider", "model", "voice", "api_key"},
    }
}

_STT_PROVIDER_OPTIONS = [
    {
        "provider": "volcengine_bigmodel",
        "label": "火山引擎 BigModel STT",
        "description": "当前 LiveRAG 已适配的实时语音识别 provider。",
        "models": [{"id": "bigmodel", "label": "bigmodel", "verified": True}],
        "default_model": "bigmodel",
        "config_fields": [
            {"key": "app_id", "label": "App ID", "type": "secret", "required": True},
            {"key": "access_token", "label": "Access Token", "type": "secret", "required": True},
        ],
    }
]

_CONTEXT_MODEL_RUNTIME_FIELDS = {
    "model",
    "base_url",
    "api_key",
    "temperature",
    "max_tokens",
    "max_session_chars",
    "history_reference_limit",
    "timeout_ms",
}

_MASKED_SECRET_MARKER = "*****"

_DASHSCOPE_VOICE_METADATA = {
    "Cherry": {
        "name": "芊悦",
        "description": "阳光积极、亲切自然小姐姐（女性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Serena": {
        "name": "苏瑶",
        "description": "温柔小姐姐（女性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Ethan": {
        "name": "晨煦",
        "description": "标准普通话，带部分北方口音。阳光、温暖、活力、朝气（男性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Chelsie": {
        "name": "千雪",
        "description": "二次元虚拟女友（女性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Momo": {
        "name": "茉兔",
        "description": "撒娇搞怪，逗你开心（女性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Vivian": {
        "name": "十三",
        "description": "拽拽的、可爱的小暴躁（女性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Moon": {
        "name": "月白",
        "description": "率性帅气的月白（男性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Maia": {
        "name": "四月",
        "description": "知性与温柔的碰撞（女性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Kai": {
        "name": "凯",
        "description": "耳朵的一场 SPA（男性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Nofish": {
        "name": "不吃鱼",
        "description": "不会翘舌音的设计师（男性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Bella": {
        "name": "萌宝",
        "description": "喝酒不打醉拳的小萝莉（女性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Jennifer": {
        "name": "詹妮弗",
        "description": "品牌级、电影质感般美语女声（女性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Ryan": {
        "name": "甜茶",
        "description": "节奏拉满，戏感炸裂，真实与张力共舞（男性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Katerina": {
        "name": "卡捷琳娜",
        "description": "御姐音色，韵律回味十足（女性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Aiden": {
        "name": "艾登",
        "description": "精通厨艺的美语大男孩（男性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Eldric Sage": {
        "name": "沧明子",
        "description": "沉稳睿智的老者，沧桑如松却心明如镜（男性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Mia": {
        "name": "乖小妹",
        "description": "温顺如春水，乖巧如初雪（女性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Mochi": {
        "name": "沙小弥",
        "description": "聪明伶俐的小大人，童真未泯却早慧如禅（男性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Bellona": {
        "name": "燕铮莺",
        "description": "声音洪亮，吐字清晰，人物鲜活，听得人热血沸腾（女性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Vincent": {
        "name": "田叔",
        "description": "一口独特的沙哑烟嗓，道尽千军万马与江湖豪情（男性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Bunny": {
        "name": "萌小姬",
        "description": "“萌属性”爆棚的小萝莉（女性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Neil": {
        "name": "阿闻",
        "description": "平直语调、字正腔圆，专业新闻主持人风格（男性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Elias": {
        "name": "墨讲师",
        "description": "保持学科严谨性，适合把复杂知识讲清楚（女性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Arthur": {
        "name": "徐大爷",
        "description": "质朴沧桑、不疾不徐的乡土叙事男声（男性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Nini": {
        "name": "邻家妹妹",
        "description": "软糯甜美的邻家妹妹声线（女性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Seren": {
        "name": "小婉",
        "description": "温和舒缓的助眠声线（女性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Pip": {
        "name": "顽屁小孩",
        "description": "调皮捣蛋、充满童真的男孩声（男性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Stella": {
        "name": "少女阿月",
        "description": "甜美迷糊的少女音，也能表达正义感和张力（女性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Bodega": {
        "name": "博德加",
        "description": "热情的西班牙大叔（男性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Sonrisa": {
        "name": "索尼莎",
        "description": "热情开朗的拉美大姐（女性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Alek": {
        "name": "阿列克",
        "description": "战斗民族的冷与毛呢大衣下的暖（男性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Dolce": {
        "name": "多尔切",
        "description": "慵懒的意大利大叔（男性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Sohee": {
        "name": "素熙",
        "description": "温柔开朗，情绪丰富的韩国欧尼（女性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Ono Anna": {
        "name": "小野杏",
        "description": "鬼灵精怪的青梅竹马（女性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Lenn": {
        "name": "莱恩",
        "description": "理性底色里带一点叛逆的德国青年（男性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Emilien": {
        "name": "埃米尔安",
        "description": "浪漫的法国大哥哥（男性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Andre": {
        "name": "安德雷",
        "description": "磁性、自然舒服、沉稳的男声（男性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Radio Gol": {
        "name": "拉迪奥·戈尔",
        "description": "足球诗人和足球解说风格（男性）",
        "language": "中文（普通话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Jada": {
        "name": "上海-阿珍",
        "description": "风风火火的沪上阿姐（女性）",
        "language": "中文（上海话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Dylan": {
        "name": "北京-晓东",
        "description": "北京胡同里长大的少年（男性）",
        "language": "中文（北京话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Li": {
        "name": "南京-老李",
        "description": "耐心的瑜伽老师（男性）",
        "language": "中文（南京话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Marcus": {
        "name": "陕西-秦川",
        "description": "面宽话短、心实声沉的老陕味道（男性）",
        "language": "中文（陕西话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Roy": {
        "name": "闽南-阿杰",
        "description": "诙谐直爽、市井活泼的台湾哥仔形象（男性）",
        "language": "中文（闽南语）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Peter": {
        "name": "天津-李彼得",
        "description": "天津相声，专业捧哏（男性）",
        "language": "中文（天津话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Sunny": {
        "name": "四川-晴儿",
        "description": "甜到心里的川妹子（女性）",
        "language": "中文（四川话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Eric": {
        "name": "四川-程川",
        "description": "跳脱市井的四川成都男子（男性）",
        "language": "中文（四川话）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Rocky": {
        "name": "粤语-阿强",
        "description": "幽默风趣的阿强，在线陪聊（男性）",
        "language": "中文（粤语）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
    "Kiki": {
        "name": "粤语-阿清",
        "description": "甜美的港妹闺蜜（女性）",
        "language": "中文（粤语）、英语、法语、德语、俄语、意大利语、西班牙语、葡萄牙语、日语、韩语",
        "description_source": "official",
    },
}

def _verified_voice_options(
    *voice_ids: str,
    metadata: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """生成已经通过真实合成验证的 voice 选项。"""

    metadata = metadata or {}
    options: list[dict[str, Any]] = []
    for voice_id in voice_ids:
        meta = metadata.get(voice_id, {})
        name = meta.get("name", "").strip()
        label = meta.get("label", "").strip()
        if not label:
            label = f"{name}（{voice_id}）" if name else voice_id
        option: dict[str, Any] = {"id": voice_id, "label": label, "verified": True}
        for key in ("name", "description", "language", "description_source"):
            value = meta.get(key, "").strip()
            if value:
                option[key] = value
        options.append(option)
    return options

_TTS_PROVIDER_OPTIONS=[
    {
    "provider": "dashscope_realtime",
            "label": "阿里 DashScope Qwen Realtime TTS",
            "description": "已适配 qwen3-tts 实时 WebSocket 链路，固定使用后端内置 endpoint。",
            "models": [
                {"id": "qwen3-tts-flash-realtime", "label": "qwen3-tts-flash-realtime", "verified": True},
                {
                    "id": "qwen3-tts-instruct-flash-realtime",
                    "label": "qwen3-tts-instruct-flash-realtime",
                    "verified": True,
                },
                {"id": "qwen-tts-realtime", "label": "qwen-tts-realtime", "verified": True},
            ],
            "voices": _verified_voice_options(
                "Cherry",
                "Serena",
                "Ethan",
                "Chelsie",
                "Momo",
                "Vivian",
                "Moon",
                "Maia",
                "Kai",
                "Nofish",
                "Bella",
                "Jennifer",
                "Ryan",
                "Katerina",
                "Aiden",
                "Eldric Sage",
                "Mia",
                "Mochi",
                "Bellona",
                "Vincent",
                "Bunny",
                "Neil",
                "Elias",
                "Arthur",
                "Nini",
                "Seren",
                "Pip",
                "Stella",
                "Bodega",
                "Sonrisa",
                "Alek",
                "Dolce",
                "Sohee",
                "Ono Anna",
                "Lenn",
                "Emilien",
                "Andre",
                "Radio Gol",
                "Jada",
                "Dylan",
                "Li",
                "Marcus",
                "Roy",
                "Peter",
                "Sunny",
                "Eric",
                "Rocky",
                "Kiki",
                metadata=_DASHSCOPE_VOICE_METADATA,
            ),
            "default_model": "qwen3-tts-flash-realtime",
            "default_voice": "Cherry",
            "config_fields": [
                {
                    "key": "api_key",
                    "label": "DashScope API Key",
                    "type": "secret",
                    "required": False,
                    "description": "留空时后端默认复用 DASHSCOPE_API_KEY。",
                }
            ],
    }
]

def _runtime_rag_config_path(user_data_dir: Path | None = None) -> Path:
    """返回运行时 RAG 配置文件路径。"""

    root = user_data_dir or Path(_str_env("LIVERAG_USER_DATA_DIR", "~/.LiveRAG")).expanduser()
    return root / "rag" / "config.json"


def runtime_model_config_path(user_data_dir: Path | None = None) -> Path:
    """返回运行时语音模型配置文件路径。"""

    root = user_data_dir or Path(_str_env("LIVERAG_USER_DATA_DIR", "~/.LiveRAG")).expanduser()
    return root / "model" / "config.json"


def runtime_context_model_config_path(user_data_dir: Path | None = None) -> Path:
    """返回运行时上下文模型配置文件路径。"""

    root = user_data_dir or Path(_str_env("LIVERAG_USER_DATA_DIR", "~/.LiveRAG")).expanduser()
    return root / "model" / "context_config.json"


def _read_runtime_rag_overrides(user_data_dir: Path | None = None) -> dict[str, Any]:
    """读取前端 API 写入的 RAG 配置覆盖项。"""

    path = _runtime_rag_config_path(user_data_dir)
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if raw.endswith("\\n"):
            raw = raw[:-2].rstrip()
        payload = json.loads(raw)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    overrides = {key: value for key, value in payload.items() if key in _RAG_RUNTIME_FIELDS}
    if overrides.get("rag_tool_mode") not in {None, "auto", "never"}:
        overrides["rag_tool_mode"] = "auto"
    return overrides


def read_runtime_model_config(user_data_dir: Path | None = None) -> dict[str, Any]:
    """读取前端 API 写入的语音模型配置覆盖项。"""

    path = runtime_model_config_path(user_data_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return _filter_runtime_model_config(payload)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        frozen=True,
        extra="ignore",
        env_prefix="LIVERAG_",
    )

    user_data_dir: Path = Path("~/.LiveRAG")
    rag_port: int = Field(default=9819, ge=1, le=49822)

    rag_llm_model: str = Field(min_length=1, description="RAG LLM模型名称")
    rag_llm_api_key: str = Field(min_length=1, description="RAG LLM模型API Key")
    rag_llm_base_url: str = Field(min_length=1, description="RAG LLM模型基础URL")

    rag_embedding_model: str = Field(min_length=1)
    rag_embedding_base_url: str = Field(min_length=1)
    rag_embedding_api_key: str = Field(min_length=1)


@dataclass(frozen=True)
class RagClientSettings:
    """语音链路访问 RAG 服务的配置。"""

    enabled: bool = _bool_env("LIGHTRAG_ENABLED", True)
    base_url: str = _str_env("LIGHTRAG_BASE_URL", "http://127.0.0.1:9721").rstrip("/")
    api_key: str = _str_env("LIGHTRAG_API_KEY", _str_env("KB_SERVICE_API_KEY", ""))
    query_mode: str = _str_env("LIGHTRAG_QUERY_MODE", _str_env("LIGHTRAG_VOICE_MODE", "naive"))
    timeout_ms: int = _int_env("LIGHTRAG_TIMEOUT_MS", 900)
    top_k: int = _int_env("LIGHTRAG_TOP_K", _int_env("LIGHTRAG_VOICE_TOP_K", 4))
    chunk_top_k: int = _int_env("LIGHTRAG_CHUNK_TOP_K", _int_env("LIGHTRAG_VOICE_CHUNK_TOP_K", 4))
    context_max_chars: int = _int_env(
        "LIGHTRAG_CONTEXT_MAX_CHARS",
        _int_env("LIGHTRAG_VOICE_CONTEXT_MAX_CHARS", 1800),
    )
    cache_ttl_s: float = _float_env("LIGHTRAG_CACHE_TTL_S", 45.0)
    enable_rerank: bool = _bool_env("LIGHTRAG_VOICE_ENABLE_RERANK", False)
    rag_tool_mode: RagToolMode = field(default_factory=_rag_tool_mode_env)

    def __post_init__(self) -> None:
        """校验 RAG 工具调用模式。"""

        if self.rag_tool_mode not in {"auto", "never"}:
            raise ValueError("rag_tool_mode must be one of: auto, never")


@dataclass(frozen=True)
class ApiSettings:
    """前端管理 API 的内部运行配置。"""

    rag_gateway_timeout_ms: int = 10000
    rag_gateway_upload_timeout_ms: int = 30000
    rag_ready_timeout_ms: int = 15000


@dataclass(frozen=True)
class VoiceSettings:
    """实时语音模型配置"""

    livekit_url: str = ""  # livekit的websocket地址

    stt_provider: str = "volcengine_bigmodel"  # 语音识别服务商
    stt_app_id: str = ""  # 火山引擎应用ID
    stt_access_token: str = ""
    stt_model: str = "bigmodel"

    llm_model: str = "qwen-flash"
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_api_key: str = ""

    tts_provider: str = "dashscope_realtime"
    tts_model: str = "qwen3-tts-flash-realtime"
    tts_voice: str = "Cherry"
    tts_api_key: str = ""
    tts_base_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"


@dataclass(frozen=True)
class ContextModelSettings:
    """通话历史压缩和知识库概览生成使用的模型配置。"""

    model: str = "qwen-max"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: str = ""
    max_tokens: int = 2000
    max_session_chars: int = 16000
    history_reference_limit: int = 8
    timeout_ms: int = 15000
    temperature: float = 0.0


def load_voice_settings(user_data_dir: Path | None = None) -> VoiceSettings:
    """按环境默认值和运行时配置覆盖项生成语音模型配置。"""

    base = _env_voice_settings()
    config = read_runtime_model_config(user_data_dir)
    configured_provider = _override_str(config, "tts", "provider", base.tts_provider)
    provider = _canonical_tts_provider(configured_provider)

    if provider != "dashscope_realtime":
        raise ValueError(f"当前只支持 DashScope TTS，实际配置为：{configured_provider}")

    return VoiceSettings(
        livekit_url=base.livekit_url,
        stt_provider=_override_str(config, "stt", "provider", base.stt_provider).lower(),
        stt_app_id=_override_str(
            config,
            "stt",
            "app_id",
            base.stt_app_id,
        ),
        stt_access_token=_override_str(
            config,
            "stt",
            "access_token",
            base.stt_access_token,
        ),
        stt_model=_override_str(
            config,
            "stt",
            "model",
            base.stt_model,
        ),
        llm_model=_override_str(
            config,
            "llm",
            "model",
            base.llm_model,
        ),
        llm_base_url=_override_str(
            config,
            "llm",
            "base_url",
            base.llm_base_url,
        ).rstrip("/"),
        llm_api_key=_override_str(
            config,
            "llm",
            "api_key",
            base.llm_api_key,
        ),
        tts_provider="dashscope_realtime",
        tts_model=_override_str(
            config,
            "tts",
            "model",
            base.tts_model,
        ),
        tts_voice=_override_str(
            config,
            "tts",
            "voice",
            base.tts_voice,
        ),
        tts_api_key=_override_str(
            config,
            "tts",
            "api_key",
            base.tts_api_key,
        ),
        tts_base_url=_override_str(
            config,
            "tts",
            "base_url",
            base.tts_base_url,
        ).rstrip("/"),
    )


def _filter_runtime_model_config(payload: dict[str, Any]) -> dict[str, Any]:
    """只保留模型运行时配置支持的字段。"""

    voice_payload = payload.get("voice")
    if not isinstance(voice_payload, dict):
        return {}

    voice: dict[str, Any] = {}
    for section, allowed_fields in _MODEL_RUNTIME_FIELDS["voice"].items():
        section_payload = voice_payload.get(section)
        if not isinstance(section_payload, dict):
            continue
        values = {
            key: value
            for key, value in section_payload.items()
            if key in allowed_fields and isinstance(value, str)
        }
        if values:
            voice[section] = values
    return {"voice": voice} if voice else {}


def _canonical_tts_provider(provider: str) -> str:
    """归一化 DashScope TTS provider 别名。"""

    clean = provider.lower().strip()
    if clean in {"dashscope", "dashscope_realtime", "qwen_realtime"}:
        return "dashscope_realtime"

    raise ValueError(f"当前只支持 DashScope TTS，实际配置为：{provider}")


def load_rag_client_settings(user_data_dir: Path | None = None) -> RagClientSettings:
    """读取环境变量和运行时配置后的 RAG 客户端配置。"""

    base = RagClientSettings()
    overrides = _read_runtime_rag_overrides(user_data_dir)
    values = {**base.__dict__, **overrides}
    return RagClientSettings(**values)


def load_context_model_settings(user_data_dir: Path | None = None) -> ContextModelSettings:
    """按环境默认值和运行时配置覆盖项生成上下文模型配置。"""

    base = _env_context_model_settings()
    config = read_runtime_context_model_config(user_data_dir)
    return ContextModelSettings(
        model=str(config.get("model") or base.model).strip(),
        base_url=str(config.get("base_url") or base.base_url).strip().rstrip("/"),
        api_key=str(config.get("api_key") or base.api_key).strip(),
        max_tokens=int(config.get("max_tokens") or base.max_tokens),
        max_session_chars=int(config.get("max_session_chars") or base.max_session_chars),
        history_reference_limit=int(
            config.get("history_reference_limit") or base.history_reference_limit
        ),
        timeout_ms=int(config.get("timeout_ms") or base.timeout_ms),
        temperature=float(
            config.get("temperature") if config.get("temperature") is not None else base.temperature
        ),
    )


def read_runtime_context_model_config(user_data_dir: Path | None = None) -> dict[str, Any]:
    """读取前端 API 写入的上下文模型配置覆盖项。"""

    path = runtime_context_model_config_path(user_data_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return _filter_runtime_context_model_config(payload)


def _filter_runtime_context_model_config(payload: dict[str, Any]) -> dict[str, Any]:
    """只保留上下文模型运行时配置支持的字段。"""

    return {
        key: value
        for key, value in payload.items()
        if key in _CONTEXT_MODEL_RUNTIME_FIELDS and isinstance(value, (str, int, float))
    }


def load_api_settings() -> ApiSettings:
    """读取管理 API 配置。"""

    return ApiSettings(
        rag_gateway_timeout_ms=_int_env("LIVERAG_RAG_GATEWAY_TIMEOUT_MS", 10000),
        rag_gateway_upload_timeout_ms=_int_env("LIVERAG_RAG_GATEWAY_UPLOAD_TIMEOUT_MS", 30000),
        rag_ready_timeout_ms=_int_env("LIVERAG_RAG_READY_TIMEOUT_MS", 15000),
    )

def public_voice_config(voice: VoiceSettings, *, effective: str) -> dict[str, Any]:
    """返回不含密钥的语音模型配置摘要。"""

    stt_app_id_masked = mask_secret(voice.stt_app_id, prefix_chars=4, suffix_chars=4)
    stt_access_token_masked = mask_secret(voice.stt_access_token)
    llm_api_key_masked = mask_secret(voice.llm_api_key)
    tts_api_key_masked = mask_secret(voice.tts_api_key)
    return {
        "stt": {
            "provider": voice.stt_provider,
            "model": voice.stt_model,
            "app_id_set": bool(voice.stt_app_id),
            "app_id_masked": stt_app_id_masked,
            "access_token_set": bool(voice.stt_access_token),
            "access_token": stt_access_token_masked,
            "access_token_masked": stt_access_token_masked,
            "effective": effective,
        },
        "llm": {
            "model": voice.llm_model,
            "base_url": voice.llm_base_url,
            "api_key_set": bool(voice.llm_api_key),
            "api_key": llm_api_key_masked,
            "api_key_masked": llm_api_key_masked,
            "effective": effective,
        },
        "tts": {
            "provider": voice.tts_provider,
            "model": voice.tts_model,
            "voice": voice.tts_voice,
            "api_key_set": bool(voice.tts_api_key),
            "api_key": tts_api_key_masked,
            "api_key_masked": tts_api_key_masked,
            "effective": effective,
        },
    }

def mask_secret(value: str, *, prefix_chars: int = 2, suffix_chars: int = 8) -> str:
    """把密钥转换成前端可展示的掩码值。"""

    clean = value.strip()
    if not clean:
        return ""
    if len(clean) <= prefix_chars + suffix_chars:
        short_suffix = min(2, max(len(clean) - prefix_chars, 0))
        return f"{clean[:prefix_chars]}{_MASKED_SECRET_MARKER}{clean[-short_suffix:] if short_suffix else ''}"
    return f"{clean[:prefix_chars]}{_MASKED_SECRET_MARKER}{clean[-suffix_chars:]}"

def is_masked_secret(value: Any) -> bool:
    """判断前端提交值是否是后端返回的密钥掩码。"""

    return isinstance(value, str) and _MASKED_SECRET_MARKER in value

def public_model_options() -> dict[str, Any]:
    """返回前端模型选择页使用的 provider、模型、音色和字段定义。"""

    return {
        "stt": {
            "providers": _STT_PROVIDER_OPTIONS,
            "default_provider": "volcengine_bigmodel",
        },
        "llm": {
            "mode": "manual",
            "description": "对话模型保持现有配置方式，前端继续填写 model、base_url 和 api_key。",
            "config_fields": [
                {"key": "model", "label": "Model", "type": "text", "required": True},
                {"key": "base_url", "label": "Base URL", "type": "url", "required": True},
                {"key": "api_key", "label": "API Key", "type": "secret", "required": True},
            ],
        },
        "tts": {
            "providers": _TTS_PROVIDER_OPTIONS,
            "default_provider": "minimax",
        },
    }


@dataclass(frozen=True)
class AppSettings:
    """LiveRAG Agent 统一配置"""

    user_data_dir: Path = field(
        default_factory=lambda: Path(_str_env("LIVERAG_USER_DATA_DIR", "~/.LiveRAG")).expanduser()
    )
    log_dir: Path = field(
        default_factory=lambda: Path(_str_env("LIVERAG_LOG_DIR", "~/.LiveRAG/logs")).expanduser()
    )
    history_limit: int = field(default_factory=lambda: _int_env("LIVERAG_HISTORY_LIMIT", 8))
    voice: VoiceSettings = field(default_factory=load_voice_settings)
    rag: RagClientSettings = field(default_factory=load_rag_client_settings)
    context_model: ContextModelSettings = field(default_factory=load_context_model_settings)
    api: ApiSettings = field(default_factory=load_api_settings)


def load_app_settings() -> AppSettings:
    """从环境变量加载当前应用配置。"""

    return AppSettings()


def _env_voice_settings() -> VoiceSettings:
    """读取环境变量中的语音模型默认配置。"""

    return VoiceSettings(
        livekit_url=_str_env("LIVEKIT_URL", ""),
        stt_provider=_str_env("VOICE_STT_PROVIDER", "volcengine_bigmodel"),
        stt_app_id=_str_env("VOLCENGINE_STT_APP_ID", ""),
        stt_access_token=_str_env("VOLCENGINE_STT_ACCESS_TOKEN", ""),
        stt_model=_str_env("VOLCENGINE_BIGMODEL_STT_MODEL", "bigmodel"),
        llm_model=_str_env("VOICE_LLM_MODEL", "qwen-flash"),
        llm_base_url=_str_env(
            "VOICE_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ),
        llm_api_key=_str_env("VOICE_LLM_API_KEY", _str_env("DASHSCOPE_API_KEY", "")),
        tts_provider="dashscope_realtime",
        tts_model=_str_env("VOICE_TTS_MODEL", _default_tts_model()),
        tts_voice=_str_env("VOICE_TTS_VOICE", _default_tts_voice()),
        tts_api_key=_str_env("VOICE_TTS_API_KEY", _default_tts_api_key()),
        tts_base_url=_str_env("VOICE_TTS_BASE_URL", _default_tts_base_url()).rstrip("/"),
    )


def _env_context_model_settings() -> ContextModelSettings:
    """读取环境变量中的上下文模型默认配置。"""

    return ContextModelSettings(
        model=_str_env("CONTEXT_MODEL_MODEL", "qwen-max"),
        base_url=_str_env(
            "CONTEXT_MODEL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/"),
        api_key=_str_env("CONTEXT_MODEL_API_KEY", _str_env("DASHSCOPE_API_KEY", "")),
        max_tokens=_int_env("CONTEXT_MODEL_MAX_TOKENS", 2000),
        max_session_chars=_int_env("CONTEXT_MODEL_MAX_SESSION_CHARS", 16000),
        history_reference_limit=_int_env("CONTEXT_MODEL_HISTORY_REFERENCE_LIMIT", 8),
        timeout_ms=_int_env("CONTEXT_MODEL_TIMEOUT_MS", 15000),
        temperature=_float_env("CONTEXT_MODEL_TEMPERATURE", 0.0),
    )


def _override_str(config: dict[str, Any], section: str, key: str, fallback: str) -> str:
    """读取运行时覆盖值，缺失或空值时使用默认值。"""

    value = config.get("voice", {}).get(section, {}).get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _default_tts_model() -> str:
    """返回 DashScope 实时 TTS 默认模型。"""

    return "qwen3-tts-flash-realtime"


def _default_tts_voice() -> str:
    """返回 DashScope 实时 TTS 默认音色。"""

    return "Cherry"


def _default_tts_api_key() -> str:
    """读取 DashScope API Key。"""

    return _str_env("DASHSCOPE_API_KEY", "")


def _default_tts_base_url() -> str:
    """返回 DashScope TTS 的 WebSocket 地址。"""

    return "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
