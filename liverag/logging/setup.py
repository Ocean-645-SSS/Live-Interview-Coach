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

    import os as _os
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _log_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))), "logs")
    _os.makedirs(_log_dir, exist_ok=True)
    _log_file = _os.path.join(_log_dir, "liverag.log")
    _fh = logging.FileHandler(_log_file, encoding="utf-8")
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.getLogger().addHandler(_fh)
