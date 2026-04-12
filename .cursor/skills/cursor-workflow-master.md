---
name: cursor-workflow-master
description: Cursor AI 编程效率提升指南。用于充分利用 Cursor 编程，包括 Rule 调用机制、Skill 触发条件、多文件协同编辑、代码生成质量优化、Rule 迭代更新、AI 生成代码调试。
---

# Cursor 编程工作流

## Rule 调用机制

Cursor 根据文件路径和内容上下文自动匹配适用的 Rules。匹配优先级：

1. **精确路径匹配**: 文件在 Rule 定义的目录下时触发
2. **内容关键词匹配**: Rule 中的关键词在当前文件中出现时触发
3. **显式指令**: 用户在对话中提及 `Use the RAG patterns rule` 时触发

### 本项目 Rule 触发条件

| Rule 文件 | 触发条件 |
|----------|---------|
| `rag-orchestrator-architecture.mdc` | 编辑 `services/rag_*.py` 或 `services/*orchestrator*` |
| `llm-abstraction-design.mdc` | 编辑 `services/llm_*.py` 或 `services/providers/*` |
| `fastapi-service-patterns.mdc` | 编辑 `backend/app/api/*.py` 或 `backend/app/main.py` |
| `tool-router-pattern.mdc` | 编辑 `services/tool_*.py` 或 `services/tools/*` |
| `mysql-redis-patterns.mdc` | 编辑 `database/*.py` 或 `cache/*.py` |
| `celery-async-patterns.mdc` | 编辑 `celery_app/*.py` 或 `celery_app/tasks/*` |

**优化方法**: 在文件顶部添加注释明确标注模块类型：

```python
# backend/app/services/rag_orchestrator.py
# [rag-orchestrator-architecture]
```

## Skill 触发机制

Skills 在对话中通过自然语言触发。当对话涉及以下主题时，对应 Skill 自动激活：

| Skill | 触发关键词 |
|-------|---------|
| `rag-orchestrator-dev` | "RAG"、"检索"、"重排"、"Query 改写"、"Prompt"、"召回" |
| `llm-provider-switching` | "切换模型"、"新增 Provider"、"Token 预算"、"成本控制" |
| `ecommerce-rag-knowledge-base` | "知识库"、"Embedding"、"商品"、"FAQ"、"切块" |
| `fastapi-debug-workflow` | "调试"、"排查"、"错误"、"报错"、"500"、"限流" |
| `cursor-workflow-master` | "Cursor"、"Rule"、"Skill"、"效率" |

## 多文件协同编辑

用 `@` 引用多个文件，让 Cursor 理解跨文件依赖：

```
帮我修改 RAG Orchestrator，新增一个 query 改写策略：
@services/rag_orchestrator.py
@services/query_rewriter.py
@.cursor/rules/rag-orchestrator-architecture.mdc
```

Cursor 会同时读取所有文件内容，理解模块边界后生成改动。

## 代码生成质量优化

### 高质量 Prompt 示例

**好 Prompt**:
```
在 services/tool_router.py 中新增一个天气查询工具。
使用 @register_tool 装饰器注册。
工具名为 "weather_query"，参数包括 city（城市名）。
使用免费的天气 API（和风天气或 OpenWeatherMap）。
参考现有的 order_tool.py 的实现模式。
```

**差 Prompt**:
```
添加一个天气查询功能`
```

### 关键要素

1. **明确文件路径**: 指定要编辑的文件
2. **参考现有模式**: 用 `@` 引用相似实现
3. **引用 Rule**: 明确要遵守的架构规范
4. **描述预期行为**: 工具名、参数、返回格式

## 增量迭代工作流

不要一次要求生成完整模块，分步骤进行：

```
Step 1: 先实现 ToolBase 基类和注册装饰器
Step 2: 实现 OrderTool，参考 tool-router-pattern.mdc
Step 3: 实现 ToolRouter 核心逻辑
Step 4: 添加熔断降级
Step 5: 编写单元测试
```

每步验证后再继续，降低返工率。

## 调试 AI 生成代码

当 AI 生成的代码出现架构违背时：

1. **指出具体问题**: 不说"不对"，说"这里违反了 tool-router-pattern.mdc 中的 X 规则"
2. **引用 Rule 原文**: 给出 Rule 中具体条款
3. **给出修正方向**: 不直接给代码，而是描述正确做法

示例：

```
当前代码在 ToolRouter.execute() 中直接调用了 order_tool 的方法。
根据 tool-router-pattern.mdc 第 3 节，所有工具必须通过 execute() 入口调用，
禁止业务层直接引用工具类。
请重构为通过 TOOL_REGISTRY 字典动态调用。
```

## Rule 迭代流程

当项目架构演进需要更新 Rule 时：

1. **识别需要更新的 Rule**: 哪个架构决策变了
2. **评估影响范围**: 哪些文件可能违反新规则
3. **先改 Rule**: 在 `.cursor/rules/` 下更新对应 `.mdc` 文件
4. **扫描违背**: 用 grep 搜索违反新规则的地方
5. **逐个修复**: 按文件优先级修复（核心模块优先）

```bash
# 示例：Rule 更新后扫描违背
# 假设 tool-router-pattern.mdc 新增了"所有工具必须实现熔断"规则

# 扫描现有工具实现
grep -n "get_timeout" services/tools/*.py
# 结果为空？说明工具未实现超时，违反新规则
```

## 常见陷阱

| 陷阱 | 表现 | 避免方法 |
|------|------|---------|
| 过度依赖 AI 生成 | 代码风格不一致 | 始终引用对应 Rule |
| 跳过测试 | AI 生成代码直接提交 | 每步生成后先本地验证 |
| 违反架构约束 | 看似正确但结构混乱 | 多引用 Rule，用 `@` 协同编辑 |
| 上下文过长 | Cursor 丢失早期对话信息 | 分会话工作，每会话专注一个模块 |
| 不读 Skill | 不知道已有调试/开发指南 | 在调试前先问 "有什么调试 Skill 可用" |

## 最佳实践

1. **每个模块开一个新会话**: RAG 一个会话，Tool Router 一个会话，减少上下文污染
2. **始终引用 Rule**: "参考 `rag-orchestrator-architecture.mdc`" 是最强提示词
3. **优先引用 Skill**: "用 `ecommerce-rag-knowledge-base.md` 中的切块策略" 比自己描述更准确
4. **生成后立即验证**: 不要连续生成 5 个文件后再测试，生成 1 个测 1 个
5. **保留生成历史**: 重要的生成结果复制到注释中，方便回溯
