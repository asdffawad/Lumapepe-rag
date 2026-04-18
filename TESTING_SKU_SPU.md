# 商品 SKU/SPU 参数传递测试指南

## 一、后端 API 接口

### 1.1 商品上下文 API
**端点**: `GET /api/v1/products/context`

**参数**:
- `sku_id` (可选): SKU ID，整数
- `spu_id` (可选): SPU ID，整数
- 两者必选其一

**示例**:
```
GET http://localhost:8000/api/v1/products/context?sku_id=753
GET http://localhost:8000/api/v1/products/context?spu_id=123
```

**响应格式**:
```json
{
  "success": true,
  "data": {
    "product_name": "商品名称",
    "product_type": "SKU" | "SPU",
    "sku_id": 753,
    "spu_id": 123,
    "price": 99.99,           // SPU 价格（元）
    "sku_price": 79.99,       // SKU 价格（元）
    "stock": 100,             // SPU 库存
    "sku_stock": 50,          // SKU 库存
    "introduction": "...",
    "description": "...",
    "parsed_introduction": [...],  // 解析后的结构化简介
    "parsed_description": [...],   // 解析后的结构化详情
    "has_images": true/false,
    "has_html": true/false,
    "properties": {...},      // SKU 规格属性（仅SKU）
    "bar_code": "...",        // 条形码（仅SKU）
    "pic_url": "...",
    ...
  }
}
```

### 1.2 商品详情 API
- `GET /api/v1/products/sku/{sku_id}` - 获取SKU详情
- `GET /api/v1/products/spu/{spu_id}` - 获取SPU详情

---

## 二、测试步骤

### 步骤 1: 确认后端服务运行
```bash
cd backend
# 启动后端服务
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

确认服务正常运行：
```
curl http://localhost:8000/api/v1/health
```

### 步骤 2: 测试商品上下文 API

#### 方法 A: 使用浏览器直接访问
打开浏览器，访问：
```
http://localhost:8000/api/v1/products/context?sku_id=753
```
或
```
http://localhost:8000/api/v1/products/context?spu_id=123
```

**期望响应**:
```json
{
  "success": true,
  "data": {
    "product_name": "Large 70.87in Cat Tree Condominium",
    "product_type": "SKU",
    "sku_id": 753,
    "spu_id": 123,
    "price": 80.88,
    "sku_price": 80.88,
    "stock": 83,
    "sku_stock": 83,
    "parsed_introduction": [...],
    "parsed_description": [...],
    "has_images": true,
    "has_html": false
  }
}
```

#### 方法 B: 使用 curl 命令
```bash
# 测试 SKU
curl "http://localhost:8000/api/v1/products/context?sku_id=753"

# 测试 SPU
curl "http://localhost:8000/api/v1/products/context?spu_id=123"
```

#### 方法 C: 使用 Python 脚本
```python
import requests

response = requests.get("http://localhost:8000/api/v1/products/context", params={
    "sku_id": 753
})
print(response.json())
```

### 步骤 3: 测试 WebSocket 连接（带商品参数）

#### 前端页面测试
1. 打开前端页面：
```
http://localhost:8000/index.html?sku_id=753
```
或
```
http://localhost:8000/index.html?spu_id=123
```

2. **观察现象**：
   - 页面加载后，顶部状态栏应显示：
     - 🔐 认证：从"未认证" → "已认证"（绿色）
     - 连接状态：从"连接中..." → "已连接"（绿色圆点）

   - 聊天消息区域应**自动显示商品信息卡片**，包含：
     - 商品名称
     - 商品类型标签 [SKU] 或 [SPU]
     - 价格、��存（如适用）
     - 商品简介
     - 商品详情
     - 标签：📷 含图片 / 📄 HTML

   - 侧边栏心跳监控开始更新

3. **发送消息测试**：
   输入："这个商品多少钱？"
   期望：AI 应该能够根据商品上下文回答，例如：
   > "这款 Large 70.87in Cat Tree Condominium 的价格是 $80.88"

### 步骤 4: 调试技巧

#### 如果商品信息未显示
1. 打开浏览器开发者工具（F12）
2. 查看 Console 标签，检查是否有错误
3. 查看 Network 标签 → WS，检查 WebSocket 连接
   - 查看第一条 `connected` 消息是否包含 `product_context` 字段
4. 查看 Network 标签 → XHR，检查 `/api/v1/products/context` 请求是否成功

#### 常见问题排查
| 问题 | 可能原因 | 解决方案 |
|------|---------|---------|
| 认证失败 | 后端 `/api/v1/debug/auth/token` 接口异常 | 检查后端日志，确认接口可访问 |
| WebSocket 连接被拒绝 | token 无效或过期 | 刷新页面重新获取 token |
| 商品信息未显示 | URL 参数错误或数据库无此商品 | 确认 sku_id/spu_id 有效 |
| API 返回 404 | 商品不存在 | 使用步骤 2 先测试 API 是否返回数据 |

---

## 三、自动测试脚本

运行本地测试脚本验证商品上下文功能：

```bash
cd backend
conda activate rag-test
python backend/tests/test_product_context_local.py
```

该脚本会：
1. 连接数据库
2. 查询一个 SPU 和 SKU 示例
3. 获取并打印商品上下文
4. 格式化系统提示
5. 关闭连接

---

## 四、已知测试数据

根据 `test_product_context_local.py`，数据库中已有测试数据：

**SPU 示例**:
- SPU ID: 753 (或从数据库查询第一个)
- 商品名: "Large 70.87in Cat Tree Condominium"
- 价格: $80.88

**SKU 示例**:
- SKU ID: 从数据库动态查询第一个
- 关联的 SPU ID: 同 SPU 示例

---

## 五、验证清单

- [ ] 后端服务运行在 `http://localhost:8000`
- [ ] 能够通过 API 获取商品上下文（返回 `success: true`）
- [ ] 前端页面加载时 URL 包含 `?sku_id=xxx` 或 `?spu_id=xxx`
- [ ] 认证状态变为"已认证"（绿色）
- [ ] WebSocket 连接成功（状态"已连接"）
- [ ] 聊天区域显示商品信息卡片
- [ ] 发送消息时，AI 回答中提到了商品信息

---

## 六、快速测试 URL

直接复制到浏览器地址栏测试：

```
http://localhost:8000/index.html?sku_id=753
```

或（如果已知 SPU ID）：
```
http://localhost:8000/index.html?spu_id=123
```

**注意**: 请将 `753` 和 `123` 替换为数据库中实际存在的 ID。
