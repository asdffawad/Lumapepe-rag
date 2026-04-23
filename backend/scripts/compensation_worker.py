#!/usr/bin/env python3
"""
补偿队列处理器
处理 WebSocket 消息保存失败的情况

功能：
- 从 Redis 队列中读取保存失败的消息
- 重新尝试保存到 MySQL
- 失败消息转移到死信队列
"""
import asyncio
import json
import sys
import os
from datetime import datetime

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database.connection import create_db_pool
from app.cache.redis_client import create_redis_pool, get_redis_client
from app.config import get_config


async def process_compensation():
    """处理补偿队列"""
    config = get_config()

    # 连接 Redis
    await create_redis_pool(config.redis.url)
    redis_client = get_redis_client()

    # 连接 MySQL
    db_pool = await create_db_pool(config.db)

    queue_key = "compensation:messages"
    dead_letter_key = "compensation:messages:dlq"
    max_retries = 3

    print(f"[补偿处理器] 启动，队列: {queue_key}")

    processed = 0
    failed = 0

    while True:
        try:
            # 从队列获取消息
            data = await redis_client.lpop(queue_key)
            if data is None:
                await asyncio.sleep(1)
                continue

            try:
                msg = json.loads(data)
                session_id = msg.get("session_id", "")
                role = msg.get("role", "")
                content = msg.get("content", "")
                intent = msg.get("intent")
                citations = msg.get("citations", [])
                created_at = msg.get("created_at", 0)

                print(f"[补偿处理] session={session_id}, role={role}, len={len(content)}")

                # 重新尝试保存
                async with db_pool.acquire() as conn:
                    async with conn.cursor() as cursor:
                        if role == "user":
                            await cursor.execute(
                                """INSERT INTO chat_messages
                                   (session_id, role, content, intent, created_at)
                                   VALUES (%s, %s, %s, %s, %s)""",
                                (session_id, role, content, intent,
                                 datetime.fromtimestamp(created_at))
                            )
                        elif role == "assistant":
                            sources = json.dumps({"sources": citations}) if citations else None
                            await cursor.execute(
                                """INSERT INTO chat_messages
                                   (session_id, role, content, intent, sources, created_at)
                                   VALUES (%s, %s, %s, %s, %s, %s)""",
                                (session_id, role, content, intent, sources,
                                 datetime.fromtimestamp(created_at))
                            )

                await conn.commit()
                processed += 1
                print(f"[补偿处理] 成功 session={session_id}")

            except json.JSONDecodeError as e:
                print(f"[补偿处理] JSON 解析失败: {e}, 转移到死信队列")
                await redis_client.rpush(dead_letter_key, data)

            except Exception as e:
                print(f"[补偿处理] 保存失败: {e}")
                failed += 1

                # 重新加入队列（带重试计数）
                retry_count = msg.get("_retry_count", 0) + 1
                if retry_count < max_retries:
                    msg["_retry_count"] = retry_count
                    await redis_client.rpush(queue_key, json.dumps(msg))
                    print(f"[补偿处理] 重试 {retry_count}/{max_retries}")
                else:
                    # 转移到死信队列
                    msg["_failed_at"] = datetime.now().isoformat()
                    msg["_error"] = str(e)
                    await redis_client.rpush(dead_letter_key, json.dumps(msg))
                    print(f"[补偿处理] 超过重试次数，转移到死信队列")

        except asyncio.CancelledError:
            print("[补偿处理器] 关闭")
            break
        except Exception as e:
            print(f"[补偿处理器] 错误: {e}")
            await asyncio.sleep(5)

    # 清理
    await db_pool.close()
    print(f"[补偿处理器] 完成，成功: {processed}, 失败: {failed}")


def main():
    asyncio.run(process_compensation())


if __name__ == "__main__":
    main()
