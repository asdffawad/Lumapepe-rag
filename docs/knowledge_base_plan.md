# 知识库管理增强计划

> 本文档记录当前 RAG 电商客服系统知识库管理模块的已实现功能、待新增功能及优先级定义。

## 目录

1. [当前已实现](#当前已实现)
2. [高优先级功能](#高优先级功能)
3. [中优先级功能](#中优先级功能)
4. [低优先级功能](#低优先级功能)
5. [实现记录](#实现记录)

---

## 当前已实现

| 功能 | 说明 | 代码位置 |
|------|------|---------|
| JSON 数据摄取 | 读取 products.json / knowledge_base.json / orders.json | `scripts/ingest_data.py` |
| 段落分块 | 按换行符切分段落，超长文本滑动窗口 | `app/utils/chunker.py` |
| 结构化分块 | JSON 字段提取拼接后再分块 | `app/utils/chunker.py` |
| 向量化 | DashScope text-embedding-v3 + Mock 兜底 | `app/services/embedding_service.py` |
| 批量摄取 | 分批向量化 + Milvus 批量入库 | `scripts/ingest_data.py` |
| 增量摄取 | SHA256 hash 去重，已索引文档跳过 | `scripts/ingest_data.py` |
| 强制重建 | `--reset` 删除 collection 重新摄取 | `scripts/ingest_data.py` |
| 元数据持久化 | MySQL documents 表记录文档状态 | `app/database/document.py` |
| Milvus 存储 | IVF_FLAT 索引，向量 + metadata | `app/vectorstore/milvus_client.py` |
| 管理 API | `POST /admin/ingest` / `GET /admin/documents` | `app/api/admin.py` |
| 同义词扩展 | 7 大类电商同义词映射 | `app/services/query_rewriter.py` |
| 指代消解 | "它/这个/那款" → 具体商品还原 | `app/services/query_rewriter.py` |
| Query 去噪 | 去除语气词/感叹词/重复 | `app/services/query_rewriter.py` |
| 热点问题缓存 | Redis ZSet 按日统计 top N | `app/cache/hot_question.py` |
| Embedding 缓存 | Redis Cache-Aside，7 天 TTL | `app/services/embedding_service.py` |
| 监控指标 | 缓存命中率、检索延迟、向量数 | `app/metrics.py` |

---

## 高优先级功能

> 生产运营必备，缺失会导致知识库无法持续维护。

### 1. 单文档重索引 ✅ (已完成)

**问题**：现有 `POST /admin/documents/{id}/reindex` 返回 501，只能通过全量 `--reset` 重建。

**实现**：
- 删除 Milvus 中该 `doc_id` 关联的所有向量
- 重新分块、向量化、入库
- 更新 MySQL documents 表状态

**接口**：`POST /api/v1/admin/documents/{doc_id}/reindex`

**代码变更**：
- `app/vectorstore/milvus_client.py` — 新增 `delete_by_doc_id()`
- `app/api/admin.py` — 实现重索引逻辑

---

### 2. 文档 CRUD 增删改 ✅ (已完成)

**问题**：现有管理 API 只能查看列表，无法动态增删改文档。

**实现**：
- `POST /api/v1/admin/documents` — 上传 JSON 创建文档并摄取
- `PUT /api/v1/admin/documents/{id}` — 更新文档内容并触发重索引
- `DELETE /api/v1/admin/documents/{id}` — 删除 Milvus 向量 + MySQL 记录

**代码变更**：
- `app/models/request.py` — 新增 `DocumentCreateRequest` / `DocumentUpdateRequest`
- `app/api/admin.py` — 新增 PUT / DELETE 端点
- `app/vectorstore/milvus_client.py` — 新增 `delete_by_doc_id()`

---

### 3. Milvus metadata 过滤检索 ✅ (已完成)

**问题**：`VectorRetriever.retrieve()` 接收 `filters` 参数但未实际使用，无法按 doc_type/来源过滤检索结果。

**实现**：
- 在 `MilvusClient.search()` 中拼接 Milvus 表达式 `expr`
- 支持 `doc_type` / `source` / `doc_id` 多字段过滤
- `VectorRetriever` 透传 filters 到 Milvus

**代码变更**：
- `app/vectorstore/milvus_client.py` — 新增 `search_with_filter()`
- `app/services/vector_retriever.py` — 透传 filters 参数

---

### 4. 富文本文件摄取 ✅ (已完成)

**问题**：只支持 JSON，真实场景有大量 PDF/Word/HTML 政策文档。

**实现**：
- `pdfplumber` 解析 PDF
- `python-docx` 解析 Word
- `BeautifulSoup` 解析 HTML
- 新增 `FileIngestor` 类统一调度

**代码变更**：
- `requirements.txt` — 新增依赖
- `app/utils/file_ingestor.py` — 新增文件解析器
- `scripts/ingest_data.py` — 支持 `.pdf` / `.docx` / `.html` 文件

---

## 中优先级功能

> 提升检索质量和运营分析能力。

### 5. 混合检索（dense + sparse）

**问题**：dense vector 对商品型号/品牌等精确关键词召回不足。

**方案**：
- 引入 BGE-M3 或 BM25 计算 sparse vector
- 与 Milvus dense 向量混合评分

### 6. 语义分块

**问题**：基于段落/字符切分可能切断完整句子。

**方案**：
- 接入 LLM 按语义边界切分
- 或使用 `langchain RecursiveCharacterTextSplitter`

### 7. 文档版本管理

**问题**：知识更新后无法回溯历史版本。

**方案**：
- MySQL documents 表增加 `version` 字段，每次更新自增
- Milvus 存储 version，便于历史回溯

### 8. 检索分析

**问题**：无法知道哪些文档被检索得多。

**方案**：
- `VectorRetriever` 记录 `doc_id → retrieval_count`
- 定时写入 MySQL，`GET /admin/documents` 附带调用次数

### 9. 实时增量摄取

**问题**：数据源更新后需要手动触发。

**方案**：
- Celery 定时任务监听数据目录变更
- 或提供 `PUT /admin/documents/{id}` 支持单个文档热更新

---

## 低优先级功能

> 高级能力，后续迭代考虑。

| 功能 | 说明 |
|------|------|
| Chunk 质量评分 | 基于困惑度过滤低质量 chunks |
| 多语言支持 | 中英混合知识库跨语言检索 |
| 知识图谱增强 | 实体抽取为图谱节点增强关联 |
| 自动知识库生成 | 爬虫 + 解析 + 自动化摄取 |
| RAG 评测基准 | 接入 `ragas` 评测召回质量 |

---

## 实现记录

| 日期 | 功能 | 状态 | 变更文件 |
|------|------|------|---------|
| 2026-04-05 | 单文档重索引 | ✅ | `admin.py` + `milvus_client.py` |
| 2026-04-05 | 文档 CRUD 增删改 | ✅ | `admin.py` + `models/request.py` |
| 2026-04-05 | Milvus metadata 过滤检索 | ✅ | `milvus_client.py` + `vector_retriever.py` |
| 2026-04-05 | 富文本文件摄取 | ✅ | `utils/file_ingestor.py` + `requirements.txt` + `ingest_data.py` |
| 2026-04-05 | MySQL schema 增强 | ✅ | `init_mysql.sql` (content_raw 字段 + deleted 状态) |