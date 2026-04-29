# Admin 端点统一鉴权重构设计

**日期**: 2026-04-28  
**状态**: Draft  
**目标**: 修复 MED-2 — Admin 端点认证逻辑复杂、鉴权路径分散的安全隐患

---

## 1. 问题描述

### 1.1 当前代码问题

`backend/app/middleware/auth.py` 中 `get_current_user` 函数约 300 行，admin 鉴权逻辑散布其中：

```python
# 硬编码 admin 路径集合
admin_paths = {
    "/api/v1/admin/ingest",
    "/api/v1/admin/documents",
}
if request.url.path in admin_paths:
    admin_key = _extract_admin_key(request)
    if admin_key and verify_admin_key(admin_key):
        return AuthUser(user_id="admin", role="admin")
    token = _extract_bearer_token(request)
    if token:
        try:
            user = decode_token(token)
            if user.role == "admin":
                return user
        except JWTError:
            pass
    raise HTTPException(status_code=403, ...)
```

**问题**：
- 新增 admin 端点若忘记加入 `admin_paths` 集合，**完全无鉴权保护**
- 认证逻辑分散在 3 个分支，难以审计和维护
- admin 端点依赖路径字符串匹配，而非显式声明

### 1.2 修复目标

每个 admin 端点显式声明 `Depends(get_admin_user)`，替代隐式的路径匹配。

---

## 2. 架构设计

### 2.1 新增 `get_admin_user` 依赖函数

位置：`backend/app/middleware/auth.py`

```python
async def get_admin_user(request: Request) -> AuthUser:
    """
    FastAPI 依赖：admin 专属鉴权

    同时支持：
    1. JWT Bearer Token (Authorization: Bearer <token>)，role 必须为 "admin"
    2. Admin API Key (X-Admin-Key: <key>)

    Raises:
        HTTPException 401: 未提供认证信息
        HTTPException 403: 认证信息无效或非 admin 角色
    """
    # 1. 尝试 Admin API Key
    admin_key = _extract_admin_key(request)
    if admin_key and verify_admin_key(admin_key):
        return AuthUser(user_id="admin", role="admin")

    # 2. 尝试 JWT Bearer Token
    token = _extract_bearer_token(request)
    if token:
        try:
            user = decode_token(token)
            if user.role == "admin":
                return user
            # role 非 admin，返回 403
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "ADMIN_REQUIRED",
                    "message": "此接口需要 admin 权限"
                },
            )
        except JWTError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": str(e)},
                headers={"WWW-Authenticate": "Bearer"},
            )

    # 3. 均未提供
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "UNAUTHORIZED", "message": "请提供 Authorization: Bearer <token> 或 X-Admin-Key"},
        headers={"WWW-Authenticate": "Bearer"},
    )
```

### 2.2 `get_current_user` 简化

从 `get_current_user` 中移除 admin 路径硬编码分支，保持通用认证逻辑不变。

### 2.3 Admin 端点改造

将 `backend/app/api/admin.py` 中所有端点的：

```python
current_user: AuthUser = Depends(get_current_user)
```

替换为：

```python
current_user: AuthUser = Depends(get_admin_user)
```

**涉及端点**（共 10 处）：
1. `POST /admin/ingest`
2. `GET /admin/documents`
3. `GET /admin/documents/{doc_id}`
4. `POST /admin/documents/{doc_id}/reindex`
5. `POST /admin/documents`
6. `POST /admin/documents/upload`
7. `PUT /admin/documents/{doc_id}`
8. `DELETE /admin/documents/{doc_id}`
9. `POST /admin/ingest/incremental`
10. `GET /admin/ws/connections`
11. `POST /admin/ws/connections/user/clear`
12. `POST /admin/ws/connections/all/clear`

---

## 3. 数据流

```
HTTP 请求
    │
    ▼
FastAPI 路由匹配
    │
    ▼
Depends(get_admin_user)
    │
    ├─ 读取 X-Admin-Key → verify_admin_key() → 通过 → return AuthUser(role="admin")
    │
    ├─ 读取 Authorization: Bearer <token> → decode_token()
    │                                           │
    │                                     role=="admin" → 通过
    │                                     role!="admin" → 403
    │                                     token 无效 → 401
    │
    └─ 均无 → 401

通过 → 执行业务逻辑
```

---

## 4. 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `backend/app/middleware/auth.py` | 修改 | 新增 `get_admin_user`；`get_current_user` 移除 admin 路径检查 |
| `backend/app/api/admin.py` | 修改 | 10 个端点的 `Depends(get_current_user)` → `Depends(get_admin_user)` |

**无新增文件，无数据库变更，无 API 兼容性问题（仅内部重构）**

---

## 5. 错误处理

| 场景 | HTTP 状态码 | error.code | 说明 |
|------|-----------|------------|------|
| 未提供任何认证 | 401 | UNAUTHORIZED | 提示提供 Bearer token 或 X-Admin-Key |
| Token 格式错误/已过期 | 401 | INVALID_TOKEN | 不泄露具体原因 |
| Token 有效但 role != admin | 403 | ADMIN_REQUIRED | 提示需要 admin 权限 |
| Admin Key 错误 | 401 | UNAUTHORIZED | 同未提供认证 |
| Admin Key 正确 | 200 | — | 返回 AuthUser(role="admin") |

---

## 6. 安全改进

| 改进点 | 说明 |
|--------|------|
| 消除隐式鉴权 | 不再依赖路径字符串匹配，每个端点显式声明鉴权需求 |
| 集中认证逻辑 | admin 认证逻辑从 3 处收敛到 1 处 |
| 防御性更强 | 新增 admin 端点时，开发者必须使用 `get_admin_user`，否则编译/运行时无认证保护（可被测试发现） |
| role 强制检查 | JWT token 中 role 非 "admin" 时明确返回 403 |

---

## 7. 兼容性

- **API 行为不变**：请求方式、认证头、响应格式完全一致
- **向后兼容**：现有 admin API Key 和 JWT token 继续有效
- **前端无影响**：无需修改任何前端代码

---

## 8. 测试策略

```python
# backend/tests/test_admin_auth.py

class TestGetAdminUser:
    """测试 get_admin_user 依赖"""

    async def test_valid_admin_key(self, ...):
        # X-Admin-Key 正确 → 200

    async def test_valid_admin_jwt(self, ...):
        # Authorization: Bearer <admin-jwt> → 200

    async def test_non_admin_jwt(self, ...):
        # Authorization: Bearer <user-jwt> → 403 ADMIN_REQUIRED

    async def test_missing_auth(self, ...):
        # 无任何认证 → 401 UNAUTHORIZED

    async def test_invalid_token(self, ...):
        # Authorization: Bearer <malformed> → 401 INVALID_TOKEN
```
