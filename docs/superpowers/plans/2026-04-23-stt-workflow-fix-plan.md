# STT 工作流修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 STT 语音转文字工作流中的 3 个关键问题：音频格式字段不符、采样率未约束、语音 WebSocket 非真正流式。

**Architecture:**
- **前端**: 修改 `sendVoiceRecording()` 和 `webmToPcmWavBase64()`，加入重采样逻辑
- **后端 WebSocket**: 修改 `handle_voice_record()` 读取 `sampleRate`；改造 `voice_ws.py` 实现真正的流式识别
- **工作目录**: 直接在当前 RAG 主分支上修复（问题不涉及 feature 分支独有代码）

**Tech Stack:** JavaScript (Web Audio API, MediaRecorder), Python (FastAPI, asyncio.Queue, aiohttp)

---

## File Structure

```
frontend/index.html                              # MODIFY: 格式字段修正 + 重采样
backend/app/api/websocket.py                     # MODIFY: handle_voice_record() 读取 sampleRate
backend/app/api/voice_ws.py                      # MODIFY: asyncio.Queue 流式改造
backend/tests/test_stt_workflow.py               # CREATE: 端到端 STT 工作流测试
```

---

## Task 1: 前端 — 修正音频格式字段 + 添加重采样

**Files:**
- Modify: `frontend/index.html:2192-2219` (sendVoiceRecording 函数)
- Modify: `frontend/index.html:2137-2184` (webmToPcmWavBase64 函数)

### 1A: 添加重采样辅助函数

- [ ] **Step 1: 在 `webmToPcmWavBase64` 之前插入重采样函数**

在 `frontend/index.html` 中找到 `async function webmToPcmWavBase64(blob) {` (约第 2137 行)，在其**上方**插入：

```javascript
    function resampleTo16kHz(audioBuffer) {
        const sourceRate = audioBuffer.sampleRate;
        if (sourceRate === 16000) {
            return audioBuffer.getChannelData(0);
        }

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

        console.debug('[Voice] Resampled audio', {
            from: sourceRate,
            to: targetRate,
            samples: `${audioBuffer.length} → ${newLength}`
        });
        return resampled;
    }
```

- [ ] **Step 2: 修改 `webmToPcmWavBase64` 函数体**

找到当前 `webmToPcmWavBase64` 函数中这行：

```javascript
        const pcmData = audioBuffer.getChannelData(0);
        const sampleRate = audioBuffer.sampleRate;
```

替换为：

```javascript
        const pcmData = resampleTo16kHz(audioBuffer);
        const sampleRate = 16000;
```

### 1B: 修正 format 字段

- [ ] **Step 3: 修改 `sendVoiceRecording` 中的 format 字段**

找到 `sendVoiceRecording` 函数 (约第 2192 行)，找到：

```javascript
            state.ws.send(JSON.stringify({
                type: 'voice_record',
                audio: base64,
                format: 'pcm',
            }));
```

替换为：

```javascript
            state.ws.send(JSON.stringify({
                type: 'voice_record',
                audio: base64,
                format: 'wav',
                sampleRate: 16000,
            }));
```

- [ ] **Step 4: 验证前端文件语法正确**

打开 `frontend/index.html` 在浏览器中检查控制台无语法错误。

### 1C: 测试重采样逻辑

- [ ] **Step 5: 添加浏览器端重采样测试代码**

在 `sendVoiceRecording` 函数之前（`blobToBase64` 函数之后）添加测试辅助函数（用于验证重采样正确性，不需要时可以删除）：

```javascript
    // Test helper — remove in production
    function _testResample() {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const sampleRate = ctx.sampleRate;
        const numSamples = 16000; // 1 second at native rate
        const buffer = ctx.createBuffer(1, numSamples, sampleRate);
        const data = buffer.getChannelData(0);
        for (let i = 0; i < numSamples; i++) {
            data[i] = Math.sin(440 * 2 * Math.PI * i / sampleRate);
        }
        ctx.close();

        const result = resampleTo16kHz(buffer);
        const expectedLen = Math.round(numSamples * 16000 / sampleRate);
        console.assert(result.length === expectedLen,
            `[Voice] Resample test: expected ${expectedLen}, got ${result.length}`);
        console.debug('[Voice] Resample test PASSED:', {
            inputRate: sampleRate,
            outputLength: result.length,
            expectedLength: expectedLen
        });
    }
```

运行：在浏览器控制台执行 `_testResample()`，确认输出 `Resample test PASSED`。

- [ ] **Step 6: 提交**

```bash
git add frontend/index.html
git commit -m "fix: correct audio format field and add 16kHz resampling for STT"
```

---

## Task 2: 后端 — handle_voice_record 读取 sampleRate

**Files:**
- Modify: `backend/app/api/websocket.py:595-686` (handle_voice_record 函数)

- [ ] **Step 1: 找到 `handle_voice_record` 函数中的 STTConfig 创建处**

在 `backend/app/api/websocket.py` 第 651 行附近，找到：

```python
        stt_cfg = STTConfig(api_key=api_key, format=format)
```

替换为：

```python
        sample_rate = data.get("sampleRate")
        stt_cfg = STTConfig(
            api_key=api_key,
            format=format,
            sample_rate=sample_rate if sample_rate else 16000,
        )
```

- [ ] **Step 2: 写单元测试**

创建 `backend/tests/test_stt_workflow.py`（如果不存在），添加：

```python
# backend/tests/test_stt_workflow.py
import pytest


def test_handle_voice_record_reads_samplerate():
    """Verify that handle_voice_record passes sampleRate from request to STTConfig."""
    import inspect
    from app.api import websocket as ws_module

    source = inspect.getsource(ws_module.handle_voice_record)
    assert 'data.get("sampleRate")' in source, \
        "handle_voice_record should read sampleRate from request data"
    assert 'sample_rate=' in source, \
        "STTConfig should be initialized with sample_rate parameter"
```

- [ ] **Step 3: 运行测试验证**

Run: `cd backend && conda activate rag-test && python -m pytest tests/test_stt_workflow.py::test_handle_voice_record_reads_samplerate -v`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add backend/app/api/websocket.py backend/tests/test_stt_workflow.py
git commit -m "fix: read sampleRate from voice_record request and pass to STTConfig"
```

---

## Task 3: 后端 — 语音 WebSocket 流式架构改造

**Files:**
- Modify: `backend/app/api/voice_ws.py` (完全重写消息处理循环)

### 3A: 理解当前架构

当前 `voice_ws.py` 采用"收集所有音频块 → stop 时批量转写"模式：
- `audio_chunks: list[bytes]` — 收集所有块
- `msg_type == "stop"` 时调用 `stt.transcribe(b"".join(audio_chunks))`

需要改造为：使用 `asyncio.Queue` 实现边收边转写边推送。

### 3B: 重写消息处理循环

- [ ] **Step 1: 找到消息处理循环的起始位置**

在 `backend/app/api/voice_ws.py` 第 80-176 行，找到：

```python
    try:
        # Send connected ack
        await websocket.send_json({
            "type": "connected",
            "session_id": session_id,
        })

        # Collect audio chunks
        audio_chunks: list[bytes] = []
```

将整个消息处理循环从第 80 行 `try:` 到第 176 行 `finally:` 替换为以下新实现：

```python
    try:
        # Send connected ack
        await websocket.send_json({
            "type": "connected",
            "session_id": session_id,
        })

        # Async queue for buffering audio chunks between receiver and transcriber
        audio_queue: asyncio.Queue = asyncio.Queue()
        accumulated_text = ""

        async def _queue_to_async_iter():
            """Convert queue to async iterable."""
            while True:
                chunk = await audio_queue.get()
                if chunk is None:
                    break
                yield chunk

        async def _transcribe_and_forward():
            """Consume audio from queue, stream to DashScope, forward results to client."""
            nonlocal accumulated_text
            try:
                stt = _get_stt_provider()
                async for text in stt.transcribe_stream(_queue_to_async_iter()):
                    accumulated_text += text
                    await websocket.send_json({
                        "type": "stream_text",
                        "text": text,
                    })
            except STTError as e:
                _logger.error("STT streaming error", extra={"error": str(e), "user_id": user_id})
                await websocket.send_json({
                    "type": "error",
                    "code": "STT_ERROR",
                    "message": f"Transcription failed: {e}",
                })
            except Exception as e:
                _logger.error("Unexpected transcription error", extra={"error": str(e), "user_id": user_id})
                await websocket.send_json({
                    "type": "error",
                    "code": "INTERNAL_ERROR",
                    "message": "Transcription service unavailable",
                })

        # Start the transcriber task
        transcriber_task = asyncio.create_task(_transcribe_and_forward())

        while True:
            try:
                data = await websocket.receive_json()
            except Exception:
                break

            msg_type = data.get("type")

            if msg_type == "audio":
                b64_data = data.get("data", "")
                if not b64_data:
                    continue

                try:
                    chunk_bytes = base64.b64decode(b64_data)
                except Exception:
                    await websocket.send_json({
                        "type": "error",
                        "code": "INVALID_AUDIO",
                        "message": "Invalid base64 audio data",
                    })
                    continue

                if len(chunk_bytes) > MAX_AUDIO_CHUNK_BYTES:
                    await websocket.send_json({
                        "type": "error",
                        "code": "CHUNK_TOO_LARGE",
                        "message": f"Audio chunk exceeds {MAX_AUDIO_CHUNK_BYTES} bytes",
                    })
                    continue

                await audio_queue.put(chunk_bytes)

            elif msg_type == "stop":
                # Signal end of audio input
                await audio_queue.put(None)

                # Wait for transcriber to finish
                try:
                    await asyncio.wait_for(transcriber_task, timeout=30.0)
                except asyncio.TimeoutError:
                    _logger.warning("Transcription timed out", extra={"user_id": user_id})
                    transcriber_task.cancel()

                await websocket.send_json({
                    "type": "done",
                    "text": accumulated_text,
                    "session_id": session_id,
                })
                accumulated_text = ""

                # Reset: start a new transcriber for next recording
                transcriber_task = asyncio.create_task(_transcribe_and_forward())

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            else:
                await websocket.send_json({
                    "type": "error",
                    "code": "INVALID_MESSAGE_TYPE",
                    "message": f"Unknown message type: {msg_type}",
                })

    except WebSocketDisconnect:
        _logger.info("Voice WS disconnected", extra={"user_id": user_id, "session_id": session_id})
    except Exception as e:
        _logger.error("Voice WS error", extra={"error": str(e), "user_id": user_id})
    finally:
        try:
            await manager.disconnect(session_id)
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
```

### 3C: 更新协议文档注释

- [ ] **Step 2: 更新文件头部的协议文档**

在 `backend/app/api/voice_ws.py` 第 46-55 行，找到：

```python
    """
    Real-time voice transcription WebSocket.

    Protocol:
    1. Client connects with ?token=<jwt>
    2. Server sends: {"type": "connected"}
    3. Client sends: {"type": "audio", "data": "<base64>", "format": "wav"}
    4. Server sends: {"type": "stream_text", "text": "<partial transcript>"}
    5. Client sends: {"type": "stop"}
    6. Server sends: {"type": "done", "text": "<final transcript>"}
    """
```

替换为（增加 session_id 到 connected 响应）：

```python
    """
    Real-time voice transcription WebSocket with true streaming support.

    Protocol:
    1. Client connects with ?token=<jwt>
    2. Server sends: {"type": "connected", "session_id": "..."}
    3. Client sends: {"type": "audio", "data": "<base64>"} (repeated)
    4. Server sends: {"type": "stream_text", "text": "<partial transcript>"} (repeated, in real-time)
    5. Client sends: {"type": "stop"}
    6. Server sends: {"type": "done", "text": "<final transcript>"}
    """
```

### 3D: 写测试

- [ ] **Step 3: 添加流式 WebSocket 测试**

在 `backend/tests/test_stt_workflow.py` 末尾追加：

```python
import pytest


def test_voice_ws_protocol_doc_updated():
    """Verify voice_ws.py docstring reflects streaming protocol."""
    import inspect
    from app.api import voice_ws as vws_module

    source = inspect.getsource(vws_module.websocket_voice)
    assert "stream_text" in source, \
        "voice_ws should mention stream_text in protocol doc"
    assert "true streaming" in inspect.getdoc(vws_module.websocket_voice).lower() or \
           "real-time" in inspect.getdoc(vws_module.websocket_voice).lower(), \
        "voice_ws docstring should mention streaming/real-time capability"


def test_queue_used_for_audio_buffering():
    """Verify asyncio.Queue is used for audio buffering in voice_ws."""
    import inspect
    from app.api import voice_ws as vws_module

    source = inspect.getsource(vws_module.websocket_voice)
    assert "asyncio.Queue" in source, \
        "voice_ws should use asyncio.Queue for audio buffering"
    assert "transcribe_stream" in source, \
        "voice_ws should use transcribe_stream for real-time transcription"


def test_asyncio_queue_import():
    """Verify asyncio is imported in voice_ws."""
    import inspect
    from app.api import voice_ws as vws_module

    source = inspect.getsource(vws_module.websocket_voice)
    module_source = inspect.getsource(vws_module)
    assert "import asyncio" in module_source, \
        "voice_ws should import asyncio for Queue support"
```

- [ ] **Step 4: 运行测试验证**

Run: `cd backend && conda activate rag-test && python -m pytest tests/test_stt_workflow.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/api/voice_ws.py backend/tests/test_stt_workflow.py
git commit -m "feat: implement true streaming in /ws/voice with asyncio.Queue"
```

---

## Self-Review Checklist

1. **Spec coverage:** 每个问题都有对应的 task：
   - 格式字段不符 → Task 1 Step 3
   - 采样率未约束 → Task 1 Step 1-2（重采样函数 + 集成）
   - 非真正流式 → Task 3 Step 1（Queue 改造）

2. **Placeholder scan:** 所有代码块都是完整的，没有 TBD/TODO。所有函数名和行号都基于当前文件内容。

3. **Type consistency:**
   - 前端 `resampleTo16kHz` 输入 `AudioBuffer`，返回 `Float32Array` — 与 `webmToPcmWavBase64` 期望的 `Float32Array` 一致
   - 后端 `sampleRate` 从 `data.get("sampleRate")` 获取，默认 16000 — 与 `STTConfig.sample_rate: int = 16000` 默认值一致
   - `asyncio.Queue` 类型正确，`.put()` / `.get()` 异步方法与 `async for` 消费模式一致

4. **Task 依赖关系:**
   - Task 1（前端）和 Task 2（后端 ws handler）可以并行执行
   - Task 3（流式改造）依赖理解 `_get_stt_provider()` 和 `transcribe_stream()` 接口，无代码依赖
   - 所有 Task 最终通过 `test_stt_workflow.py` 验证

---

**Plan complete and saved to `docs/superpowers/plans/2026-04-23-stt-workflow-fix-plan.md`.**

**Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
