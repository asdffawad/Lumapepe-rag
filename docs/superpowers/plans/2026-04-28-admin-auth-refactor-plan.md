# Admin 端点统一鉴权重构 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标:** 将 admin 端点的鉴权从 `get_current_user` 中的硬编码路径匹配改为显式 `Depends(get_admin_user)` 依赖注入，消除隐式鉴权。

**架构:** 在 `middleware/auth.py` 中新增 `get_admin_user` 依赖函数，从 `get_current_user` 移除 `admin_paths` 硬编码分支，所有 admin 端点显式声明 `Depends(get_admin_user)`。

**技术栈:** FastAPI, Pydantic, aiomysql

---

## 文件变更概览

| 文件 | 变更 |
|------|------|
| `backend/app/middleware/auth.py` | 新增 `get_admin_user` 函数；`get_current_user` 移除 `admin_paths` 分支 |
| `backend/app/api/admin.py` | 全部 12 个 admin 端点添加/替换为 `Depends(get_admin_user)` |

---

## Task 1: 修改 `middleware/auth.py`

### 1a: 新增 `get_admin_user` 函数

**文件:** `backend/app/middleware/auth.py`

**步骤 1:** 在 `get_current_user` 函数末尾（第 341 行的 `}` 之后）新增 `get_admin_user` 函数：

```python
async def get_admin_user(request: Request) -> AuthUser:
    """
    FastAPI 依赖：admin 专属鉴权，同时支持 JWT Bearer Token 和 Admin API Key。

    支持的认证方式（优先级依次）：
    1. X-Admin-Key: <key> — 验证管理员 API Key
    2. Authorization: Bearer <token> — 验证 JWT，role 必须为 "admin"

    Raises:
        HTTPException 401: 未提供认证信息或 Token 无效
        HTTPException 403: Token 有效但非 admin 角色
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
        except JWTError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": str(e)},
                headers={"WWW-Authenticate": "Bearer"},
            )

        if user.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "ADMIN_REQUIRED",
                    "message": "此接口需要 admin 权限"
                },
            )
        return user

    # 3. 均未提供
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": "UNAUTHORIZED",
            "message": "请提供 Authorization: Bearer <token> 或 X-Admin-Key"
        },
        headers={"WWW-Authenticate": "Bearer"},
    )
```

### 1b: 从 `get_current_user` 中移除 admin_paths 分支

**步骤 2:** 从 `get_current_user` 函数中删除以下代码块（位于第 283-311 行）：

```python
    # 管理员端点检查
    admin_paths = {
        "/api/v1/admin/ingest",
        "/api/v1/admin/documents",
    }
    if request.url.path in admin_paths:
        # 检查管理员认证
        admin_key = _extract_admin_key(request)
        if admin_key and verify_admin_key(admin_key):
            return AuthUser(user_id="admin", role="admin")

        # 检查 JWT 是否具有 admin 角色
        token = _extract_bearer_token(request)
        if token:
            try:
                user = decode_token(token)
                if user.role == "admin":
                    return user
            except JWTError:
                pass

        # 管理员端点无认证
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ADMIN_REQUIRED",
                "message": "管理员权限认证失败，请提供有效的 X-Admin-Key 或使用 admin 角色的 JWT"
            },
        )
```

**步骤 3:** 运行语法检查

```bash
cd backend && conda activate rag-test && python -m py_compile app/middleware/auth.py && echo "OK"
```

预期: 输出 "OK"

---

## Task 2: 修改 `api/admin.py` — 为所有 12 个端点添加鉴权

**文件:** `backend/app/api/admin.py`

**步骤 1:** 修改文件顶部的 import，添加 `get_admin_user`：

```python
# 修改前（第 739 行附近）
from app.middleware.auth import get_current_user, AuthUser

# 修改后
from app.middleware.auth import get_current_user, get_admin_user, AuthUser
```

**步骤 2:** 为所有 12 个 admin 端点添加 `Depends(get_admin_user)` 依赖，并删除内联的角色检查。

| # | 端点 | 行号 | 改动 |
|---|------|------|------|
| 1 | `POST /admin/ingest` | 76 | 添加 `current_user: AuthUser = Depends(get_admin_user)` |
| 2 | `GET /admin/documents` | 125 | 添加 `current_user: AuthUser = Depends(get_admin_user)` |
| 3 | `GET /admin/documents/{doc_id}` | 162 | 添加 `current_user: AuthUser = Depends(get_admin_user)` |
| 4 | `POST /admin/documents/{doc_id}/reindex` | 186 | 添加 `current_user: AuthUser = Depends(get_admin_user)` |
| 5 | `POST /admin/documents` | 275 | 添加 `current_user: AuthUser = Depends(get_admin_user)` |
| 6 | `POST /admin/documents/upload` | 359 | 添加 `current_user: AuthUser = Depends(get_admin_user)` |
| 7 | `PUT /admin/documents/{doc_id}` | 499 | 添加 `current_user: AuthUser = Depends(get_admin_user)` |
| 8 | `DELETE /admin/documents/{doc_id}` | 592 | 添加 `current_user: AuthUser = Depends(get_admin_user)` |
| 9 | `POST /admin/ingest/incremental` | 637 | 添加 `current_user: AuthUser = Depends(get_admin_user)` |
| 10 | `GET /admin/ws/connections` | 744 | 替换为 `Depends(get_admin_user)`，删除内联 `if role != "admin"` 检查 |
| 11 | `POST /admin/ws/connections/user/clear` | 787-788 | 替换为 `Depends(get_admin_user)` |
| 12 | `POST /admin/ws/connections/all/clear` | 827 | 替换为 `Depends(get_admin_user)`，删除内联 `if role != "admin"` 检查 |

**改动示例（端点 1 `trigger_ingest`）：**

```python
# 修改前
@router.post("/ingest")
async def trigger_ingest(reset: bool = False):
    script_path = SCRIPT_DIR / "ingest_data.py"

# 修改后
@router.post("/ingest")
async def trigger_ingest(
    reset: bool = False,
    current_user: AuthUser = Depends(get_admin_user),
):
    script_path = SCRIPT_DIR / "ingest_data.py"
```

**改动示例（端点 10 `list_connections`）：**

```python
# 修改前（第 743-755 行）
@router.get("/ws/connections")
async def list_connections(current_user: AuthUser = Depends(get_current_user)):
    """
    查看当前活跃的 WebSocket 连接（仅管理员）
    ...
    """
    # 只有管理员可以查看
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限"
        )
    ...

# 修改后
@router.get("/ws/connections")
async def list_connections(
    current_user: AuthUser = Depends(get_admin_user),
):
    """
    查看当前活跃的 WebSocket 连接（仅管理员）
    ...
    """
    # 内联 role 检查已移除，由 Depends(get_admin_user) 保证
    ...
```

**步骤 3:** 运行语法检查

```bash
cd backend && conda activate rag-test && python -m py_compile app/api/admin.py && echo "OK"
```

预期: 输出 "OK"

---

## Task 3: 验证

### 步骤 1: 确认 `admin_paths` 已从 `get_current_user` 中移除

```bash
grep -n "admin_paths" backend/app/middleware/auth.py
```

预期: 无输出

### 步骤 2: 确认 `get_admin_user` 存在

```bash
grep -n "def get_admin_user" backend/app/middleware/auth.py
```

预期: 输出一行，显示函数定义行号

### 步骤 3: 确认所有 admin 端点均使用 `get_admin_user`

```bash
grep -n "Depends(get_admin_user)" backend/app/api/admin.py | wc -l
```

预期: 12

### 步骤 4: 确认无遗漏的端点使用了 `get_current_user`

```bash
grep -n "Depends(get_current_user)" backend/app/api/admin.py
```

预期: 无输出

### 步骤 5: 运行 linter

```bash
cd backend && conda activate rag-test && python -m py_compile app/middleware/auth.py app/api/admin.py && echo "OK"
```

预期: 输出 "OK"

---

## Task 4: 提交

```bash
git add backend/app/middleware/auth.py backend/app/api/admin.py
git commit -m "fix(auth): add get_admin_user dependency, eliminate implicit admin path bypass

- New get_admin_user() dependency in middleware/auth.py enforces admin auth explicitly
- Removed hardcoded admin_paths check from get_current_user() (security risk)
- All 12 admin endpoints now use Depends(get_admin_user) with inline role checks removed
- Fixes MED-2: eliminates implicit path-based authentication that was bypassable
"
```

---

## 自检清单

- [ ] `get_admin_user` 函数位于 `get_current_user` 之后
- [ ] `get_current_user` 中无 `admin_paths` 变量
- [ ] `admin.py` 中 12 个端点均使用 `Depends(get_admin_user)`
- [ ] `admin.py` 中无 `Depends(get_current_user)`
- [ ] `list_connections` 和 `clear_all_connections` 中的内联 `if role != "admin"` 检查已删除
- [ ] linter 检查通过
- [ ] `git commit` 成功
