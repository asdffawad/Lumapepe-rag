"""
Business alerting module.
Monitors critical metrics and triggers alerts on anomalies.
Alert conditions:
- LLM consecutive failures > 5
- Vector retrieval consecutive failures > 10
- P99 latency > 30s
- Memory usage > 80%
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import time
import threading
import psutil
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Awaitable

from app.logging_config import get_logger

logger = get_logger("alerting")


class AlertLevel(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Alert:
    level: AlertLevel
    source: str
    message: str
    metric: str
    value: float
    threshold: float
    timestamp: float = field(default_factory=time.time)


AlertHandler = Callable[[Alert], Awaitable[None]]


async def _default_alert_handler(alert: Alert):
    level_str = {
        AlertLevel.INFO: "INFO",
        AlertLevel.WARNING: "WARN",
        AlertLevel.CRITICAL: "CRITICAL",
    }[alert.level]
    logger.warning(
        f"[ALERT][{level_str}] {alert.source}: {alert.message}",
        extra={
            "alert_level": alert.level.value,
            "alert_source": alert.source,
            "alert_metric": alert.metric,
            "alert_value": alert.value,
            "alert_threshold": alert.threshold,
        },
    )


_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="alert_emitter")


def _emit_alert_sync(alert: Alert):
    try:
        asyncio.run(_default_alert_handler(alert))
    except Exception:
        logger.error(f"[ALERT][FALLBACK] {alert.source}: {alert.message}")


def _schedule_alert(alert: Alert):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _executor.submit(_emit_alert_sync, alert)
    else:
        asyncio.ensure_future(_default_alert_handler(alert))


class AlertMonitor:
    def __init__(
        self,
        llm_failure_threshold=5,
        vector_failure_threshold=10,
        latency_threshold_ms=30000.0,
        memory_threshold_pct=80.0,
        check_interval_seconds=60.0,
    ):
        self.llm_failure_threshold = llm_failure_threshold
        self.vector_failure_threshold = vector_failure_threshold
        self.latency_threshold_ms = latency_threshold_ms
        self.memory_threshold_pct = memory_threshold_pct
        self.check_interval = check_interval_seconds
        self._llm_failures = 0
        self._vector_failures = 0
        self._latency_samples = []
        self._latency_max_window = 100
        self._handlers = [_default_alert_handler]
        self._running = False
        self._task = None
        self._lock = threading.Lock()

    def add_handler(self, handler):
        self._handlers.append(handler)

    async def _emit(self, alert):
        for handler in self._handlers:
            try:
                result = handler(alert)
                if hasattr(result, "__await__"):
                    await result
            except Exception as e:
                logger.error(f"Alert handler failed: {e}")

    def record_llm_success(self):
        if self._llm_failures > 0:
            self._llm_failures = 0
            logger.info("LLM failure count reset to 0")

    def record_llm_failure(self, error=""):
        with self._lock:
            self._llm_failures += 1
        if self._llm_failures == self.llm_failure_threshold:
            alert = Alert(
                level=AlertLevel.CRITICAL,
                source="llm",
                message=f"LLM consecutive failures {self._llm_failures}, check DashScope service. error={error}",
                metric="llm_failure_count",
                value=float(self._llm_failures),
                threshold=float(self.llm_failure_threshold),
            )
            try:
                loop = asyncio.get_running_loop()
                asyncio.ensure_future(self._emit(alert))
            except RuntimeError:
                asyncio.run(self._emit(alert))

    def record_vector_success(self):
        if self._vector_failures > 0:
            self._vector_failures = 0

    def record_vector_failure(self, error=""):
        with self._lock:
            self._vector_failures += 1
        if self._vector_failures == self.vector_failure_threshold:
            alert = Alert(
                level=AlertLevel.WARNING,
                source="vector",
                message=f"Vector retrieval consecutive failures {self._vector_failures}, check Milvus service. error={error}",
                metric="vector_failure_count",
                value=float(self._vector_failures),
                threshold=float(self.vector_failure_threshold),
            )
            try:
                loop = asyncio.get_running_loop()
                asyncio.ensure_future(self._emit(alert))
            except RuntimeError:
                asyncio.run(self._emit(alert))

    def record_latency(self, latency_ms):
        with self._lock:
            self._latency_samples.append(latency_ms)
            if len(self._latency_samples) > self._latency_max_window:
                self._latency_samples = self._latency_samples[-self._latency_max_window:]
            if len(self._latency_samples) >= 10:
                p99 = sorted(self._latency_samples)[int(len(self._latency_samples) * 0.99)]
                if p99 > self.latency_threshold_ms:
                    alert = Alert(
                        level=AlertLevel.WARNING,
                        source="latency",
                        message=f"P99 latency {p99:.0f}ms exceeds threshold {self.latency_threshold_ms:.0f}ms",
                        metric="p99_latency_ms",
                        value=p99,
                        threshold=self.latency_threshold_ms,
                    )
                    try:
                        loop = asyncio.get_running_loop()
                        asyncio.ensure_future(self._emit(alert))
                    except RuntimeError:
                        asyncio.run(self._emit(alert))

    async def _check_system_resources(self):
        try:
            memory_pct = psutil.virtual_memory().percent
            if memory_pct > self.memory_threshold_pct:
                await self._emit(Alert(
                    level=AlertLevel.CRITICAL,
                    source="system",
                    message=f"Memory usage {memory_pct:.1f}% exceeds threshold {self.memory_threshold_pct:.1f}%",
                    metric="memory_percent",
                    value=memory_pct,
                    threshold=self.memory_threshold_pct,
                ))
        except Exception as e:
            logger.error(f"Failed to check system resources: {e}")

    async def _monitor_loop(self):
        logger.info(f"Alert monitor started (interval={self.check_interval}s)")
        while self._running:
            try:
                await asyncio.sleep(self.check_interval)
                await self._check_system_resources()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Alert monitor error: {e}")
        logger.info("Alert monitor stopped")

    def start(self):
        if not self._running:
            self._running = True
            try:
                loop = asyncio.get_running_loop()
                self._task = loop.create_task(self._monitor_loop())
            except RuntimeError:
                def run_loop():
                    asyncio.run(self._monitor_loop())
                self._monitor_thread = threading.Thread(target=run_loop, daemon=True)
                self._monitor_thread.start()

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def get_status(self):
        with self._lock:
            p99 = (
                sorted(self._latency_samples)[int(len(self._latency_samples) * 0.99)]
                if len(self._latency_samples) >= 10
                else None
            )
            return {
                "running": self._running,
                "llm_failures": self._llm_failures,
                "vector_failures": self._vector_failures,
                "latency_samples": len(self._latency_samples),
                "latency_p99": p99,
            }


_monitor = None


def get_monitor():
    global _monitor
    if _monitor is None:
        _monitor = AlertMonitor()
    return _monitor
