import asyncio
import aiomysql

async def cleanup():
    pool = await aiomysql.create_pool(
        host='mysql', port=3306, user='root', password='rag2026',
        db='rag_customer_service', charset='utf8mb4'
    )
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM documents")
            await conn.commit()
            await cur.execute("SELECT COUNT(*) FROM documents")
            cnt = await cur.fetchone()
            print(f"documents 表已清空，当前记录数: {cnt[0]}")
    pool.close()
    await pool.wait_closed()

asyncio.run(cleanup())
