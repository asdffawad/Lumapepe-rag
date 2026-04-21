"""
Debug API 路由
POST /api/v1/debug/retrieve — 分阶段检索调试
GET  /api/v1/debug/embed   — Embedding 测试
POST /api/v1/debug/auth/token — 获取测试用 JWT Token（仅用于测试环境）
"""
from __future__ import annotations

import math
import time
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.logging_config import get_logger
from app.config import get_config
from app.middleware.auth import create_token
from app.services.vector_retriever import SimpleVectorRetriever, HybridRetriever, RetrievedDoc
from app.services.reranker import MMRReranker, RerankedDoc
from app.services.llm_generator import StandardLLMGenerator
from app.services.prompt_builder import Prompt

router = APIRouter(prefix="/api/v1/debug", tags=["debug"])
_logger = get_logger("debug.api")


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------
class RetrieveRequest(BaseModel):
    query: str = Field(..., description="查询词（必填）")
    top_k: int = Field(default=10, description="召回数量")
    rerank_top_k: int = Field(default=5, description="重排后保留")
    hybrid_search: bool = Field(default=True, description="开启 BM25 混合")
    bm25_weight: float = Field(default=0.3, description="BM25 融合权重")
    use_llm_rerank: bool = Field(default=False, description="启用 LLM 重排")
    lang_filter: list[str] = Field(default_factory=list, description="限定语言 ['zh', 'en', 'vi']")
    section_type_filter: list[str] = Field(
        default_factory=list, description="限定类型 ['bullet', 'ordered', 'body', 'heading', 'note']"
    )
    run_llm: bool = Field(default=False, description="是否执行 LLM 生成")
    intent: str = Field(default="product_qa", description="意图分类")


class DenseItem(BaseModel):
    content: str
    dense_score: float
    section_type: str = ""
    markets: list[str] = Field(default_factory=list)
    lang: str = ""
    hierarchy: str = ""
    source: str = ""


class BM25Item(BaseModel):
    content: str
    bm25_raw: float
    bm25_norm: float
    source: str = ""


class RRFItem(BaseModel):
    content: str
    rrf_score: float
    dense_rank: int
    bm25_rank: int
    section_type: str = ""
    markets: list[str] = Field(default_factory=list)
    lang: str = ""
    source: str = ""


class RerankedItem(BaseModel):
    content: str
    rerank_score: float
    mmr_reason: str
    section_type: str = ""
    markets: list[str] = Field(default_factory=list)
    lang: str = ""
    source: str = ""


class LLMStage(BaseModel):
    message: str
    intent: str
    latency_ms: float
    tokens_used: int
    prompt_preview: str


class StageDense(BaseModel):
    count: int
    items: list[DenseItem]
    duration_ms: float


class StageBM25(BaseModel):
    count: int
    items: list[BM25Item]
    duration_ms: float


class StageRRF(BaseModel):
    count: int
    items: list[RRFItem]
    duration_ms: float


class StageRerank(BaseModel):
    count: int
    items: list[RerankedItem]
    duration_ms: float
    method: str = "rule"


class Stages(BaseModel):
    dense: StageDense | None = None
    bm25: StageBM25 | None = None
    rrf: StageRRF | None = None
    rerank: StageRerank | None = None
    llm: LLMStage | None = None


class RetrieveResponse(BaseModel):
    params: dict[str, Any]
    stages: Stages
    total_latency_ms: float


class EmbedResponse(BaseModel):
    text: str
    token_count: int
    vector_dim: int
    vector_preview: list[float]
    latency_ms: float


# ---------------------------------------------------------------------------
# Token 估算（经验公式）
# ---------------------------------------------------------------------------
def _estimate_tokens(text: str) -> int:
    """汉字×1.5 + 英文×0.25"""
    chars_zh = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    chars_en = sum(1 for c in text if c.isalpha())
    other = len(text) - chars_zh - chars_en
    return int(chars_zh * 1.5 + chars_en * 0.25 + other * 0.5)


# ---------------------------------------------------------------------------
# mmr_reason 生成
# ---------------------------------------------------------------------------
def _infer_mmr_reason(doc: RetrievedDoc, doc_content_lower: str) -> str:
    """从 reranked doc 内容推断 mmr 排序原因"""
    boost_kws = [
        "新品", "热销", "活动", "特价", "爆款", "推荐", "促销", "打折",
        "限时", "爆款推荐", "今日特惠", "爆款热卖", "正品", "包邮", "急速发货",
    ]
    for kw in boost_kws:
        if kw in doc_content_lower:
            return "关键词命中"

    hierarchy = doc.metadata.get("hierarchy", {})
    if hierarchy:
        return "综合排序"

    section_type = doc.metadata.get("section_type", "")
    if section_type in ("bullet", "ordered", "heading"):
        return "章节类型偏好"

    markets = doc.metadata.get("markets", [])
    if isinstance(markets, list) and markets:
        return "市场偏好"

    return "综合排序"


# ---------------------------------------------------------------------------
# BM25 + RRF 中间计算（从 HybridRetriever 逻辑中提取）
# ---------------------------------------------------------------------------
def _compute_bm25_and_rrf(
    query: str,
    dense_docs: list[RetrievedDoc],
    milvus_client,
    k1: float = 1.5,
    b: float = 0.75,
    rrf_k: int = 60,
) -> tuple[list[RetrievedDoc], list[RetrievedDoc], list[RRFItem]]:
    """
    真正的多路召回：先从 Milvus 全量 BM25 召回，再与 Dense 融合。
    返回:
    - bm25_docs: BM25 召回的文档列表（独立召回，可能包含 dense 中没有的文档）
    - fused_docs: RRF 融合后的 docs 列表（按 RRF 分数排序）
    - rrf_items: RRF 阶段的展示数据（含 rrf_score, dense_rank, bm25_rank）
    """
    import asyncio
    import hashlib

    if not dense_docs:
        return [], [], []

    async def _run():
        from app.services.bm25_retriever import BM25Retriever

        bm25_retriever = BM25Retriever(milvus_client=milvus_client, k1=k1, b=b)
        bm25_results = await bm25_retriever.retrieve(query, top_k=20)

        # 转换为 RetrievedDoc 格式
        bm25_docs = [
            RetrievedDoc(
                content=doc.content,
                metadata=doc.metadata,
                score=doc.score,
                source=doc.metadata.get("source", ""),
            )
            for doc in bm25_results
        ]
        return bm25_docs

    # 在事件循环中执行异步 BM25 检索
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, _run())
                bm25_docs = future.result(timeout=30)
        else:
            bm25_docs = loop.run_until_complete(_run())
    except Exception:
        bm25_docs = []

    # 建立 content 哈希映射
    def make_key(doc: RetrievedDoc) -> str:
        content = doc.content.strip()
        key_content = content[:200] if content else ""
        return hashlib.md5(key_content.encode()).hexdigest()

    # Dense 映射
    dense_map: dict[str, tuple[float, int]] = {}
    for rank, doc in enumerate(dense_docs):
        key = make_key(doc)
        dense_map[key] = (doc.score, rank)

    # BM25 映射
    bm25_map: dict[str, tuple[float, int]] = {}
    for rank, doc in enumerate(bm25_docs):
        key = make_key(doc)
        bm25_map[key] = (doc.score, rank)

    # 计算 RRF
    all_keys = set(dense_map.keys()) | set(bm25_map.keys())
    rrf_scores: dict[str, float] = {}
    for key in all_keys:
        dense_rank = dense_map[key][1] if key in dense_map else len(dense_docs)
        bm25_rank = bm25_map[key][1] if key in bm25_map else len(bm25_docs)
        rrf_scores[key] = 1.0 / (rrf_k + dense_rank) + 1.0 / (rrf_k + bm25_rank)

    sorted_keys = sorted(rrf_scores.keys(), key=lambda k: rrf_scores[k], reverse=True)

    # 构建 RRF items
    rrf_items: list[RRFItem] = []
    fused_docs: list[RetrievedDoc] = []
    bm25_scores_aligned: list[float] = [0.0] * len(dense_docs)

    for key in sorted_keys[:20]:
        # 找到对应文档
        doc = None
        for d in dense_docs:
            if make_key(d) == key:
                doc = d
                break
        if doc is None:
            for d in bm25_docs:
                if make_key(d) == key:
                    doc = d
                    break

        if doc is None:
            continue

        dense_rank = dense_map.get(key, (0.0, len(dense_docs)))[1]
        bm25_rank = bm25_map.get(key, (0.0, len(bm25_docs)))[1]
        bm25_score_val = bm25_map.get(key, (0.0, 0))[0]

        # 更新 dense_docs 中对应项的 metadata
        for i, d in enumerate(dense_docs):
            if make_key(d) == key:
                d.metadata["bm25_score"] = bm25_score_val
                d.metadata["dense_rank"] = dense_rank
                d.metadata["bm25_rank"] = bm25_rank
                bm25_scores_aligned[i] = bm25_score_val
                break

        meta = doc.metadata
        rrf_items.append(
            RRFItem(
                content=doc.content,
                rrf_score=rrf_scores[key],
                dense_rank=dense_rank,
                bm25_rank=bm25_rank,
                section_type=meta.get("section_type", ""),
                markets=meta.get("markets", []) if isinstance(meta.get("markets"), list) else [],
                lang=meta.get("lang", ""),
                source=doc.source,
            )
        )
        fused_docs.append(doc)

    return bm25_docs, fused_docs, rrf_items


# ---------------------------------------------------------------------------
# Endpoint: POST /api/v1/debug/retrieve
# ---------------------------------------------------------------------------
@router.post("/retrieve", response_model=RetrieveResponse)
async def debug_retrieve(request: RetrieveRequest):
    """
    分阶段检索调试接口。

    展示从向量召回 → BM25 → RRF 融合 → MMR 重排 → LLM 生成的全流程，
    每个阶段返回独立的结果和耗时。
    """
    overall_start = time.monotonic()

    # 获取依赖（避免循环导入：import 在函数内部执行）
    from app.main import app as _app

    milvus_client = _app.state.milvus_client
    if milvus_client is None:
        return RetrieveResponse(
            params=request.model_dump(),
            stages=Stages(),
            total_latency_ms=0.0,
        )

    config = get_config()

    # 构建过滤器（Qdrant 兼容格式）
    filter_conditions = []
    if request.lang_filter:
        for lang in request.lang_filter:
            filter_conditions.append({"key": "lang", "match": {"value": lang}})
    if request.section_type_filter:
        for st in request.section_type_filter:
            filter_conditions.append({"key": "section_type", "match": {"value": st}})
    filters = {"must": filter_conditions} if filter_conditions else None

    # 初始化 retrievers
    simple_retriever = SimpleVectorRetriever(milvus_client)

    stages: Stages = Stages()
    dense_docs: list[RetrievedDoc] = []
    rerank_candidates: list[RetrievedDoc] = []

    # ----- 阶段1：Dense 召回（纯向量） -----
    dense_stage: StageDense | None = None
    if request.hybrid_search:
        # hybrid=True 时，单独调用 SimpleVectorRetriever 获取未融合的 dense 结果
        try:
            t0 = time.monotonic()
            dense_docs = await simple_retriever.retrieve(
                request.query, top_k=request.top_k * 2, filters=filters
            )
            dense_ms = (time.monotonic() - t0) * 1000

            dense_items = []
            for idx, doc in enumerate(dense_docs):
                meta = doc.metadata.copy() if doc.metadata else {}
                hierarchy_val = meta.get("hierarchy", {})
                if isinstance(hierarchy_val, dict):
                    hierarchy_str = " > ".join(
                        str(v) for v in hierarchy_val.values() if v and str(v) != "_flat_index"
                    )
                else:
                    hierarchy_str = str(hierarchy_val) if hierarchy_val else ""

                dense_items.append(
                    DenseItem(
                        content=doc.content,
                        dense_score=doc.score,
                        section_type=meta.get("section_type", ""),
                        markets=meta.get("markets", []) if isinstance(meta.get("markets"), list) else [],
                        lang=meta.get("lang", ""),
                        hierarchy=hierarchy_str,
                        source=doc.source,
                    )
                )
                # 注入 dense_rank 到 metadata（使用索引避免对不可哈希对象排序）
                meta["dense_rank"] = idx

            dense_stage = StageDense(count=len(dense_items), items=dense_items, duration_ms=dense_ms)
        except Exception as e:
            _logger.warning(f"Dense stage failed: {e}")
            dense_stage = StageDense(count=0, items=[], duration_ms=0.0)
    else:
        # hybrid=False 时，SimpleVectorRetriever 结果直接作为 rerank 输入
        try:
            t0 = time.monotonic()
            dense_docs = await simple_retriever.retrieve(
                request.query, top_k=request.top_k, filters=filters
            )
            dense_ms = (time.monotonic() - t0) * 1000

            dense_items = []
            for doc in dense_docs:
                meta = doc.metadata
                hierarchy_val = meta.get("hierarchy", {})
                if isinstance(hierarchy_val, dict):
                    hierarchy_str = " > ".join(
                        str(v) for v in hierarchy_val.values() if v and str(v) != "_flat_index"
                    )
                else:
                    hierarchy_str = str(hierarchy_val) if hierarchy_val else ""

                dense_items.append(
                    DenseItem(
                        content=doc.content,
                        dense_score=doc.score,
                        section_type=meta.get("section_type", ""),
                        markets=meta.get("markets", []) if isinstance(meta.get("markets"), list) else [],
                        lang=meta.get("lang", ""),
                        hierarchy=hierarchy_str,
                        source=doc.source,
                    )
                )
            dense_stage = StageDense(count=len(dense_items), items=dense_items, duration_ms=dense_ms)
        except Exception as e:
            _logger.warning(f"Dense stage failed: {e}")
            dense_stage = StageDense(count=0, items=[], duration_ms=0.0)

    stages.dense = dense_stage

    # ----- 阶段2+3：BM25 + RRF（仅 hybrid_search=True） -----
    bm25_stage: StageBM25 | None = None
    rrf_stage: StageRRF | None = None

    if request.hybrid_search and dense_docs:
        try:
            # 真正的多路召回：BM25 从全量文档检索 + RRF 融合
            bm25_docs, fused_docs, rrf_items = _compute_bm25_and_rrf(
                request.query, dense_docs, milvus_client,
                k1=1.5, b=0.75, rrf_k=60,
            )

            # BM25 stage - 显示 BM25 召回的独立结果
            bm25_items = []
            for idx, doc in enumerate(bm25_docs):
                bm25_raw = doc.score
                bm25_norm = math.log1p(bm25_raw) / 10.0
                bm25_items.append(
                    BM25Item(
                        content=doc.content,
                        bm25_raw=bm25_raw,
                        bm25_norm=bm25_norm,
                        source=doc.source,
                    )
                )
            bm25_stage = StageBM25(count=len(bm25_items), items=bm25_items, duration_ms=0.0)
            stages.bm25 = bm25_stage

            # RRF stage
            rrf_stage = StageRRF(count=len(rrf_items), items=rrf_items, duration_ms=0.0)
            stages.rrf = rrf_stage

            # RRF 结果作为 rerank 的候选（hybrid_search=True）
            rerank_candidates = fused_docs
        except Exception as e:
            _logger.warning(f"BM25/RRF stage failed: {e}")
            bm25_stage = StageBM25(count=0, items=[], duration_ms=0.0)
            rrf_stage = StageRRF(count=0, items=[], duration_ms=0.0)
            stages.bm25 = bm25_stage
            stages.rrf = rrf_stage
            rerank_candidates = dense_docs
    else:
        # hybrid=False 时，dense 结果直接作为 rerank 候选
        rerank_candidates = dense_docs

    # ----- 阶段4：MMR 重排 -----
    rerank_stage: StageRerank | None = None
    if rerank_candidates:
        try:
            # 获取 LLM
            llm = None
            try:
                from app.services.llm_service import LLMFactory
                llm = LLMFactory.create(config.llm.provider, config.llm)
            except Exception:
                pass

            reranker = MMRReranker(
                llm=llm,
                use_llm_rerank=request.use_llm_rerank,
                bm25_weight=request.bm25_weight,
            )

            t0 = time.monotonic()
            reranked = await reranker.rerank(
                request.query,
                rerank_candidates,
                top_k=request.rerank_top_k,
                intent=request.intent,
            )
            rerank_ms = (time.monotonic() - t0) * 1000

            rerank_items = []
            for doc in reranked:
                meta = doc.metadata
                content_lower = doc.content.lower()
                mmr_reason = _infer_mmr_reason(doc, content_lower)

                rerank_items.append(
                    RerankedItem(
                        content=doc.content,
                        rerank_score=doc.rerank_score,
                        mmr_reason=mmr_reason,
                        section_type=meta.get("section_type", ""),
                        markets=meta.get("markets", []) if isinstance(meta.get("markets"), list) else [],
                        lang=meta.get("lang", ""),
                        source=doc.source,
                    )
                )

            method = "rule+llm" if request.use_llm_rerank and llm else "rule"
            rerank_stage = StageRerank(
                count=len(rerank_items),
                items=rerank_items,
                duration_ms=rerank_ms,
                method=method,
            )
        except Exception as e:
            _logger.warning(f"Rerank stage failed: {e}")
            rerank_stage = StageRerank(count=0, items=[], duration_ms=0.0, method="rule")
            reranked = []
    else:
        rerank_stage = StageRerank(count=0, items=[], duration_ms=0.0, method="rule")
        reranked = []

    stages.rerank = rerank_stage

    # ----- 阶段5：LLM 生成（仅 run_llm=True） -----
    llm_stage: LLMStage | None = None
    if request.run_llm and reranked:
        try:
            from app.services.llm_service import LLMFactory
            llm = LLMFactory.create(config.llm.provider, config.llm)
            generator = StandardLLMGenerator(llm)

            # 构建 prompt
            context_parts = []
            for i, doc in enumerate(reranked, 1):
                src = doc.source or "未知来源"
                context_parts.append(f"[{i}] 来源: {src}\n{doc.content}")
            context_text = "\n\n".join(context_parts)

            system_prompt = (
                "你是一位专业的电商商品顾问，请根据提供的商品知识库内容，准确回答用户关于商品的问题。\n"
                "回答规范：\n"
                "- 只基于知识库内容回答，不要编造信息\n"
                "- 如知识库中没有相关信息，请明确告知用户\n"
                "- 回答时标注信息来源，例如【商品详情】【FAQ】【退换货政策】\n"
                "- 回答要专业、友好、简洁"
            )
            user_prompt = f"【用户问题】\n{request.query}\n\n【相关知识】\n{context_text}\n\n请根据以上信息回答用户问题。"

            prompt = Prompt(
                system=system_prompt,
                user=user_prompt,
                system_tokens=_estimate_tokens(system_prompt),
                user_tokens=_estimate_tokens(user_prompt),
            )

            t0 = time.monotonic()
            gen_result = await generator.generate(prompt, session_id="debug-session")
            llm_ms = (time.monotonic() - t0) * 1000

            prompt_preview = (
                f"System: {system_prompt[:100]}...\n"
                f"User: {user_prompt[:200]}..."
                if len(user_prompt) > 200
                else f"System: {system_prompt[:100]}...\nUser: {user_prompt}"
            )

            llm_stage = LLMStage(
                message=gen_result.content,
                intent=request.intent,
                latency_ms=llm_ms,
                tokens_used=gen_result.tokens_used,
                prompt_preview=prompt_preview,
            )
        except Exception as e:
            _logger.warning(f"LLM stage failed: {e}")
            llm_stage = LLMStage(
                message=f"[LLM generation failed: {e}]",
                intent=request.intent,
                latency_ms=0.0,
                tokens_used=0,
                prompt_preview="",
            )

    stages.llm = llm_stage

    total_ms = (time.monotonic() - overall_start) * 1000

    return RetrieveResponse(
        params=request.model_dump(),
        stages=stages,
        total_latency_ms=total_ms,
    )


# ---------------------------------------------------------------------------
# Endpoint: GET /api/v1/debug/embed
# ---------------------------------------------------------------------------
@router.get("/embed", response_model=EmbedResponse)
async def debug_embed(text: str = Query(..., description="待测试文本")):
    """
    Embedding 测试接口。
    返回文本的 token 估算数、向量维度、前 5 个维度预览和延迟。
    """
    from app.services.embedding_service import EmbeddingService

    start = time.monotonic()
    embed_service = EmbeddingService.get_instance()

    try:
        vectors = await embed_service.embed([text])
        vector = vectors[0]
    except Exception as e:
        _logger.warning(f"Embedding failed: {e}")
        vector = []

    latency_ms = (time.monotonic() - start) * 1000
    token_count = _estimate_tokens(text)
    vector_dim = len(vector)
    vector_preview = vector[:5] if vector else []

    return EmbedResponse(
        text=text,
        token_count=token_count,
        vector_dim=vector_dim,
        vector_preview=vector_preview,
        latency_ms=latency_ms,
    )


# ---------------------------------------------------------------------------
# Endpoint: POST /api/v1/debug/auth/token
# ---------------------------------------------------------------------------
class TokenRequest(BaseModel):
    user_id: str = Field(..., description="用户ID")
    role: str = Field(default="user", description="用户角色")


class TokenResponse(BaseModel):
    token: str
    expires_in: int = 86400


@router.post("/auth/token", response_model=TokenResponse)
async def create_test_token(request: TokenRequest):
    """
    生成测试用 JWT Token（仅用于测试环境）

    用于前端 WebSocket 连接认证。
    生产环境应使用真实的登录流程。
    """
    import os

    # 仅在测试环境允许此接口
    env = os.getenv("APP_ENV", "development")
    if env == "production":
        return {"error": "Not available in production"}, 403

    token = create_token(
        user_id=request.user_id,
        role=request.role,
        ttl_seconds=86400  # 24小时
    )
    return TokenResponse(token=token)
