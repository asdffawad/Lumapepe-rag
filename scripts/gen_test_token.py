#!/usr/bin/env python3
"""
本地测试辅助脚本：生成 JWT Token

使用方法：
  python scripts/gen_test_token.py                    # 生成普通用户 token
  python scripts/gen_test_token.py --admin            # 生成管理员 token
  python scripts/gen_test_token.py --user-id user-123  # 自定义用户 ID

然后用生成的 token 调用 API：
  curl -H "Authorization: Bearer <token>" http://localhost:8000/api/v1/products/search ...
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

# 尝试从 .env 加载 JWT_SECRET
def _load_env_jwt_secret() -> str:
    """从项目根目录的 .env 文件读取 JWT_SECRET"""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("JWT_SECRET="):
                    secret = line.split("=", 1)[1].strip()
                    if secret and secret not in ("your_jwt_secret_here", ""):
                        return secret
    return ""


def create_token(user_id: str, role: str = "user", ttl_seconds: int = 86400) -> str:
    """
    生成 JWT Token（与 backend/app/middleware/auth.py 保持一致）
    """
    jwt_secret = _load_env_jwt_secret()
    if not jwt_secret:
        print("[ERROR] 无法获取 JWT_SECRET", file=sys.stderr)
        print("  请确保 .env 文件中配置了 JWT_SECRET", file=sys.stderr)
        sys.exit(1)

    algorithm = "HS256"
    header = {"alg": algorithm, "typ": "JWT"}
    payload = {
        "sub": user_id,
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + ttl_seconds,
    }

    def b64(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    header_b64 = b64(header)
    payload_b64 = b64(payload)
    sign_input = f"{header_b64}.{payload_b64}"

    signature = hmac.new(
        jwt_secret.encode(), sign_input.encode(), hashlib.sha256
    ).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).decode().rstrip("=")

    return f"{sign_input}.{sig_b64}"


def main():
    parser = argparse.ArgumentParser(description="生成测试用 JWT Token")
    parser.add_argument("--user-id", "-u", default="test-user-001", help="用户 ID")
    parser.add_argument("--admin", "-a", action="store_true", help="生成管理员 token")
    parser.add_argument("--role", "-r", default=None, help="自定义角色")
    parser.add_argument("--ttl", "-t", type=int, default=86400, help="有效期秒数（默认 86400=24h）")
    args = parser.parse_args()

    role = args.role or ("admin" if args.admin else "user")
    token = create_token(user_id=args.user_id, role=role, ttl_seconds=args.ttl)

    print("=" * 60)
    print(f"用户ID : {args.user_id}")
    print(f"角色   : {role}")
    print(f"有效期 : {args.ttl} 秒")
    print("=" * 60)
    print(f"Bearer Token:")
    print(token)
    print("=" * 60)
    print()
    print("测试命令示例（普通用户）:")
    print(f'  curl -H "Authorization: Bearer {token}" \\')
    print('    http://localhost:8000/api/v1/products/search \\')
    print('    -H "Content-Type: application/json" \\')
    print('    -d \'{"query":"iPhone 16"}\'')
    print()
    if args.admin or role == "admin":
        print("测试命令示例（管理员）:")
        print(f'  curl -H "Authorization: Bearer {token}" \\')
        print('    http://localhost:8000/api/v1/admin/documents')
    print()


if __name__ == "__main__":
    main()
