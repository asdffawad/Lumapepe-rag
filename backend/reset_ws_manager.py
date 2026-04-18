#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""重置 WebSocket 连接管理器（解决连接数超限问题）"""
import sys
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "backend"))

def reset_ws_manager():
    """重置连接管理器单例"""
    print("=" * 60)
    print("WebSocket 连接管理器重置工具")
    print("=" * 60)

    try:
        from app.api.ws_manager import reset_connection_manager
        from app.api.websocket import get_rag_orchestrator, get_intent_classifier

        # 重置单例
        reset_connection_manager()
        print("\n✓ 连接管理器已重置")
        print("  所有活跃连接计数已清零")
        print("\n建议操作:")
        print("  1. 重启后端服务（确保完全清理）")
        print("  2. 刷新前端页面重新连接")
        print("\n或者，如果你添加了 admin 接口，可以调用:")
        print("  POST /api/v1/admin/ws/connections/all/clear")

    except Exception as e:
        print(f"\n✗ 重置失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    reset_ws_manager()
