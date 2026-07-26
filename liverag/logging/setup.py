"""标准日志初始化。"""

from __future__ import annotations

import logging


def setup_logging() -> None:
    """初始化基础日志格式。
    固定格式：时间:Y-M-d H:M:S
             日志级别:INFO/WARNING/ERROR
             日志器名称:agent/liverag.rag.service
             日志内容
    """

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
