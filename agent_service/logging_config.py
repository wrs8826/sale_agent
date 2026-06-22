"""集中式日志配置。

控制台输出统一格式：`时间 [级别] 模块名: 消息`。
级别来源优先级：环境变量 `LOG_LEVEL` > `config.yaml` 顶层 `log_level` > 默认 `INFO`。

用法：
    # 入口处（create_app 最前）调一次：
    from agent_service.logging_config import setup_logging
    setup_logging()

    # 业务模块里取 logger：
    from agent_service.logging_config import get_logger
    log = get_logger(__name__)
    log.info("...")  log.warning("...")  log.error("...")  log.debug("...")

排查问题时把 config.yaml 的 `log_level` 改为 `DEBUG`（或设环境变量 `LOG_LEVEL=DEBUG`）即可，
DEBUG 级别下还会放开 httpx/openai 等三方库日志，便于追踪外部 API 调用。
"""
from __future__ import annotations

import logging
import os

import yaml

from . import CONFIG_PATH

_DEFAULT_LEVEL = "INFO"
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 默认压低的三方库（非 DEBUG 时只看它们的 WARNING 及以上，避免刷屏）
_NOISY_LIBS = ("httpx", "httpcore", "urllib3", "openai", "chromadb", "sentence_transformers")

_configured = False


def _read_log_level() -> str:
    """读取期望日志级别名（大写）。环境变量优先，其次 config.yaml，最后默认。"""
    env = os.getenv("LOG_LEVEL")
    if env and env.strip():
        return env.strip().upper()
    try:
        if CONFIG_PATH.exists():
            with CONFIG_PATH.open("r", encoding="utf-8") as reader:
                data = yaml.safe_load(reader) or {}
            level = str(data.get("log_level") or "").strip().upper()
            if level:
                return level
    except Exception as exc:  # 配置读取失败不应阻断启动
        print(f"[logging] 读取 log_level 失败，回退 {_DEFAULT_LEVEL}: {exc}")
    return _DEFAULT_LEVEL


def _coerce_level(name: str) -> int:
    """把级别名转成 logging 整数级别；无法识别时回退 INFO。"""
    level = getattr(logging, name, None)
    return level if isinstance(level, int) else logging.INFO


def setup_logging(force: bool = False) -> int:
    """配置根 logger 的控制台输出与级别；幂等（重复调用不会叠加 handler）。

    返回生效的整数日志级别。`force=True` 可在配置变更后重配。
    """
    global _configured
    if _configured and not force:
        return logging.getLogger().level

    level_name = _read_log_level()
    level = _coerce_level(level_name)

    root = logging.getLogger()
    root.setLevel(level)

    # 去重：移除本模块此前装过的 handler（双进程 / 热重载下避免重复打印）
    root.handlers = [h for h in root.handlers if not getattr(h, "_sales_agent", False)]

    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
    handler._sales_agent = True  # 标记，便于上面的去重识别
    root.addHandler(handler)

    # 三方库：非 DEBUG 时压到 WARNING；DEBUG 时放开以便追踪外部 API
    noisy_level = logging.DEBUG if level <= logging.DEBUG else logging.WARNING
    for lib in _NOISY_LIBS:
        logging.getLogger(lib).setLevel(noisy_level)

    _configured = True
    logging.getLogger(__name__).debug("日志已初始化，级别=%s", level_name)
    return level


def get_logger(name: str) -> logging.Logger:
    """获取命名 logger；若尚未配置则先做一次默认配置。"""
    if not _configured:
        setup_logging()
    return logging.getLogger(name)
