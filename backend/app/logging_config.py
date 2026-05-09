"""
结构化日志模块
使用 Python 标准库 logging，以 JSON 格式输出到 stdout
供 Docker / ELK / Grafana Loki 收集
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

# 日志级别
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


class JSONFormatter(logging.Formatter):
    """JSON 格式日志格式化器"""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "process_id": record.process,
            "thread_id": record.thread,
        }

        # 添加 extra 字段
        if hasattr(record, "request_id"):
            entry["request_id"] = record.request_id
        if hasattr(record, "user_id"):
            entry["user_id"] = record.user_id
        if hasattr(record, "duration_ms"):
            entry["duration_ms"] = record.duration_ms
        if hasattr(record, "intent"):
            entry["intent"] = record.intent
        if hasattr(record, "model"):
            entry["model"] = record.model
        for key in (
            "db_host",
            "db_port",
            "db_name",
            "db_user",
            "db_connect_retries",
            "db_connect_timeout",
            "conversation_id",
            "conversation_status",
            "conversation_user_id",
            "assigned_admin_id",
            "jwt_sub",
            "jwt_role",
            "resolved_admin_id",
            "message_id",
            "sender_type",
            "sender_id",
            "receiver_type",
            "receiver_id",
            "content_type",
            "content",
            "content_length",
            "tenant_id",
            "client_msg_id",
            "unread_field",
            "missing_field",
            "target_role",
            "target_count",
            "sent_count",
            "stale_count",
            "user_connection_count",
            "admin_connection_count",
            "event",
            "error",
        ):
            if hasattr(record, key):
                entry[key] = getattr(record, key)

        # 添加异常信息
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(entry, ensure_ascii=False)


# 全局日志记录器缓存
_loggers: dict[str, logging.Logger] = {}


def get_logger(name: str) -> logging.Logger:
    """获取结构化日志记录器（进程级单例）"""
    if name not in _loggers:
        _loggers[name] = _create_logger(name)
    return _loggers[name]


def _create_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.propagate = False
    return logger

