请基于当前项目代码，新增或修复"AI 客服自动识别用户转人工意图并触发转人工"的功能。

当前项目背景：

项目是 RAG 智能客服系统，已经具备：

- 用户与 AI 客服对话
- RAG 回复
- 用户手动点击"转人工客服"按钮
- 创建或关联 promotion_kefu_conversation
- ragz_sessions.handoff_status 转人工状态
- 客服后台 admin.html 查看转人工请求
- 客服接入与关闭请求
- 人工客服 WebSocket 聊天能力

现在希望新增能力：

当用户在聊天中输入明显希望转人工的内容时，AI 客服可以自动帮用户发起转人工。

例如用户输入：

- 转人工
- 人工客服
- 我要找人工
- 找客服
- 联系客服
- 帮我转人工
- 真人客服
- 找人处理
- 我要人工处理
- 你解决不了，找人工
- 投诉
- 退款问题找客服
- 售后人工
- 订单异常找人工

系统应该自动触发转人工流程。

---

## 一、目标行为

用户在 http://localhost/?user_id=288 页面中正常和 AI 客服对话。

当用户输入具有转人工意图的消息时：

1. 后端识别该消息是"转人工意图"。
2. 不再继续走 RAG 生成普通 AI 回复。
3. 调用或复用现有转人工逻辑。
4. 创建或复用 promotion_kefu_conversation。
5. 更新 ragz_sessions.handoff_status = 1。
6. 写入必要的系统记录，例如"用户请求转人工"。
7. 返回给用户简洁提示：

   已为你转接人工客服，请稍候。

8. 用户端进入 waiting_human 状态。
9. 用户端连接 /ws/kefu/user。
10. admin.html 人工客服管理中可以看到新的转人工请求。
11. 如果用户重复输入"转人工"，不要重复创建多个 promotion_kefu_conversation。
12. 如果当前会话已经是 handoff_status = 1 或 2，则不要再次触发，只返回当前状态。

---

## 二、不要做的事情

请注意：

1. 不要只让 AI 回复"好的，我帮你转人工"，但实际没有创建客服会话。
2. 不要重复创建 promotion_kefu_conversation。
3. 不要破坏用户手动点击"转人工客服"按钮的逻辑。
4. 不要让 summary / metadata 显示到用户聊天窗口。
5. 不要把人工客服消息写进 ragz_messages。
6. 不要影响普通 AI 问答。
7. 不要大重构现有 WebSocket。
8. 不要新增一套独立转人工系统，必须复用现有 handoff API / service 逻辑。

---

## 三、请先定位现有代码

请先检查当前项目中这些文件，路径按真实项目为准：

- backend/app/api/websocket.py
- backend/app/api/kefu.py
- backend/app/services/session_service.py
- backend/app/database/session.py
- backend/app/database/message.py
- frontend/index.html
- frontend/admin.html

请确认：

1. /ws/chat 中用户消息进入 RAG 前的处理位置。
2. 当前用户点击"转人工客服"按钮调用的是哪个接口。
3. 当前 POST /api/kefu/handoff/request 或等价逻辑在哪里。
4. 当前 ragz_sessions.handoff_status 如何更新。
5. 当前 promotion_kefu_conversation 如何创建。
6. 当前 kefu_conversation_id 如何回写到 ragz_sessions。
7. 当前 index.html 如何进入 waiting_human / human 模式。
8. 当前 /ws/kefu/user 如何连接。

---

## 四、转人工意图识别

请新增一个轻量函数，例如：

```python
def is_handoff_intent(text: str) -> bool:
    ...
```

识别规则：

- 文本为空时返回 False。
- 做基础归一化：
  - strip
  - lower
  - 去掉多余空格
- 命中以下关键词或短语时返回 True：
  - 转人工
  - 人工客服
  - 人工
  - 真人客服
  - 找客服
  - 联系客服
  - 联系人工
  - 找人工
  - 我要人工
  - 帮我转人工
  - 转接人工
  - 人工处理
  - 找人处理
  - 客服介入
  - 投诉
  - 售后人工
  - 退款人工
  - 订单异常

避免误判：

如果用户只是问：

- 什么是人工客服？
- 如何设置人工客服？
- 你们有人工客服吗？

可以先不自动转人工，而是让 AI 正常回答。

请实现一个简单但可维护的规则，例如：

- **强触发短语**：直接触发
  - 转人工
  - 我要找人工
  - 帮我转人工
  - 联系客服
  - 真人客服
  - 找人处理
- **弱触发词**：需要结合上下文或动词
  - 人工
  - 客服
  - 投诉
  - 退款
  - 售后

MVP 阶段可以先用规则，不需要引入大模型分类器。

## 五、接入点要求

请在 /ws/chat 处理用户消息时，进入 RAG 前增加判断：

伪代码：

```python
async def handle_user_message(...):
    # 1. 保存或获取当前 session
    session = await get_or_create_session(...)

    # 2. 先检查当前 handoff_status
    if session.handoff_status in [1, 2]:
        # 已经在人工流程中
        # 不调用 RAG
        # 消息写入 promotion_kefu_message
        # 推送给客服端
        return

    # 3. 检查用户消息是否是转人工意图
    if is_handoff_intent(user_message):
        # 可以先把用户原始消息写入 ragz_messages，保留 AI 阶段上下文
        # 然后调用现有转人工 service
        handoff_result = await request_handoff(
            session_id=session.id,
            user_id=current_user_id,
            reason=user_message,
            trigger_source="ai_detected_intent"
        )

        # 发送给用户简洁提示
        await websocket.send_json({
            "type": "handoff_started",
            "event": "handoff.status_changed",
            "session_id": session.id,
            "kefu_conversation_id": handoff_result.kefu_conversation_id,
            "handoff_status": 1,
            "message": "已为你转接人工客服，请稍候。"
        })

        # 不再调用 RAG
        return

    # 4. 否则正常走 RAG
    await run_rag(...)
```

要求：

- 转人工意图判断必须发生在 RAG 调用前。
- 命中转人工意图后，不要继续调用 RAG。
- 转人工逻辑必须复用已有 handoff 创建逻辑。
- 如果没有 service，请把现有 POST /api/kefu/handoff/request 内的核心逻辑抽成 service，供 HTTP 和 WebSocket 共用。
- 不要复制两套创建 promotion_kefu_conversation 的代码。

## 六、建议抽象 HandoffService

如果当前转人工逻辑只写在 API 里，请抽成 service，例如：

```python
class HandoffService:
    async def request_handoff(
        self,
        session_id: str,
        user_id: str,
        reason: str | None = None,
        trigger_source: str = "manual_button"
    ):
        ...
```

支持两种触发来源：

- `manual_button` — 用户点击按钮
- `ai_detected_intent` — AI/后端识别用户想转人工

共同逻辑：

1. 校验 session 属于当前用户。
2. 查询 ragz_sessions。
3. 如果已经有 kefu_conversation_id，直接复用。
4. 如果没有，创建 promotion_kefu_conversation。
5. 更新 ragz_sessions：
   - handoff_status = 1
   - handoff_at = NOW()
   - kefu_conversation_id
   - merge metadata，不覆盖原 metadata：
     ```json
     {
       "handoff": {
         "trigger_source": "ai_detected_intent",
         "reason": "用户原始输入",
         "requested_at": "..."
       }
     }
     ```
6. 写入 promotion_kefu_message 系统记录：用户请求转人工。
7. 返回：
   - session_id
   - kefu_conversation_id
   - handoff_status
   - conversation_status

## 七、前端 index.html 改造

用户端需要支持 AI 自动触发转人工事件。

当 /ws/chat 收到以下事件之一时：

- `handoff_started`
- `handoff.status_changed`

前端应该：

1. 设置 currentSessionId。
2. 设置 currentKefuConversationId。
3. 设置 chatMode = "waiting_human"。
4. 显示简洁提示：已为你转接人工客服，请稍候。
5. 自动连接：
   ```javascript
   new WebSocket(
     `${wsBaseUrl}/ws/kefu/user?conversation_id=${currentKefuConversationId}`,
     ["bearer", token]
   )
   ```

要求：

- 不要继续等待 AI 流式回复。
- 不要渲染 summary / metadata。
- 不要重复追加多条系统提示。

## 八、客服后台效果

AI 自动触发转人工后，admin.html 应该和用户点击按钮效果一致。

要求：

- admin.html 人工客服管理中能看到该请求。
- 请求详情中能看到：
  - 用户信息
  - ragz_sessions.summary
  - ragz_sessions.metadata
  - 最近 ragz_messages
  - 触发原因 reason
  - trigger_source = ai_detected_intent
- 客服可以接入。
- 客服接入后，用户页面收到 conversation.accepted。
- 客服可以正常回复用户。
- 用户页面实时显示客服消息。

## 九、状态兼容

请处理以下情况：

### handoff_status = 0

- 正常 AI 模式。
- 如果识别到转人工意图，触发转人工。

### handoff_status = 1

- 已经等待人工接入。
- 用户再次输入"转人工"，不要重复创建，只提示：你已进入人工客服队列，请稍候。

### handoff_status = 2

- 人工客服处理中。
- 用户消息应走人工客服通道，不调用 RAG。

### handoff_status = 3

- 人工服务已结束。
- 如果用户再次请求转人工，可以创建新的客服会话，或复用当前规则重新发起。
- MVP 可以先允许重新发起。

## 十、测试要求

请测试以下流程：

1. **打开**

   http://localhost/?user_id=288

2. **用户发送普通问题**

   你们的物流多久发货？

   **期望**：正常走 RAG，不触发转人工

3. **用户输入**

   我要转人工

   **期望**：
   - 不调用 RAG
   - 自动创建或复用 promotion_kefu_conversation
   - ragz_sessions.handoff_status = 1
   - 用户页面显示"已为你转接人工客服，请稍候。"
   - 用户端连接 /ws/kefu/user
   - admin.html 能看到请求

4. **用户重复输入**

   转人工

   **期望**：不重复创建 promotion_kefu_conversation，只提示已在人工队列中

5. **客服在 admin.html 点击接入**

   **期望**：
   - ragz_sessions.handoff_status = 2
   - 用户页面实时显示人工客服已接入

6. **客服发送**

   您好，我是人工客服

   **期望**：
   - promotion_kefu_message 写入 sender_type = 2 的消息
   - 用户页面实时显示客服回复

7. **用户在人工客服处理中继续发送消息**

   **期望**：
   - 不调用 RAG
   - 写入 promotion_kefu_message
   - 客服页面实时收到

8. **测试误判**

   用户输入：什么是人工客服？

   **期望**：不自动转人工，正常 AI 回答

9. **测试用户点击按钮转人工**

   **期望**：
   - 仍然正常可用
   - 与 AI 自动识别转人工共用同一套 service

## 十一、验收标准

完成后必须满足：

- 用户点击按钮可以转人工。
- 用户输入明确转人工意图也可以自动转人工。
- 自动转人工不会继续调用 RAG。
- 自动转人工不会重复创建客服会话。
- 自动转人工请求能出现在 admin.html。
- 自动转人工后用户端进入 waiting_human。
- 客服接入和回复流程正常。
- 普通问题不误触发转人工。
- "什么是人工客服？"这类咨询不触发转人工。
- 原有 AI 问答不受影响。
- 原有历史会话不受影响。
- 原有人工客服 WebSocket 不受影响。

## 十二、最终输出要求

请完成后输出：

- 修改了哪些文件。
- 是否新增 HandoffService。
- 转人工意图识别函数在哪里。
- 哪些关键词会触发转人工。
- 如何避免误判。
- /ws/chat 中的接入点在哪里。
- HTTP 按钮转人工和 AI 自动转人工是否复用同一逻辑。
- 数据库写入了哪些字段。
- 如何测试。
- 当前限制和后续优化建议。
