"""
结构化日志配置。
"""

from __future__ import annotations

import logging
import sys


def setup_logging(level: str = "INFO", json_format: bool = False):
    """
    配置全局日志。

    Args:
        level: 日志级别 (DEBUG/INFO/WARNING/ERROR)
        json_format: 是否使用JSON格式输出（生产环境推荐）
    """
    root = logging.getLogger("travelclaw")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    if root.handlers:
        return  # 避免重复配置

    handler = logging.StreamHandler(sys.stderr)

    if json_format:
        fmt = (
            '{"time":"%(asctime)s","level":"%(levelname)s",'
            '"logger":"%(name)s","message":"%(message)s"}'
        )
    else:
        fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    handler.setFormatter(logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(handler)
