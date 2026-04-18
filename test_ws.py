"""Test WebSocket connection to verify fixes"""
import asyncio
import websockets
import json

async def test_ws():
    uri = "ws://localhost:8000/ws/chat?token=eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJzdWIiOiAidXNlci1sb2JjZDQxYyIsICJyb2xlIjogInVzZXIiLCAiaWF0IjogMTc3NjQ4MzkxMywgImV4cCI6IDE3NzY1NzAzMTN9.cO__RNEEzYC9jKCa43vzQzuSGl_vAgYZEbdZVK8-s4A&sku_id=209"
    print(f'Connecting to {uri}')

    try:
        async with websockets.connect(uri) as websocket:
            print("Connected!")

            # Wait for connected message
            msg = await websocket.recv()
            data = json.loads(msg)
            print(f"Received: {data.get('type')}")
            if data.get('type') == 'connected':
                print(f"✅ Session ID: {data.get('session_id')}")
                print(f"✅ Product context: {data.get('product_context', 'None')}")
                return True
    except Exception as e:
        print(f"❌ Error: {type(e).__name__}: {e}")
        return False

if __name__ == '__main__':
    result = asyncio.run(test_ws())
    exit(0 if result else 1)
