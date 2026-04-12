"""
请求追踪中间件
生成 trace_id 贯穿整个请求链路
记录每个 RAG Pipeline 阶段的 span
"""
from __future__ import annotations

import uuid
import time
from contextvars import ContextVar
from typing import Optional

# 进程级上下文变量（用于在同一协程中传递 trace 信息）
_current_trace: ContextVar[Optional[dict]] = ContextVar("current_trace", default=None)


def get_current_trace() -> Optional[dict]:
    """获取当前请求的 trace 上下文"""
    return _current_trace.get()


def get_trace_id() -> Optional[str]:
    """获取当前 trace_id"""
    trace = _current_trace.get()
    return trace.get("trace_id") if trace else None


def set_trace_tag(key: str, value: str):
    """为当前 trace 设置标签（如 intent, model 等）"""
    trace = _current_trace.get()
    if trace is not None:
        if "tags" not in trace:
            trace["tags"] = {}
        trace["tags"][key] = value


class TraceSpan:
    """追踪跨度"""

    def __init__(self, name: str):
        self.name = name
        self.start_time: float = 0
        self.end_time: float = 0
        self.tags: dict = {}

    def __enter__(self):
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end_time = time.perf_counter()
        # 打印 span 日志（供 ELK 收集）
        from app.logging_config import get_logger
        logger = get_logger("trace")
        duration_ms = (self.end_time - self.start_time) * 1000
        trace_id = get_trace_id()
        extra = {"trace_id": trace_id, "span": self.name, "duration_ms": round(duration_ms, 2)}
        if exc_type:
            extra["error"] = str(exc_val)
            extra["span_status"] = "error"
            logger.warning(f"[SPAN] {self.name} failed after {duration_ms:.2f}ms", extra=extra)
        else:
            extra["span_status"] = "ok"
            logger.debug(f"[SPAN] {self.name} completed in {duration_ms:.2f}ms", extra=extra)


# ---------------------------------------------------------------------------
# FastAPI 中间件
# ---------------------------------------------------------------------------
async def tracing_middleware(request, call_next):
    """追踪中间件入口"""
    trace_id = request.headers.get("X-Trace-ID") or str(uuid.uuid4())
    start_time = time.perf_counter()

    # 设置上下文变量
    trace_data = {
        "trace_id": trace_id,
        "request_path": str(request.url.path),
        "request_method": request.method,
        "client_ip": request.client.host if request.client else "unknown",
    }
    token = _current_trace.set(trace_data)

    try:
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000

        # 添加 trace 头到响应
        response.headers["X-Trace-ID"] = trace_id
        response.headers["X-Response-Time-Ms"] = str(round(duration_ms, 2))

        from app.logging_config import get_logger
        logger = get_logger("request")
        logger.info(
            f"{request.method} {request.url.path} {response.status_code} {duration_ms:.2f}ms",
            extra={
                "trace_id": trace_id,
                "method": request.method,
                "path": str(request.url.path),
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 2),
                "client_ip": request.client.host if request.client else "unknown",
            }
        )

        return response
    except Exception:
        duration_ms = (time.perf_counter() - start_time) * 1000
        from app.logging_config import get_logger
        logger = get_logger("request")
        logger.error(
            f"{request.method} {request.url.path} ERROR after {duration_ms:.2f}ms",
            extra={
                "trace_id": trace_id,
                "method": request.method,
                "path": str(request.url.path),
                "duration_ms": round(duration_ms, 2),
            }
        )
        raise
    finally:
        _current_trace.reset(token)
