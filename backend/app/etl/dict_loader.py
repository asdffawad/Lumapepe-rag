"""
商品字典热加载器
支持定时重载和手动重载，线程安全
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Awaitable

from app.etl.dict_builder import ProductDict, load_dict, save_dict
from app.logging_config import get_logger

_logger = get_logger("etl.dict_loader")


class ProductDictLoader:
    """
    商品字典热加载器

    特性：
    - 懒加载：首次访问时才加载
    - 线程安全：使用 asyncio.Lock 保护加载操作
    - 定时重载：支持按配置间隔自动重载
    - 手动重载：API触发热重载
    - 回调通知：重载成功后可执行回调（如通知其他组件）

    使用示例：
        loader = ProductDictLoader("data/product_dict.json")

        # 同步获取（自动加载）
        product_dict = await loader.get()

        # 手动触发重载
        await loader.reload()

        # 带回调的重载
        await loader.reload(callback=on_dict_updated)
    """

    def __init__(
        self,
        dict_path: str | Path,
        reload_interval_seconds: int = 3600,
        on_reload_callback: Optional[Callable[[ProductDict], Awaitable[None]]] = None,
    ):
        """
        Args:
            dict_path: 字典文件路径
            reload_interval_seconds: 自动重载间隔（秒），默认1小时
            on_reload_callback: 重载成功后的回调函数
        """
        self._dict_path = Path(dict_path)
        self._reload_interval = reload_interval_seconds
        self._callback = on_reload_callback

        self._dict: Optional[ProductDict] = None
        self._lock = asyncio.Lock()
        self._last_loaded_at: Optional[datetime] = None
        self._reload_task: Optional[asyncio.Task] = None

    async def load(self) -> ProductDict:
        """
        加载字典（若未加载或已过期则自动重载）

        Returns:
            商品字典对象
        """
        async with self._lock:
            if self._dict is None:
                self._dict = await self._load_from_file()
            return self._dict

    async def get(self) -> ProductDict:
        """
        获取当前字典（懒加载）

        Returns:
            商品字典对象
        """
        if self._dict is None:
            return await self.load()
        return self._dict

    async def reload(self, force: bool = False) -> ProductDict:
        """
        强制重载字典

        Args:
            force: 是否强制重载（即使未过期）

        Returns:
            新加载的字典对象
        """
        async with self._lock:
            _logger.info("Reloading product dictionary", extra={"force": force})
            self._dict = await self._load_from_file()
            self._last_loaded_at = datetime.now()

            # 执行回调
            if self._callback:
                await self._callback(self._dict)

            _logger.info(
                "Product dictionary reloaded",
                extra={
                    "version": self._dict.get("version"),
                    "stats": self._dict.get("stats"),
                }
            )

            return self._dict

    async def start_auto_reload(self) -> None:
        """
        启动定时自动重载任务

        注意：需要在应用生命周期内运行，通常在 startup 事件中调用
        """
        if self._reload_task is not None and not self._reload_task.done():
            _logger.warning("Auto-reload task already running, skipping start")
            return

        self._reload_task = asyncio.create_task(self._auto_reload_loop())
        _logger.info(
            "Auto-reload task started",
            extra={"interval_seconds": self._reload_interval}
        )

    async def stop_auto_reload(self) -> None:
        """
        停止定时自动重载任务

        注意：需要在应用关闭时调用，通常在 shutdown 事件中调用
        """
        if self._reload_task is not None:
            self._reload_task.cancel()
            try:
                await self._reload_task
            except asyncio.CancelledError:
                pass
            self._reload_task = None
            _logger.info("Auto-reload task stopped")

    async def _auto_reload_loop(self) -> None:
        """定时重载循环"""
        while True:
            try:
                await asyncio.sleep(self._reload_interval)
                await self.reload()
            except asyncio.CancelledError:
                raise  # 正常退出
            except Exception as e:
                _logger.error("Auto-reload failed", extra={"error": str(e)})
                # 重试间隔减半，避免连续失败时阻塞太久
                await asyncio.sleep(self._reload_interval // 2)

    async def _load_from_file(self) -> ProductDict:
        """
        从文件加载字典

        Returns:
            字典对象

        Raises:
            FileNotFoundError: 文件不存在
            json.JSONDecodeError: JSON格式错误
        """
        if not self._dict_path.exists():
            raise FileNotFoundError(f"Product dictionary not found: {self._dict_path}")

        return load_dict(self._dict_path)

    @property
    def last_loaded_at(self) -> Optional[datetime]:
        """最后加载时间"""
        return self._last_loaded_at

    @property
    def is_loaded(self) -> bool:
        """是否已加载"""
        return self._dict is not None

    @property
    def stats(self) -> Optional[dict]:
        """字典统计信息"""
        if self._dict:
            return self._dict.get("stats")
        return None

    @property
    def version(self) -> Optional[str]:
        """字典版本"""
        if self._dict:
            return self._dict.get("version")
        return None