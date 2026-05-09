> 本次修复重点：用户重新发起转人工时，绝不能复用 `status=2` 已结束的 `promotion_kefu_conversation`；必须创建新的 conversation，并把 `ragz_sessions.kefu_conversation_id` 更新为新 ID。


# 修复：用户重新发起转人工客服请求后，后台显示请求已结束无法操作

## 问题描述

用户重新发起转人工客服请求后，后台人工客服管理中显示请求已结束，无法继续操作。

**当前现象：**

1. 用户发起转人工客服请求。
2. 客服后台可以接入。
3. 客服关闭请求后，该请求状态变为已结束。
4. 用户再次发起转人工客服请求。
5. `admin.html` 人工客服管理中出现请求，但该请求显示"已结束"。
6. 后台无法再次接入、回复或关闭。

**根因分析：**

新请求错误复用了旧的已关闭 `promotion_kefu_conversation`。

**修复范围：**

只修复转人工请求创建/复用逻辑、状态同步逻辑和前端状态恢复逻辑，不要大重构。

---

## 一、代码定位

请检查以下文件，路径以当前项目实际结构为准：

- `backend/app/api/kefu.py`
- `backend/app/api/websocket.py`
- `backend/app/services/session_service.py`
- `backend/app/database/session.py`
- `backend/app/database/message.py`
- `frontend/index.html`
- `frontend/admin.html`

### 重点定位项

1. `POST /api/kefu/handoff/request`
2. `HandoffService.request_handoff`，如果已经抽象
3. 用户点击"转人工客服"按钮调用逻辑
4. 用户输入"转人工"自动触发逻辑
5. 客服关闭请求接口
6. `ragz_sessions.handoff_status` 更新逻辑
7. `ragz_sessions.kefu_conversation_id` 复用逻辑
8. `promotion_kefu_conversation.status` 判断逻辑
9. `admin.html` 请求列表状态显示逻辑
10. `admin.html` 是否把已关闭旧请求当作新请求展示

---

## 二、状态定义

### 2.1 `promotion_kefu_conversation.status`

| 值 | 含义 |
|----|------|
| 0 | 待接入 |
| 1 | 已接入 / 处理中 |
| 2 | 已结束 |

### 2.2 `ragz_sessions.handoff_status`

| 值 | 含义 |
|----|------|
| 0 | AI处理中 |
| 1 | 等待人工接入 |
| 2 | 人工客服处理中 |
| 3 | 人工服务已结束 |

---

## 三、核心修复：不要复用已关闭 conversation

### 3.1 修复原则

**错误的逻辑示例：**

```python
if session.kefu_conversation_id:
    return existing_conversation
```

**正确的逻辑：**

```python
existing = None
if session.kefu_conversation_id:
    existing = get_promotion_kefu_conversation(session.kefu_conversation_id)

if existing and existing.status in [0, 1]:
    # 0 待接入，1 人工处理中
    # 可以复用
    return existing

if existing and existing.status == 2:
    # 已结束，不能复用
    # 必须创建新的 conversation
    new_conversation = create_promotion_kefu_conversation(
        user_id=session.member_user_id,
        tenant_id=session.tenant_id,
        status=0
    )
    update ragz_sessions
    set
        kefu_conversation_id = new_conversation.id,
        handoff_status = 1,
        handoff_at = NOW()
    where id = session.id
    return new_conversation

if not existing:
    # 没有关联 conversation，创建新的
    new_conversation = create_promotion_kefu_conversation(...)
    update ragz_sessions...
    return new_conversation
```

### 3.2 具体要求

- 已结束的 conversation 绝对不能复用。
- 已结束旧 conversation 不要删除。
- 旧 conversation 保留历史消息。
- 重新发起必须创建新 conversation。
- 新 conversation.status 必须是 0。
- 新 conversation.id 必须回写到 `ragz_sessions.kefu_conversation_id`。
- `handoff_status` 必须从 3 改回 1。
- `admin.html` 应展示新的待接入请求，而不是旧的已结束请求。

---

## 四、关闭接口检查

客服关闭请求时，请确认：

- `promotion_kefu_conversation.status = 2`
- `ragz_sessions.handoff_status = 3`
- **不要清空旧 conversation 的历史数据**

### 推荐方案

关闭时不清空 `ragz_sessions.kefu_conversation_id`，重新发起时判断旧 `conversation.status = 2` 后创建新 conversation 并用新 `conversation_id` 覆盖。

> 也可以选择关闭时清空 `ragz_sessions.kefu_conversation_id`，但必须保证重新发起时创建新 conversation。推荐第一种，更方便追踪最后一次客服会话。

---

## 五、Metadata 记录重新发起历史

重新发起时不要覆盖原 metadata，请使用 JSON merge，追加类似：

```json
{
  "handoff": {
    "current_conversation_id": 456,
    "trigger_source": "manual_button",
    "reason": "用户重新发起转人工",
    "requested_at": "2026-05-06 12:00:00"
  },
  "handoff_history": [
    {
      "conversation_id": 123,
      "status": "closed",
      "closed_at": "2026-05-06 11:50:00"
    },
    {
      "conversation_id": 456,
      "status": "requested",
      "requested_at": "2026-05-06 12:00:00"
    }
  ]
}
```

### 要求

- 不覆盖 existing metadata。
- 如果 metadata 不是合法 JSON，要兼容处理。
- `handoff.current_conversation_id` 必须是新的 `conversation_id`。
- 可以保留旧 `conversation_id` 到 `handoff_history`。

---

## 六、admin.html 请求列表修复

请检查后台人工客服管理列表查询和渲染。

### 要求

1. **待处理列表**默认只展示：
   - `promotion_kefu_conversation.status IN (0, 1)`
   - 或 `ragz_sessions.handoff_status IN (1, 2)`

2. **已结束请求**可以显示在历史列表，但不能显示为新的待接入请求。

3. 用户重新发起后，列表中必须出现新的 conversation：
   - `status = 0`
   - 可点击接入
   - 操作按钮可用

4. 如果前端根据旧 `currentKefuConversationId` 渲染详情，需要在重新发起成功后更新为后端返回的新 `conversation_id`。

5. 不要把旧的已结束 conversation 当作当前请求。

---

## 七、index.html 用户端修复

当用户端当前状态为：`handoff_status = 3`，用户再次点击"转人工客服"时：

### 要求

1. 不要因为本地存在旧 `currentKefuConversationId` 而阻止请求。

2. 仍然调用：`POST /api/kefu/handoff/request`

3. 后端返回新的 `kefu_conversation_id` 后，前端必须更新：
   ```javascript
   currentKefuConversationId = result.kefu_conversation_id;
   chatMode = "waiting_human";
   handoffStatus = 1;
   ```

4. 重新连接：
   ```
   /ws/kefu/user?conversation_id=新的conversation_id
   ```

5. 页面提示：已为你转接人工客服，请稍候。

6. 不要继续连接旧的已关闭 conversation。

---

## 八、AI 自动识别转人工修复

如果用户在关闭后输入：

```
转人工
我要找人工
联系客服
```

也要允许重新发起。

请检查 `/ws/chat` 中的逻辑：

```python
if session.handoff_status == 3 and is_handoff_intent(user_message):
    result = await handoff_service.request_handoff(...)
    return
```

### 要求

- `handoff_status = 3` 时，不要阻止转人工。
- 如果是普通消息，可以恢复 AI。
- 如果是转人工意图，必须创建新的 conversation。
- 不能复用已关闭 conversation。

---

## 九、数据库验证 SQL

修复后请执行或提供以下 SQL 验证：

```sql
SELECT
  id,
  user_id,
  status,
  assigned_admin_id,
  last_message_content,
  last_message_time,
  tenant_id,
  deleted,
  create_time
FROM promotion_kefu_conversation
WHERE user_id = 352
ORDER BY id DESC
LIMIT 10;
```

```sql
SELECT
  id,
  user_id,
  kefu_conversation_id,
  handoff_status,
  handoff_at,
  updated_at
FROM ragz_sessions
WHERE user_id = '352'
   OR member_user_id = 352
ORDER BY updated_at DESC
LIMIT 10;
```

### 期望结果

**旧 conversation：**

- `status = 2`

**新 conversation：**

- id 不同于旧 conversation
- `status = 0`

**ragz_sessions：**

- `kefu_conversation_id = 新 conversation.id`
- `handoff_status = 1`

---

## 十、测试场景

### 场景 1：关闭后点击按钮重新发起

1. 用户访问：`http://localhost/?user_id=288`
2. 用户点击转人工。
3. admin.html 接入请求。
4. admin.html 关闭请求。
5. 用户页面显示人工服务已结束。
6. 用户再次点击转人工。

**期望：**

- 创建新的 `promotion_kefu_conversation`
- 新 conversation.status = 0
- admin.html 显示新的待接入请求
- 操作按钮可用
- 用户页面进入等待人工状态

---

### 场景 2：关闭后输入"转人工"重新发起

1. 人工服务关闭后。
2. 用户输入：`转人工`

**期望：**

- 不调用 RAG
- 创建新的 `promotion_kefu_conversation`
- `ragz_sessions.handoff_status = 1`
- admin.html 出现新的待接入请求

---

### 场景 3：等待中重复点击

`handoff_status = 1` 时重复点击。

**期望：**

- 不创建新 conversation
- 复用当前待接入 conversation
- admin.html 仍只有一个待接入请求

---

### 场景 4：人工处理中重复点击

`handoff_status = 2` 时重复点击。

**期望：**

- 不创建新 conversation
- 用户保持人工处理中

---

### 场景 5：关闭后普通 AI 对话

`handoff_status = 3` 后，用户输入普通问题：`物流多久发货？`

**期望：**

- 可以恢复 AI 流程
- 不自动创建客服会话

---

## 十一、验收标准

修复完成后必须满足：

- [ ] 已关闭的 `promotion_kefu_conversation.status = 2` 不再被复用。
- [ ] 重新发起会创建新的 `promotion_kefu_conversation`。
- [ ] 新 conversation.status = 0。
- [ ] `ragz_sessions.kefu_conversation_id` 更新为新的 `conversation_id`。
- [ ] `ragz_sessions.handoff_status` 从 3 改为 1。
- [ ] admin.html 显示新请求为"待接入"，不是"已结束"。
- [ ] admin.html 可以对新请求执行接入操作。
- [ ] 用户端会连接新的 `/ws/kefu/user?conversation_id=新ID`。
- [ ] 旧请求和旧消息历史仍然保留。
- [ ] 重复点击不会在待接入/处理中创建多个请求。
- [ ] 普通 AI 对话不受影响。
- [ ] 历史会话加载不受影响。

---

## 完成后输出内容

请输出以下内容：

1. **根因**
2. **修改文件**
3. **核心代码改动**
4. **新旧 conversation 状态变化**
5. **SQL 验证结果**
6. **前端验证步骤**

---
