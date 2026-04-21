"""
文本向量化服务（企业级）
支持通义文本 Embedding + Mock Embedding（测试用）
集成 EmbedCache 实现 cache-aside 模式

企业级增强:
- 连接池复用
- 批量大小限制（避免超长请求）
- 速率限制与重试
- 结构化日志
- Metrics 埋点
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.config import AppConfig

from app.logging_config import get_logger
from app import metrics as _metrics

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
_embed_logger = get_logger("embedding")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
# DashScope Embedding API 单次最大文本数（API 限制）
MAX_BATCH_SIZE = 10
# 文本最大 token 数（超过则截断）
MAX_TEXT_LENGTH = 8000
# Embedding 向量维度（与 Milvus schema 一致）
DEFAULT_DIM = 1024


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class EmbeddingError(Exception):
    pass


class EmbeddingRateLimitError(EmbeddingError):
    """速率限制异常"""
    pass


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------
class EmbeddingBase(ABC):
    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        pass

    @abstractmethod
    def get_dim(self) -> int:
        pass

    def preprocess_text(self, text: str) -> str:
        """文本预处理：去除多余空白、截断超长文本"""
        text = text.strip()
        if len(text) > MAX_TEXT_LENGTH:
            text = text[:MAX_TEXT_LENGTH]
        return text


# ---------------------------------------------------------------------------
# Mock Embedding（确定性，用于测试和降级）
# ---------------------------------------------------------------------------
class MockEmbedding(EmbeddingBase):
    def __init__(self, dim: int = DEFAULT_DIM):
        self._dim = dim

    def get_dim(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        await asyncio.sleep(0.01)
        return [
            self._deterministic_vector(self.preprocess_text(t), i)
            for i, t in enumerate(texts)
        ]

    def _deterministic_vector(self, text: str, seed: int) -> list[float]:
        """确定性向量（相同文本产生相同向量，便于缓存测试）"""
        vec = []
        for j in range(self._dim):
            val = hashlib.md5(f"{text}_{seed}_{j}".encode()).digest()[j % 16]
            vec.append((val / 255.0) * 2 - 1)
        return vec


# ---------------------------------------------------------------------------
# DashScope Embedding（企业级）
# ---------------------------------------------------------------------------
class DashScopeEmbedding(EmbeddingBase):
    """
    DashScope Embedding 服务（企业级）

    特性：
    - Cache-Aside 缓存模式
    - 连接池复用
    - 批量大小分片（自动拆分为每批最多 MAX_BATCH_SIZE 条）
    - 速率限制处理（429 自动退避）
    - 重试机制
    """

    _POOL_LIMITS = httpx.Limits(
        max_keepalive_connections=10,
        max_connections=20,
        keepalive_expiry=30.0,
    )

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://dashscope.aliyuncs.com",
        embed_cache: "EmbedCache | None" = None,
        max_retries: int = 3,
        timeout: int = 30,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._embed_cache = embed_cache
        self._max_retries = max_retries
        self._timeout = timeout
        self._dim: int | None = None
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=10.0),
                limits=self._POOL_LIMITS,
                follow_redirects=True,
            )
        return self._client

    def get_dim(self) -> int:
        if self._dim is None:
            return DEFAULT_DIM
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """
        主入口：
        1. 文本预处理
        2. 缓存查询
        3. 分批调用 API（未命中部分）
        4. 缓存回填
        """
        start = time.monotonic()
        total = len(texts)

        # 预处理
        processed = [self.preprocess_text(t) for t in texts]

        # Step 1: 缓存查询
        if self._embed_cache:
            cache_results: list[list[float] | None] = [None] * total
            miss_indices: list[int] = []

            for i, text in enumerate(processed):
                cached = await self._embed_cache.get(text)
                if cached is not None:
                    cache_results[i] = cached
                else:
                    miss_indices.append(i)

            _embed_logger.debug(
                "Embedding cache lookup",
                extra={"total": total, "hits": total - len(miss_indices), "misses": len(miss_indices)}
            )

            if not miss_indices:
                latency_ms = int((time.monotonic() - start) * 1000)
                _embed_logger.info(
                    "Embedding cache hit",
                    extra={"total": total, "latency_ms": latency_ms}
                )
                _metrics.embedding_cache_hits.inc(total)
                return cache_results  # type: ignore

            # 部分命中：先填入缓存结果，再处理未命中
            miss_texts = [processed[i] for i in miss_indices]
            miss_vectors = await self._batch_embed(miss_texts)

            # 组装结果
            for idx, vec in zip(miss_indices, miss_vectors):
                cache_results[idx] = vec

            # 回填缓存
            for text, vec in zip(miss_texts, miss_vectors):
                await self._embed_cache.set(text, vec)

            _metrics.embedding_cache_hits.inc(total - len(miss_indices))
            _metrics.embedding_cache_misses.inc(len(miss_indices))

            latency_ms = int((time.monotonic() - start) * 1000)
            _embed_logger.info(
                "Embedding completed (partial cache)",
                extra={"total": total, "hits": total - len(miss_indices), "latency_ms": latency_ms}
            )
            return cache_results  # type: ignore
        else:
            # 无缓存，直接分批调用
            return await self._batch_embed(processed)

    async def _batch_embed(self, texts: list[str]) -> list[list[float]]:
        """分批调用 API（自动拆分，避免超限）"""
        if not texts:
            return []

        all_vectors: list[list[float]] = []
        total_latency = 0

        # 分批
        for i in range(0, len(texts), MAX_BATCH_SIZE):
            batch = texts[i:i + MAX_BATCH_SIZE]
            vectors, latency = await self._call_api(batch)
            all_vectors.extend(vectors)
            total_latency += latency

            # 分批间小延迟，避免触发限流
            if i + MAX_BATCH_SIZE < len(texts):
                await asyncio.sleep(0.05)

        _embed_logger.info(
            "Embedding batch completed",
            extra={"batches": (len(texts) + MAX_BATCH_SIZE - 1) // MAX_BATCH_SIZE, "total": len(texts)}
        )
        return all_vectors

    async def _call_api(self, texts: list[str]) -> tuple[list[list[float]], int]:
        """
        调用 DashScope Embedding API

        Returns:
            (vectors, latency_ms)
        """
        start = time.monotonic()

        # 无 API Key 时降级到确定性 Mock
        if not self.api_key:
            vectors = [
                self._deterministic_vector(text, i)
                for i, text in enumerate(texts)
            ]
            if self._dim is None:
                self._dim = DEFAULT_DIM
            latency_ms = int((time.monotonic() - start) * 1000)
            return vectors, latency_ms

        client = self._get_client()
        last_error: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                http_resp = await client.post(
                    f"{self.base_url}/embeddings",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "text-embedding-v3",
                        "input": texts,
                    },
                )

                if http_resp.status_code == 429:
                    wait = 2 ** attempt
                    _embed_logger.warning(
                        "Embedding rate limited, backing off",
                        extra={"attempt": attempt + 1, "wait_s": wait}
                    )
                    if attempt < self._max_retries - 1:
                        await asyncio.sleep(wait)
                        continue
                    raise EmbeddingRateLimitError("Embedding API rate limited after retries")

                http_resp.raise_for_status()
                data = http_resp.json()

                embeddings = data["data"]
                # 按 input 顺序排序
                sorted_embeddings = sorted(embeddings, key=lambda e: e.get("index", 0))
                vectors = [e["embedding"] for e in sorted_embeddings]

                if self._dim is None and vectors:
                    self._dim = len(vectors[0])

                latency_ms = int((time.monotonic() - start) * 1000)

                _embed_logger.info(
                    "Embedding API call completed",
                    extra={
                        "batch_size": len(texts),
                        "latency_ms": latency_ms,
                        "model": "text-embedding-v3",
                    }
                )
                return vectors, latency_ms

            except httpx.HTTPStatusError as e:
                last_error = e
                err_code = e.response.status_code
                if err_code in (429, 500, 502, 503):
                    if attempt < self._max_retries - 1:
                        wait = 2 ** attempt
                        _embed_logger.warning(
                            "Embedding API error, retrying",
                            extra={"status": err_code, "attempt": attempt + 1, "wait_s": wait}
                        )
                        await asyncio.sleep(wait)
                        continue
                _embed_logger.error(
                    "Embedding API HTTP error",
                    extra={"status": err_code, "error": str(e)[:200]}
                )
                raise EmbeddingError(f"Embedding API HTTP {err_code}: {e.response.text[:200]}") from e

            except httpx.RequestError as e:
                last_error = e
                if attempt < self._max_retries - 1:
                    wait = 2 ** attempt
                    _embed_logger.warning(
                        "Embedding network error, retrying",
                        extra={"attempt": attempt + 1, "error": str(e)[:100]}
                    )
                    await asyncio.sleep(wait)
                    continue
                _embed_logger.error(
                    "Embedding network error (exhausted retries)",
                    extra={"error": str(e)[:200]}
                )
                raise EmbeddingError(f"Embedding network error: {e}") from e

        # 回退到确定性 Mock
        _embed_logger.warning(
            "Embedding API exhausted retries, falling back to mock vectors",
            extra={"attempts": self._max_retries}
        )
        vectors = [self._deterministic_vector(text, i) for i, text in enumerate(texts)]
        latency_ms = int((time.monotonic() - start) * 1000)
        if self._dim is None:
            self._dim = DEFAULT_DIM
        return vectors, latency_ms

    def _deterministic_vector(self, text: str, seed: int) -> list[float]:
        """确定性 Mock 向量（与 API 向量维度一致）"""
        vec = []
        for j in range(self._dim or DEFAULT_DIM):
            val = hashlib.md5(f"{text}_{seed}_{j}".encode()).digest()[j % 16]
            vec.append((val / 255.0) * 2 - 1)
        return vec

    async def close(self):
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# ---------------------------------------------------------------------------
# EmbedCache
# ---------------------------------------------------------------------------
class EmbedCache:
    """Embedding 结果缓存（Redis）"""
    _TTL = 7 * 24 * 3600  # 7 days

    def __init__(self, redis_client, ttl: int = _TTL):
        self._r = redis_client
        self._ttl = ttl

    def _hash(self, text: str) -> str:
        return hashlib.md5(text.encode()).hexdigest()

    async def get(self, text: str) -> list[float] | None:
        import orjson
        key = f"embed:cache:{self._hash(text)}"
        data = await self._r.get(key)
        if data:
            return orjson.loads(data)
        return None

    async def set(self, text: str, embedding: list[float]):
        import orjson
        key = f"embed:cache:{self._hash(text)}"
        await self._r.set(key, orjson.dumps(embedding), ex=self._ttl)


# ---------------------------------------------------------------------------
# EmbeddingService 单例
# ---------------------------------------------------------------------------
class EmbeddingService:
    _instance: EmbeddingBase | None = None

    @classmethod
    def init(cls, config: "AppConfig", redis_client=None):
        if config.llm.provider == "mock":
            cls._instance = MockEmbedding()
            _embed_logger.info("EmbeddingService initialized with MockEmbedding")
        else:
            embed_cache: EmbedCache | None = None
            if redis_client is not None:
                embed_cache = EmbedCache(redis_client)

            cls._instance = DashScopeEmbedding(
                api_key=config.llm.api_key,
                base_url=config.llm.base_url,
                embed_cache=embed_cache,
            )
            _embed_logger.info("EmbeddingService initialized with DashScopeEmbedding")

    @classmethod
    def get_instance(cls) -> EmbeddingBase:
        if cls._instance is None:
            cls._instance = MockEmbedding()
        return cls._instance

    @classmethod
    async def embed_batch(cls, texts: list[str]) -> list[list[float]]:
        return await cls.get_instance().embed(texts)

    @classmethod
    async def shutdown(cls):
        """优雅关闭（关闭 HTTP 客户端）"""
        if isinstance(cls._instance, DashScopeEmbedding):
            await cls._instance.close()
