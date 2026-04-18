# -*- coding: utf-8 -*-
"""测试商品上下文传递功能"""
import asyncio
import json
import websockets
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "backend"))


async def test_product_context():
    """测试商品上下文传递"""
    print("=" * 60)
    print("[TEST] 商品上下文传递测试")
    print("=" * 60)

    # WebSocket连接配置
    ws_url = "ws://localhost:8000/ws/chat"

    # 模拟JWT token (需要从实际登录获取)
    token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiMTIzNDUiLCJleHAiOjE3MjgxMjM2MDB9.mock_token"

    # 测试场景1: 通过SKU ID连接
    print("\n[场景1] 用户浏览SKU=753商品详情页，点击AI���服")
    sku_id = 753
    spu_id = None

    ws_uri = f"{ws_url}?token={token}&sku_id={sku_id}" if sku_id else ws_url
    if spu_id:
        ws_uri += f"&spu_id={spu_id}"

    try:
        async with websockets.connect(ws_uri) as websocket:
            print(f"  [OK] WebSocket连接成功 (sku_id={sku_id})")

            # 接收连接消息（应包含商品上下文）
            connected_msg = await websocket.recv()
            msg = json.loads(connected_msg)
            print(f"  [消息] {msg.get('type')}")

            if "product_context" in msg:
                pc = msg["product_context"]
                print(f"  ✓ 商品上下文已注入:")
                print(f"    - 商品名称: {pc.get('product_name')}")
                print(f"    - 商品类型: {pc.get('product_type')}")
                print(f"    - 价格: ${pc.get('price')}" if pc.get('price') else "    - 价格: 未知")
                print(f"    - SKU ID: {pc.get('sku_id')}")
                print(f"    - SPU ID: {pc.get('spu_id')}")
            else:
                print(f"  ✗ 未收到商品上下文")

            # 发送测试消息
            test_message = "这个商品有哪些特点？"
            print(f"\n  [发送] 用户消息: '{test_message}'")
            await websocket.send_json({
                "type": "message",
                "content": test_message
            })

            # 接收AI回复
            full_response = ""
            while True:
                response = await asyncio.wait_for(websocket.recv(), timeout=10)
                data = json.loads(response)

                if data.get("type") == "chunk":
                    chunk = data.get("content", "")
                    full_response += chunk
                    print(f"  [流式] {chunk}", end="", flush=True)
                elif data.get("type") == "done":
                    print(f"\n  [完成] 消息接收完毕")
                    break
                elif data.get("type") == "error":
                    print(f"\n  [错误] {data.get('message')}")
                    break

            print(f"\n  [回复长度] {len(full_response)} 字符")

    except Exception as e:
        print(f"  [ERROR] 连接失败: {e}")
        import traceback
        traceback.print_exc()


async def test_without_product_context():
    """测试无商品上下文的情况"""
    print("\n" + "=" * 60)
    print("[场景2] 普��对话（无商品上下文）")
    print("=" * 60)

    token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiMTIzNDUiLCJleHAiOjE3MjgxMjM2MDB9.mock_token"
    ws_url = f"ws://localhost:8000/ws/chat?token={token}"

    try:
        async with websockets.connect(ws_url) as websocket:
            print("  [OK] WebSocket连接成功")

            # 接收连接消息
            connected_msg = await websocket.recv()
            msg = json.loads(connected_msg)
            print(f"  [消息] {msg.get('type')}")

            if "product_context" in msg:
                print(f"  ⚠ 意外: 收到商品上下文 (应该是普通对话)")
            else:
                print(f"  ✓ 正常: 无商品上下文")

            # 发送测试消息
            test_message = "你好"
            print(f"\n  [发送] 用户消息: '{test_message}'")
            await websocket.send_json({
                "type": "message",
                "content": test_message
            })

            # 接收回复
            full_response = ""
            while True:
                response = await asyncio.wait_for(websocket.recv(), timeout=10)
                data = json.loads(response)

                if data.get("type") == "chunk":
                    chunk = data.get("content", "")
                    full_response += chunk
                    print(f"  [流式] {chunk}", end="", flush=True)
                elif data.get("type") == "done":
                    print(f"\n  [完成] 消息接收完毕")
                    break
                elif data.get("type") == "error":
                    print(f"\n  [错误] {data.get('message')}")
                    break

    except Exception as e:
        print(f"  [ERROR] {e}")


async def main():
    print("\n" + "=" * 60)
    print("🛒 商品上下文传递功能测试")
    print("=" * 60)
    print("\n说明：当用户从商品详情页点击AI客服时，")
    print("系统应自动传递商品ID，并在对话中注入商品信息。")

    # 场景1：带商品上下文
    await test_product_context()

    # 场景2：普通对话
    await test_without_product_context()

    print("\n" + "=" * 60)
    print("[OK] 测试完成!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
