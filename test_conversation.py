import asyncio
import websockets
import json

async def test():
    import httpx
    r = await httpx.AsyncClient().post(
        'http://localhost:8000/api/v1/debug/auth/token',
        json={'username': 'admin', 'password': 'admin123', 'user_id': 'test'}
    )
    token = r.json().get('token')

    uri = 'ws://localhost:8000/ws/chat?token=' + token
    async with websockets.connect(uri) as ws:
        await ws.recv()  # connected

        # 第1轮：你好
        print('=== Round 1: 你好 ===')
        await ws.send(json.dumps({'type': 'chat', 'content': '你好', 'user_id': 'test-user'}))
        r1_session = None
        r1_content = ''
        while True:
            msg = await ws.recv()
            data = json.loads(msg)
            t = data.get('type')
            if t == 'message_received':
                r1_session = data.get('session_id')
                print('Session ID:', r1_session)
            elif t == 'chunk':
                r1_content += data.get('content', '')
            elif t == 'done':
                print('Response:', r1_content[:150])
                break

        # 第2轮：退货政策是什么
        print()
        print('=== Round 2: 退货政策是什么 ===')
        print('Session ID:', r1_session)
        await ws.send(json.dumps({'type': 'chat', 'content': '退货政策是什么', 'session_id': r1_session, 'user_id': 'test-user'}))
        r2_content = ''
        while True:
            msg = await ws.recv()
            data = json.loads(msg)
            t = data.get('type')
            if t == 'chunk':
                r2_content += data.get('content', '')
            elif t == 'done':
                print('Response:', r2_content[:300])
                break

asyncio.run(test())
