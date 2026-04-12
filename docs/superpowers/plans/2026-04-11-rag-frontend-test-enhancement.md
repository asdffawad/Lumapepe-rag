# RAG 前端测试增强实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 admin.html 新增「检索调试」Tab（时间线模式）和独立 test.html 页面，实现 RAG 各环节可单独调试、参数可调、过程全透明。

**Architecture:** 前端新增两个区域：1) admin.html 的「检索调试」Tab 通过阶段化时间线卡片展示召回→BM25→RRF→重排→LLM 全流程，每个阶段可折叠/展开，参数面板实时调控；2) 独立 test.html 拆出 Embedding 测试 / 检索测试 / 端到端测试三个入口。后端新增 `POST /api/v1/debug/retrieve` 端点接受完整参数，执行分阶段调试并返回各阶段详细结果。

**Tech Stack:** 纯 HTML+CSS+JS（无框架），admin.html / test.html，双页共享样式变量；后端 FastAPI 新增 `/debug/retrieve` 端点。

---

## 文件结构

```
frontend/
  admin.html        — 修改：新增 Tab + 时间线面板 + 参数面板
  test.html         — 新建：独立测试页（3 个入口）
backend/app/api/
  debug.py          — 新建：/debug/retrieve 端点（分阶段调试）
```

---

## Task 1: 后端 Debug 检索 API

**Files:**
- Create: `backend/app/api/debug.py`

---

- [ ] **Step 1: 编写 debug.py — 分阶段检索端点**

端点 `POST /api/v1/debug/retrieve` 接受查询词 + 完整参数，返回各阶段详细结果。

```python
"""
RAG 分阶段调试接口
POST /api/v1/debug/retrieve
返回: { stages: { dense, bm25, rrf, rerank, llm }, params, query }

参数 (request body):
  query: str                      # 查询词
  top_k: int = 10                 # 召回数量
  rerank_top_k: int = 5           # 重排后保留
  hybrid_search: bool = True      # 开启 BM25 混合
  bm25_weight: float = 0.3        # BM25 融合权重
  use_llm_rerank: bool = False    # 启用 LLM 重排
  lang_filter: list[str] = []     # 限定语言 ['zh', 'en', 'vi']
  section_type_filter: list[str] = []  # 限定类型
  run_llm: bool = False           # 是否执行 LLM 生成
  intent: str = "product_qa"      # 意图分类
"""
from __future__ import annotations
import time, asyncio
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/api/v1/debug", tags=["debug"])

class DebugRetrieveRequest(BaseModel):
    query: str
    top_k: int = 10
    rerank_top_k: int = 5
    hybrid_search: bool = True
    bm25_weight: float = 0.3
    use_llm_rerank: bool = False
    lang_filter: list[str] = []
    section_type_filter: list[str] = []
    run_llm: bool = False
    intent: str = "product_qa"

class StageResult(BaseModel):
    count: int
    items: list[dict]   # 每条记录含 content 摘要、metadata、score 等
    duration_ms: float

class DebugRetrieveResponse(BaseModel):
    params: dict
    stages: dict[str, StageResult | None]
    final_message: str | None
    total_latency_ms: float
```

核心逻辑：直接调用 `HybridRetriever`（已实现）获取 dense+bm25+rrf 全过程，从 metadata 提取各阶段分数字段注入 `MMRReranker`。

---

- [ ] **Step 2: 注册路由到 main.py**

在 `backend/app/main.py` 的 `include_router` 部分添加：

```python
from app.api.debug import router as debug_router
app.include_router(debug_router)
```

---

- [ ] **Step 3: 语法检查**

```bash
python -m py_compile backend/app/api/debug.py
echo "exit: $?"
```
Expected: exit: 0

---

## Task 2: admin.html — 新增「检索调试」Tab

**Files:**
- Modify: `frontend/admin.html`（在 `</script>` 前插入 Tab + JS，在 Tab Bar 加入按钮）

---

- [ ] **Step 1: 在 Tab Bar 添加按钮**

在 `<div class="tab-bar">` 末尾新增：

```html
<button class="tab-btn" onclick="switchTab('retrieve')">
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
  检索调试
</button>
```

---

- [ ] **Step 2: 在 `</div>` 关闭 content 区域前插入面板 HTML**

面板包含：参数控制区 + 6 个阶段时间线卡片 + 查询输入 + 执行按钮。

---

- [ ] **Step 3: `switchTab('retrieve')` 时初始化**

在 `switchTab()` JS 函数中追加：

```javascript
else if (name === 'retrieve') { initRetrievePanel(); }
```

---

- [ ] **Step 4: 实现 `initRetrievePanel()` — 参数面板渲染**

渲染以下控件（使用 admin.html 已有 `.toggle` / `.btn` / 样式）：

| 控件 | 类型 | 默认值 |
|------|------|-------|
| 查询词 | text input (全宽) | "" |
| top_k | range slider 5-50 | 10 |
| rerank_top_k | range slider 3-20 | 5 |
| hybrid_search | toggle switch | true |
| bm25_weight | range slider 0-100 (显示%) | 30 |
| use_llm_rerank | toggle switch | false |
| lang_filter | checkbox: 中文/英文/越南 | [] |
| section_type_filter | checkbox: heading/body/bullet/ordered/note | [] |
| run_llm | toggle switch | false |

---

- [ ] **Step 5: 实现 `runRetrieveDebug()` — 调用 debug API 并渲染时间线**

调用 `POST /api/v1/debug/retrieve`，传入所有参数，按顺序渲染 6 个阶段：

**阶段 1: 向量检索**
- 卡片标题：`Dense 召回 · N 篇 · Xms`
- 展开内容：表格，每行：排名 | 内容摘要（60字） | 分数 | section_type | markets | lang
- 点击行展开：显示完整 content + metadata JSON

**阶段 2: BM25 打分**（hybrid_search=true 时显示）
- 卡片标题：`BM25 融合 · bm25_weight=0.30`
- 展开内容：每篇的原始 BM25 分 + 归一化分

**阶段 3: RRF 融合**（hybrid=true 时显示）
- 卡片标题：`RRF 融合 · Top-K 排序`
- 表格：排名 | 标题 | RRF 综合分 | 来源

**阶段 4: MMR 重排**
- 卡片标题：`MMR 重排 · Top-K · [规则/规则+LLM]`
- 表格：排名 | 标题 | MMR分 | section_type | markets | lang | 重排原因（关键词命中 / LLM打分 / 语言偏好）

**阶段 5: LLM 生成**（run_llm=true 时显示）
- 卡片标题：`LLM 生成 · Xms · Y tokens`
- 内容：Prompt 预览（折叠） + 完整回复 + 意图标签

**通用交互：**
- 每个阶段卡片默认折叠，点击标题栏展开/折叠
- 总耗时显示在底部
- 执行中出现错误在最上方显示红色提示

---

- [ ] **Step 6: 验证语法**

前端为纯 HTML/JS，刷新浏览器控制台检查无报错即可。

---

## Task 3: test.html — 独立测试页

**Files:**
- Create: `frontend/test.html`

---

- [ ] **Step 1: 创建 test.html，3 个测试模块**

共用 admin.html 的 CSS 变量和样式模式（从 admin.html 复制变量定义到 `<style>`），整体布局：

```
顶部导航（返回聊天 | Prometheus）
主标题：RAG 独立测试工具
Tab: [Embedding] [检索] [端到端]
内容区（根据 Tab 切换）
```

---

- [ ] **Step 2: Embedding 测试模块**

功能：输入文本 → 显示 token 数估算 + 向量维度 + 前 5 维值（其余省略）

API：`GET /api/v1/debug/embed?text=...`（需先在后端补充，Task 3.5）

若后端暂不支持，fallback 为纯前端 token 估算（汉字 1 token / 字符）。

---

- [ ] **Step 3: 检索测试模块**

功能：输入查询 → 仅看向量检索结果（不送 LLM）

调用 `POST /api/v1/debug/retrieve`，设置 `run_llm=false`，展示 Dense + BM25 + RRF + Rerank 全过程（同 Task 2 的时间线，但无 LLM 阶段）。

---

- [ ] **Step 4: 端到端测试模块**

功能：完整 RAG 流程（检索→重排→LLM），可对比不同参数效果。

- 输入框 + 参数面板 + "发送" 按钮
- 左侧显示对话历史（可展开每条对话查看中间过程）
- 点击单条对话可查看该次检索的各阶段详情

---

- [ ] **Step 5: 后端补充 Embedding 测试 API（如需要）**

若 `EmbeddingService` 已有 `embed()` 方法，可在 `debug.py` 中添加 `GET /api/v1/debug/embed` 端点：

```python
@router.get("/embed")
async def embed_text(text: str = Query(...)):
    service = EmbeddingService.get_instance()
    t0 = time.time()
    vectors = await service.embed([text])
    latency = (time.time() - t0) * 1000
    vec = vectors[0]
    return {
        "text": text,
        "token_count": _estimate_tokens(text),
        "vector_dim": len(vec),
        "vector_preview": vec[:5],
        "latency_ms": round(latency, 1),
    }
```

---

## Task 4: 端到端验证

**Files:**
- 验证: Docker 容器中 `rag-backend` 重启后正常工作

---

- [ ] **Step 1: 重建 backend 镜像（debug.py 变更）**

```bash
cd f:\Myprogram\cursor\work\RAG
docker-compose build rag-backend
docker-compose up -d rag-backend
```

---

- [ ] **Step 2: 检查 debug 端点可用**

```bash
curl -s http://localhost:8000/api/v1/debug/retrieve -X POST \
  -H "Content-Type: application/json" \
  -d '{"query":"iPhone 退货政策","top_k":5,"hybrid_search":true}' | python -m json.tool
```
Expected: 返回包含 `stages.dense`, `stages.bm25`, `stages.rrf`, `stages.rerank` 的 JSON

---

- [ ] **Step 3: 浏览器访问**

打开 `http://localhost:8000/admin/` → 点击「检索调试」Tab → 输入「iPhone 退货政策」→ 执行 → 验证 6 个阶段卡片正确渲染 → 验证参数调节（切换 hybrid_search 开关）后结果变化。

打开 `http://localhost:8000/test.html` → 验证三个 Tab 均正常加载。

---

## 依赖关系

```
Task 1 (后端 debug API)
    ↓
Task 2 (admin.html 检索调试 Tab) ←─ 依赖 Task 1 端点就绪
Task 3 (test.html) ←─ 依赖 Task 1 端点就绪
Task 4 (端到端验证) ←─ 依赖 Task 1+2+3 完成
```

---

## Spec 覆盖率检查

| 设计要求 | 对应实现 |
|---------|---------|
| 时间线模式 6 阶段 | Task 2 Step 5 — 6 个阶段卡片 |
| 完整参数面板（top_k/rerank_top_k/hybrid/bm25_weight/use_llm/lang_filter/section_type_filter） | Task 2 Step 4 — 所有控件 |
| 可折叠/展开 | Task 2 Step 5 — CSS collapse + JS toggle |
| 新字段展示（section_type/markets/lang/hierarchy） | Task 2 Step 5 — 各阶段表格列 |
| 独立测试页（Embedding/检索/端到端） | Task 3 Steps 2-4 |
| Embedding 测试 | Task 3 Step 2 + Task 3 Step 5 |
| 后端 debug API | Task 1 |
