"""
Debug API 路由
POST /api/v1/debug/retrieve — 分阶段检索调试
GET  /api/v1/debug/embed   — Embedding 测试
"""
from __future__ import annotations

import math
import time
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.logging_config import get_logger
from app.config import get_config
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
    k1: float = 1.5,
    b: float = 0.75,
    rrf_k: int = 60,
) -> tuple[list[float], list[RetrievedDoc], list[RRFItem]]:
    """
    在 dense_docs 上计算 BM25 + RRF，返回:
    - bm25_scores: 原始 BM25 分数列表
    - fused_docs: RRF 融合后的 docs 列表
    - rrf_items: RRF 阶段的展示数据（含 rrf_score, dense_rank, bm25_rank）
    """
    if not dense_docs:
        return [], [], []

    # BM25 scoring（与 HybridRetriever 逻辑一致）
    try:
        from app.services.reranker import get_tokenizer
        tokenizer = get_tokenizer()
    except Exception:
        return [], dense_docs, []

    query_terms = tokenizer.tokenize(query)
    if not query_terms:
        bm25_scores = [0.0] * len(dense_docs)
    else:
        doc_count = len(dense_docs)
        word_doc_freq: dict[str, int] = {}
        docs_bow: list[dict[str, int]] = []
        for doc_text in [d.content for d in dense_docs]:
            bow: dict[str, int] = {}
            for word in tokenizer.tokenize(doc_text):
                bow[word] = bow.get(word, 0) + 1
                word_doc_freq[word] = word_doc_freq.get(word, 0) + 1
            docs_bow.append(bow)

        avgdl = sum(len(bow) for bow in docs_bow) / max(doc_count, 1)
        bm25_scores = []
        for bow in docs_bow:
            score = 0.0
            doc_len = len(bow)
            for term in query_terms:
                if term in bow:
                    df = word_doc_freq.get(term, 0)
                    idf = max(0, (doc_count - df + 0.5) / (df + 0.5))
                    tf = bow[term]
                    score += idf * (tf * (k1 + 1)) / (
                        tf + k1 * (1 - b + b * doc_len / max(avgdl, 1))
                    )
            bm25_scores.append(score)

    # RRF 融合
    dense_ranked = sorted(range(len(dense_docs)), key=lambda i: dense_docs[i].score, reverse=True)
    bm25_ranked = sorted(range(len(bm25_scores)), key=lambda i: bm25_scores[i], reverse=True)

    rrf: dict[int, float] = {}
    for rank, idx in enumerate(dense_ranked):
        rrf[idx] = rrf.get(idx, 0) + 1.0 / (rrf_k + rank)
    for rank, idx in enumerate(bm25_ranked):
        rrf[idx] = rrf.get(idx, 0) + 1.0 / (rrf_k + rank)

    # 构建 rank 映射
    dense_rank_map = {idx: rank for rank, idx in enumerate(dense_ranked)}
    bm25_rank_map = {idx: rank for rank, idx in enumerate(bm25_ranked)}

    fused_indices = sorted(rrf.keys(), key=lambda i: rrf[i], reverse=True)
    fused_docs = [dense_docs[i] for i in fused_indices]

    # 构建 RRF items（含 rank 信息）
    rrf_items: list[RRFItem] = []
    for idx in fused_indices:
        doc = dense_docs[idx]
        meta = doc.metadata
        # 注入 bm25_score 到 metadata（供 reranker 使用）
        doc.metadata["bm25_score"] = bm25_scores[idx]
        doc.metadata["dense_rank"] = dense_rank_map[idx]
        doc.metadata["bm25_rank"] = bm25_rank_map[idx]

        rrf_items.append(
            RRFItem(
                content=doc.content,
                rrf_score=rrf[idx],
                dense_rank=dense_rank_map[idx],
                bm25_rank=bm25_rank_map[idx],
                section_type=meta.get("section_type", ""),
                markets=meta.get("markets", []) if isinstance(meta.get("markets"), list) else [],
                lang=meta.get("lang", ""),
                source=doc.source,
            )
        )

    return bm25_scores, fused_docs, rrf_items


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

    # 构建过滤器（兼容两种过滤方式）
    filters = None
    filter_exprs: list[str] = []
    if request.lang_filter:
        lang_parts = [f'(lang like "%{lang}%")' for lang in request.lang_filter]
        filter_exprs.append("(" + " or ".join(lang_parts) + ")")
    if request.section_type_filter:
        st_parts = [f'(section_type like "%{st}%")' for st in request.section_type_filter]
        filter_exprs.append("(" + " or ".join(st_parts) + ")")
    if filter_exprs:
        filters = {"expr": " and ".join(filter_exprs)}

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

            # 注入 dense_rank
            sorted_dense = sorted(enumerate(dense_docs), key=lambda x: x[1].score, reverse=True)
            dense_rank_map = {idx: rank for rank, idx in sorted_dense}

            dense_items = []
            for idx, doc in enumerate(dense_docs):
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
                # 注入 dense_rank 到 metadata
                doc.metadata["dense_rank"] = dense_rank_map[idx]

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
            # 同时计算 BM25 和 RRF（复用 dense_docs）
            bm25_scores, fused_docs, rrf_items = _compute_bm25_and_rrf(
                request.query, dense_docs,
                k1=1.5, b=0.75, rrf_k=60,
            )

            # BM25 stage
            bm25_items = []
            for idx, doc in enumerate(dense_docs):
                bm25_raw = bm25_scores[idx]
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
