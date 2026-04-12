---
name: rag-orchestrator-dev
description: RAG Orchestrator 开发工作流和调试指南。用于开发或调试 RAG 系统时，包括：新增子模块、Query 改写策略调整、重排模块切换、Prompt 模板迭代、检索质量诊断、性能分析。涉及 query_rewriter / vector_retriever / reranker / prompt_builder / llm_generator / rag_orchestrator。
---

# RAG Orchestrator 开发工作流

## 子模块快速索引

| 模块 | 文件 | 核心方法 | 配置项 |
|------|------|---------|--------|
| Orchestrator | `rag_orchestrator.py` | `run()`, `run_stream()` | `RAGConfig` |
| QueryRewriter | `query_rewriter.py` | `rewrite(query, history)` | `rewrite_enabled`, `expand_synonyms` |
| VectorRetriever | `vector_retriever.py` | `retrieve(query, top_k, filters)` | `top_k`, `similarity_threshold` |
| Reranker | `reranker.py` | `rerank(query, candidates, top_k)` | `rerank_top_k`, `use_mmr` |
| PromptBuilder | `prompt_builder.py` | `build(query, context, intent, history)` | `max_context_tokens`, `system_prompt_templates` |
| LLMGenerator | `llm_generator.py` | `generate(prompt)`, `stream(prompt)` | `temperature`, `max_tokens` |

## 开发流程

### 新增 Query 改写策略

1. 在 `services/query_rewriter.py` 中新建策略类：

```python
class HyDEQueryRewriter(QueryRewriter):
    """HyDE: 先生成假设性答案，再用假设答案检索"""

    async def rewrite(self, query: str, history: list[dict]) -> RewrittenQuery:
        # Step 1: LLM 生成假设性答案
        hypothetical = await self.llm.chat([
            ChatMessage(role="user", content=f"请用一句话回答: {query}")
        ])
        # Step 2: 用原 query + 假设答案共同检索
        rewritten = f"{query} {hypothetical.content}"
        return RewrittenQuery(rewritten=rewritten, expanded_terms=[], intent="")
```

2. 在 `RAGConfig` 中添加选项：

```python
@dataclass
class RAGConfig:
    rewrite_strategy: str = "basic"  # "basic" | "hyde" | "conversational"
```

3. 在 `RAGOrchestrator` 中根据配置注入不同实现：

```python
if config.rewrite_strategy == "hyde":
    rewriter = HyDEQueryRewriter(llm=llm)
else:
    rewriter = BasicQueryRewriter()
```

### 新增重排模型

在 `services/reranker.py` 中实现新策略：

```python
class BGEReranker(Reranker):
    """使用 BGE-Reranker 模型（效果优于 BM25）"""

    def __init__(self, model_name: str = "BAAI/bge-reranker-base"):
        self.reranker_model = AutoModelForSequenceClassification.from_pretrained(model_name)

    async def rerank(self, query: str, candidates: list[RetrievedDoc], top_k: int) -> list[RerankedDoc]:
        pairs = [[query, c.content] for c in candidates]
        scores = self.reranker_model.predict(pairs)
        sorted_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [candidates[i] for i in sorted_idx[:top_k]]
```

注入方式（不改 `rag_orchestrator.py` 的 `run()` 逻辑）：

```python
orchestrator = RAGOrchestrator(
    ...,
    reranker=BGEReranker(),  # 替换默认实现
    ...
)
```

## Prompt 模板迭代

Prompt 模板存放在 `utils/prompt_template.py`，按意图分类：

```python
SYSTEM_TEMPLATES = {
    "product_qa": """你是电商平台的智能客服，专注于回答商品相关问题。
根据以下商品信息回答用户问题。如果信息不足，坦诚告知用户。
---
{context}
---
注意：回答时引用信息来源。""",

    "order_query": """你是一个订单查询助手。根据以下订单信息回答用户问题。
---
{context}
---
如果用户询问物流，请给出预计送达时间范围。""",

    "refund_return": """你是退换货政策助手。根据以下政策信息回答用户问题。
---
{context}
---
请清晰告知用户申请流程和所需材料。""",

    "general_chat": """你是一个友好的电商客服。请简洁回答用户问题。
回答控制在 3 句话以内。""",
}
```

**迭代流程**：
1. 修改模板字符串
2. 启动服务，发送测试 query
3. 验证回复质量
4. 如需 A/B 测试，可用 feature flag 切换模板版本

## 检索质量诊断

当 RAG 召回质量差时，按以下顺序排查：

### Step 1: 检查 Embedding 质量

```python
# 在 embedding_service.py 中添加调试代码
query_emb = embedding_service.embed("查询手机价格")
top_docs = milvus_client.search(query_emb, top_k=5)
for doc in top_docs:
    print(f"Score: {doc.score:.4f} | Content: {doc.content[:100]}")
```

### Step 2: 检查 Query 改写是否生效

```python
rewritten = await rewriter.rewrite("这个手机多少钱", history=[])
print(f"Rewritten: {rewritten.rewritten}")
print(f"Expanded: {rewritten.expanded_terms}")
```

### Step 3: 检查向量检索召回

| 分数区间 | 判断 | 处理方式 |
|---------|------|---------|
| > 0.85 | 高度相关 | 正常 |
| 0.6 - 0.85 | 中度相关 | 检查 top_k 是否足够 |
| < 0.6 | 不相关 | 检查 Embedding 模型是否匹配业务场景 |

### Step 4: 检查重排是否引入噪声

对比重排前后的 `content` 长度和多样性，如果高分结果高度重复，启用 MMR：

```python
config.use_mmr = True
config.mmr_diversity_factor = 0.3  # 越高越追求多样性
```

## 性能分析

每个子模块的耗时分析（可通过 `RAGOrchestrator` 的日志查看）：

```
[orchestrator] total: 1234ms
  ├── query_rewrite: 45ms
  ├── retrieval: 234ms
  │   ├── embed_query: 56ms
  │   └── milvus_search: 178ms
  ├── rerank: 123ms
  ├── prompt_build: 2ms
  └── llm_generate: 830ms
```

**常见瓶颈**：
- 检索 > 500ms：检查 Milvus 索引类型（HNSW > IVF_FLAT > FLAT）
- 重排 > 500ms：减少 rerank_top_k（从 50 降至 20）
- LLM > 10s：检查模型响应时间，或启用流式输出

## 流式输出集成

在 `rag_orchestrator.py` 中：

```python
async def run_stream(self, query, session_id, user_id):
    # 前处理（非流式）
    rewritten = await self.rewriter.rewrite(query, history)
    candidates = await self.retriever.retrieve(...)
    reranked = await self.reranker.rerank(...)
    prompt = self.prompt_builder.build(...)

    # 流式 LLM（直接透传）
    async for token in self.generator.stream(prompt, session_id):
        yield token
```

前端通过 SSE 消费：

```javascript
const eventSource = new EventSource(`/api/v1/chat/stream?q=${query}`);
eventSource.onmessage = (e) => {
    // e.data = 逐字 token
    appendMessage(e.data);
};
```
