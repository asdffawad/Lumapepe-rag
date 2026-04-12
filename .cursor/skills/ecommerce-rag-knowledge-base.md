---
name: ecommerce-rag-knowledge-base
description: 电商知识库构建和维护指南。用于设计和构建商品知识库、文档切块策略、Embedding 索引设计、FAQ 质量标准、知识库更新流程、效果评估指标。涉及 products.json、knowledge_base.json、Celery 任务链、Milvus collection 配置。
---

# 电商知识库构建指南

## 文档类型定义

| 类型 | 内容来源 | 切块策略 | Embedding 策略 |
|------|---------|---------|--------------|
| product | 商品标题/描述/规格参数 | 按规格项切块，50-100 tokens | 混合文本（标题+描述+规格） |
| faq | 常见问题/答案对 | QA 对保持完整，200-500 tokens | 问题单独索引，答案作为 content |
| policy | 退换货/配送/售后政策 | 按条款切块，300-800 tokens | 政策条款单独索引 |
| manual | 商品使用手册/说明书 | 按章节切块，500-1000 tokens | 章节标题+正文混合 |

## 商品数据模型

```json
// data/products.json
{
  "products": [
    {
      "id": "P001",
      "name": "iPhone 15 Pro 256GB 深空黑",
      "category": "手机/智能手机",
      "brand": "Apple",
      "price": 8999,
      "description": "A17 Pro 芯片，钛金属设计，4800万像素摄像头...",
      "specs": {
        "屏幕": "6.1英寸 OLED",
        "芯片": "A17 Pro",
        "内存": "8GB",
        "存储": "256GB",
        "电池": "3274mAh",
        "重量": "187g"
      },
      "faq": [
        {"q": "支持5G吗", "a": "支持全频段5G网络"},
        {"q": "有防水吗", "a": "支持IP68级防水防尘"}
      ]
    }
  ]
}
```

## 切块策略

### RecursiveCharacterTextSplitter

推荐用于商品描述和政策文档：

```python
from langchain.text_splitter import RecursiveCharacterTextSplitter

splitter = RecursiveCharacterTextSplitter(
    chunk_size=512,          # 目标 tokens（含余量）
    chunk_overlap=50,        # 重叠 tokens（保持上下文连续性）
    separators=["\n\n", "\n", "。", "！", "？", " ", ""],
    length_function=lambda text: count_tokens(text),  # 用实际 tokenizer
)

# 商品描述切块
chunks = splitter.split_text(product["description"])
# 规格参数：每个 key-value 对作为独立 chunk
spec_chunks = [f"{k}: {v}" for k, v in product["specs"].items()]
```

### FAQ 保持完整

FAQ 不得切分，保持问答对完整：

```python
def chunk_faqs(faq_list: list[dict]) -> list[dict]:
    """FAQ 按问答对切块，不做进一步拆分"""
    chunks = []
    for i, faq in enumerate(faq_list):
        chunks.append({
            "content": f"问题: {faq['q']}\n答案: {faq['a']}",
            "chunk_index": i,
            "type": "faq",
            "question": faq["q"],
            "answer": faq["a"],
        })
    return chunks
```

### Chunk 大小选择决策树

```
用户 query 平均长度?
  ├── < 20 tokens (短查询)  -> chunk_size = 256-384 (集中匹配)
  ├── 20-50 tokens (中等)   -> chunk_size = 384-512 (平衡)
  └── > 50 tokens (长查询)  -> chunk_size = 512-768 (包含更多语义)
```

## Embedding 策略

### 多字段混合索引

```python
def build_product_text(product: dict) -> str:
    """构建用于 embedding 的混合文本"""
    parts = [
        product["name"],                    # 标题（权重最高）
        product["description"][:300],        # 描述前300字
        " ".join([f"{k}: {v}" for k, v in product["specs"].items()]),  # 规格
        product["category"],                 # 类目
    ]
    return " | ".join(parts)

# 向量化
embedding = embedding_service.embed(build_product_text(product))
```

### FAQ 独立索引

```python
# FAQ 用问题单独 embedding（用于精准匹配用户问法）
faq_question_emb = embedding_service.embed(faq["q"])
# FAQ 内容（答案）作为 metadata 存储，不做 embedding
```

### Milvus Collection 配置

```python
# scripts/init_milvus.py
from pymilvus import Collection, CollectionSchema, Field, DataType

def create_collections():
    # 商品向量库
    create_collection(
        name="products",
        dimension=1536,  # 通义 embedding 维度
        description="商品知识库",
        schema_fields=[
            Field(name="id", dtype=DataType.VARCHAR, max_length=64, is_primary=True),
            Field(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=1536),
            Field(name="product_id", dtype=DataType.VARCHAR, max_length=64),
            Field(name="content", dtype=DataType.VARCHAR, max_length=4096),
            Field(name="content_type", dtype=DataType.VARCHAR, max_length=32),
            Field(name="category", dtype=DataType.VARCHAR, max_length=64),
        ],
        index_params={
            "metric_type": "IP",      # 内积相似度
            "index_type": "HNSW",     # 高性能 ANN 索引
            "params": {"M": 16, "efConstruction": 256}
        }
    )

    # FAQ 向量库
    create_collection(
        name="faqs",
        dimension=1536,
        description="FAQ 知识库",
        schema_fields=[
            Field(name="id", dtype=DataType.VARCHAR, max_length=64, is_primary=True),
            Field(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=1536),
            Field(name="question", dtype=DataType.VARCHAR, max_length=512),
            Field(name="answer", dtype=DataType.VARCHAR, max_length=2048),
            Field(name="category", dtype=DataType.VARCHAR, max_length=64),
        ],
        index_params={
            "metric_type": "IP",
            "index_type": "HNSW",
            "params": {"M": 16, "efConstruction": 256}
        }
    )
```

## FAQ 设计规范

好 FAQ 的 5 个标准：

1. **具体**: 避免泛泛而问
   - 好: "iPhone 15 支持双卡双待吗"
   - 差: "手机功能有哪些"

2. **完整**: 答案包含所有必要信息
   - 好: "支持全网5G，双卡（nano-SIM + eSIM），不支持双电信卡同时在线"
   - 差: "支持双卡"

3. **一致**: 同一问题不同商品的答案格式统一

4. **可执行**: 如果是操作类问题，答案包含明确步骤
   - 好: "退货运费险申请步骤：1. 打开订单详情页 2. 点击申请退款 3. 选择'已收到货' 4. 填写物流单号"

5. **分类清晰**: FAQ 按主题分组
   - 物流类 / 支付类 / 售后类 / 商品类 / 优惠类

## 知识库更新流程

```
新商品上架/文档上传
    │
    ▼
API: POST /admin/documents/upload
    │
    ├── 保存文件到存储（S3/本地）
    ├── 创建 documents 记录（status=pending）
    └── 返回 task_id
    │
    ▼
Celery Task Chain:
    │
    ├── doc_parser.parse_document  (解析 PDF/Word/HTML/JSON)
    │
    ├── chunker.chunk_text        (文本切块)
    │
    ├── embed_task.embed_batch     (向量化)
    │
    └── vector_index.index_batch   (Milvus 入库 + MySQL 状态更新)
    │
    ▼
WebSocket/SSE 通知前端:
    {"event": "document_indexed", "doc_id": 123}
```

## 效果评估

### 召回率（Recall@K）

```python
def recall_at_k(retrieved_ids: list[str], relevant_ids: list[str], k: int) -> float:
    retrieved_k = set(retrieved_ids[:k])
    relevant = set(relevant_ids)
    return len(retrieved_k & relevant) / len(relevant)

# 评估集
# 测试集格式: {query, relevant_product_ids}
test_set = load_test_set("data/eval/test_set.json")
recall_scores = []
for item in test_set:
    retrieved = retriever.retrieve(item["query"], top_k=5)
    recall = recall_at_k([d["id"] for d in retrieved], item["relevant_ids"], k=5)
    recall_scores.append(recall)

print(f"Recall@5: {sum(recall_scores)/len(recall_scores):.4f}")
```

### MRR (Mean Reciprocal Rank)

```python
def mean_reciprocal_rank(retrieved_lists: list[list[str]], relevant_lists: list[list[str]]) -> float:
    rr = []
    for retrieved, relevant in zip(retrieved_lists, relevant_lists):
        for i, doc_id in enumerate(retrieved):
            if doc_id in relevant:
                rr.append(1 / (i + 1))
                break
        else:
            rr.append(0)
    return sum(rr) / len(rr)
```

### 生成质量人工评估维度

| 维度 | 评估标准 | 打分 1-5 |
|------|---------|---------|
| 准确性 | 回复内容是否准确 | |
| 相关性 | 回复是否切中用户问题 | |
| 完整性 | 是否回答了问题的所有方面 | |
| 引用准确性 | 引用的来源是否正确 | |
| 语言流畅 | 回复是否通顺自然 | |

## 常见问题

**Q: 商品数量达到百万级时如何优化？**
A: (1) 使用 Milvus 分区按类目隔离 (2) 启用 GPU 加速 HNSW 检索 (3) 用 product_id 做预过滤减少搜索范围 (4) 考虑量化（PQ）压缩向量降低内存

**Q: 如何处理同义词导致的召回率低？**
A: 在 QueryRewriter 中添加电商同义词词典，例如：手机->智能手机->移动电话->大哥大(老旧说法)，并在检索前进行 query 改写。

**Q: FAQ 和商品描述检索如何避免互相干扰？**
A: 在 VectorRetriever 中按类型分组检索，先判断意图类型，再在对应 collection 中检索，用不同 top_k 权重组合结果。
