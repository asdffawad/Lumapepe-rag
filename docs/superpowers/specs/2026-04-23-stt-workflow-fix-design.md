# STT 工作流修复设计文档

**日期:** 2026-04-23
**状态:** 待用户审批
**问题数量:** 3 个关键问题

---

## 1. 问题概述

当前 STT（语音转文字）工作流存在以下关键问题：

| # | 问题 | 严重程度 | 影响 |
|---|------|---------|------|
| 1 | 音频格式字段不符 — 前端发送 WAV，`format` 标注 `'pcm'` | 高 | 语义混乱，维护困难 |
| 2 | 浏览器录音采样率未约束 | 高 | 部分设备识别质量下降或异常 |
| 3 | 语音 WebSocket 非真正流式 | 中 | 无法实现边说边识别 |

---

## 2. 问题 1 修复：音频格式字段修正

### 2.1 当前状态

前端 `sendVoiceRecording()` 发送：

```javascript
{
    type: 'voice_record',
    audio: '<base64-wav>',  // 实际是完整 WAV 文件（有 RIFF 头）
    format: 'pcm',           // 标注为 pcm，语义错误
}
```

后端通过检测 `RIFF` 头能识别为 WAV，但 `format` 字段误导。

### 2.2 修复方案

**方案 B：前端传递真实格式和采样率**

**修改文件：** `frontend/index.html`

1. 修改 `sendVoiceRecording()` — 发送 `format: 'wav'` 和 `sampleRate`

```javascript
// 修改点 1: sendVoiceRecording() 中
state.ws.send(JSON.stringify({
    type: 'voice_record',
    audio: base64,           // WAV base64
    format: 'wav',            // 修正：WAV 格式
    sampleRate: 16000,        // 新增：重采样后的目标采样率
}));
```

2. 修改 `handle_voice_record()`（`websocket.py`）— 将 `sampleRate` 传入 STTConfig

```python
# 修改点 2: backend/app/api/websocket.py 第 651 行
stt_cfg = STTConfig(
    api_key=api_key,
    format=format,
    sample_rate=data.get("sampleRate", 16000),  # 从请求中读取
)
```

**影响范围：** 仅修改前后端各 1 处，不涉及架构变更。

---

## 3. 问题 2 修复：采样率约束与重采样

### 3.1 当前状态

前端录音使用默认采样率（设备相关，常见 44.1kHz 或 48kHz），`paraformer-realtime-v2` 期望 16kHz 输入。

### 3.2 修复方案

**方案 B：Web Audio API 重采样**

在 `webmToPcmWavBase64()` 中加入重采样逻辑：

1. 使用 `AudioContext.decodeAudioData()` 解码 webm
2. 检测原始采样率，如果非 16kHz，执行线性插值降采样
3. 生成 16kHz WAV 文件

**修改文件：** `frontend/index.html`

新增辅助函数 `resampleTo16kHz(audioBuffer)`：

```javascript
function resampleTo16kHz(audioBuffer) {
    const sourceRate = audioBuffer.sampleRate;
    if (sourceRate === 16000) {
        return audioBuffer.getChannelData(0);
    }

    // 线性插值降采样
    const targetRate = 16000;
    const ratio = sourceRate / targetRate;
    const newLength = Math.round(audioBuffer.length / ratio);
    const resampled = new Float32Array(newLength);

    for (let i = 0; i < newLength; i++) {
        const srcIdx = i * ratio;
        const srcIdxFloor = Math.floor(srcIdx);
        const srcIdxCeil = Math.min(Math.ceil(srcIdx), audioBuffer.length - 1);
        const t = srcIdx - srcIdxFloor;
        resampled[i] = (1 - t) * audioBuffer.getChannelData(0)[srcIdxFloor]
                      + t * audioBuffer.getChannelData(0)[srcIdxCeil];
    }
    return resampled;
}
```

修改 `webmToPcmWavBase64()`：

```javascript
async function webmToPcmWavBase64(blob) {
    const audioContext = new AudioContext();
    const arrayBuffer = await blob.arrayBuffer();
    const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);
    // 修改：使用重采样函数
    const pcmData = resampleTo16kHz(audioBuffer);
    const sampleRate = 16000;  // 固定为目标采样率
    audioContext.close();
    // ... 后续 WAV 头构建逻辑不变 ...
}
```

**替代方案（AudioWorklet）：** 如果线性插值精度不足，可使用 `OfflineAudioContext` 进行高质量重采样，但实现复杂度更高。当前场景下线性插值已足够。

**验证方法：** 通过调试日志输出原始采样率和重采样后的长度比例。

---

## 4. 问题 3 修复：语音 WebSocket 流式架构

### 4.1 当前状态

`/ws/voice` 端点采用"收集所有音频块 → 收到 stop → 批量转写"模式：

```
Client → audio (chunk 1)
Client → audio (chunk 2)
Client → audio (chunk 3)
Client → stop
Server → 批量调用 stt.transcribe(all_chunks) → done
```

`transcribe_stream()` 已存在但未被使用。

### 4.2 修复方案

**方案 B：真正的流式改造**

流式模式核心变化：

```
Client → audio (chunk 1)
Server → stream_text (部分结果 1)
Client → audio (chunk 2)
Server → stream_text (部分结果 2)
Client → stop
Server → done (最终结果)
```

**修改文件：** `backend/app/api/voice_ws.py`

#### 4.2.1 修改协议说明

将协议文档从批量模式更新为流式模式（文件头部注释）：

```python
# 新协议：
# 1. Client connects with ?token=<jwt>
# 2. Server sends: {"type": "connected", "session_id": "..."}
# 3. Client sends: {"type": "audio", "data": "<base64>"}
# 4. Server sends: {"type": "stream_text", "text": "<partial transcript>"}
# 5. Client sends: {"type": "stop"}
# 6. Server sends: {"type": "done", "text": "<final transcript>"}
```

#### 4.2.2 重构消息处理循环

当前结构（批量）:

```python
# 收集所有音频块
audio_chunks: list[bytes] = []

while True:
    data = await websocket.receive_json()
    if msg_type == "audio":
        audio_chunks.append(chunk_bytes)  # 仅追加
    elif msg_type == "stop":
        text = await stt.transcribe(b"".join(audio_chunks))  # 批量转写
```

新结构（流式）:

```python
# 流式转写对象（async generator）
transcribe_task = None
buffered_chunks = []

async def stream_transcribe():
    """异步生成器：持续产出转写片段"""
    async for text in stt.transcribe_stream(buffered_chunks):
        yield text

while True:
    data = await websocket.receive_json()
    if msg_type == "audio":
        chunk_bytes = base64.b64decode(data.get("data", ""))
        buffered_chunks.append(chunk_bytes)
        # 立即转发到 STT 流式接口
        if transcribe_task is None:
            transcribe_task = asyncio.create_task(_stream_and_forward(buffered_chunks, websocket))
    elif msg_type == "stop":
        # 通知 STT 结束输入
        if transcribe_task:
            await transcribe_task
        await websocket.send_json({
            "type": "done",
            "text": accumulated_text,
            "session_id": session_id,
        })
```

#### 4.2.3 关键挑战

**挑战 1：transcribe_stream 接受的是 async iterable，但 buffered_chunks 是 list**

需要将 list 包装为 async iterable：

```python
async def _audio_iter(chunks: list):
    for chunk in chunks:
        yield chunk

# 改造后：
async for text in stt.transcribe_stream(_audio_iter(buffered_chunks)):
    await websocket.send_json({"type": "stream_text", "text": text})
```

**挑战 2：transcribe_stream 需要在音频发送同时接收结果**

当前 `_stream_audio` 内部已经实现了并发收发（见 `stt_service.py` 第 423 行），只需将 `transcribe_stream` 的输出实时转发到客户端。

**挑战 3：并发安全**

需要用 `asyncio.Queue` 替代 `list` 来安全地在接收线程和发送线程间传递音频块：

```python
audio_queue: asyncio.Queue = asyncio.Queue()

async def audio_producer():
    """接收客户端音频块，实时放入队列"""
    while True:
        data = await websocket.receive_json()
        if data.get("type") == "audio":
            chunk = base64.b64decode(data.get("data", ""))
            await audio_queue.put(chunk)
        elif data.get("type") == "stop":
            await audio_queue.put(None)  # 结束信号
            break

async def audio_consumer():
    """消费队列，转发到 STT"""
    async for text in stt.transcribe_stream(_queue_iter(audio_queue)):
        await websocket.send_json({"type": "stream_text", "text": text})
```

**简化方案（推荐）：** 保持当前批量收集逻辑不变，但改用 `transcribe_stream()` 替代 `transcribe()`，这样可以同时利用 DashScope 的流式识别能力，同时保持代码简单。

---

## 5. 实现优先级

| 优先级 | 任务 | 预计改动量 |
|--------|------|----------|
| P0 | 问题 1：格式字段修正 | 前端 1 处 + 后端 1 处 |
| P0 | 问题 2：重采样逻辑 | 前端 ~30 行 |
| P1 | 问题 3：流式架构 | 后端 ~50 行 + 测试 |

---

## 6. 测试计划

1. **采样率测试** — 验证不同设备录音都能正确重采样到 16kHz
2. **格式字段测试** — 验证后端能正确识别 format='wav'
3. **流式识别测试** — 验证边说边识别延迟 < 500ms
4. **兼容性测试** — Chrome/Firefox/Safari 录音 + 识别

---

## 7. 风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 线性插值精度不足 | 低 | 中 | 可升级为 `OfflineAudioContext` 重采样 |
| Safari 不支持 `getUserMedia` 部分参数 | 中 | 低 | 静默降级到浏览器默认采样率，后端可处理 |
| 流式改造引入并发 bug | 中 | 中 | 充分单元测试 + 逐步集成测试 |

---

## 8. 决定事项

以下问题需要在审批前确认：

- [x] 问题 1 格式字段 — 确认采用方案 B（前端传 `format: 'wav'` + `sampleRate`）
- [x] 问题 2 重采样 — 确认采用方案 B（Web Audio API 线性插值）
- [x] 问题 3 流式 — 确认采用方案 B（Queue 驱动的真正流式）

---

**下一步：** 用户审批后，调用 `writing-plans` skill 生成任务计划并开始实施。
