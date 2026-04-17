"""
深入分析 product_sku 和 product_spu 表
找出可用于匹配的最佳字段，优化数据限制
"""
import asyncio
import os

os.environ["MYSQL_HOST"] = "localhost"
os.environ["MYSQL_PORT"] = "3306"
os.environ["MYSQL_USER"] = "root"
os.environ["MYSQL_PASSWORD"] = "123456"
os.environ["MYSQL_DATABASE"] = "lumapepe"

import aiomysql
from aiomysql import DictCursor


async def analyze_tables():
    pool = await aiomysql.create_pool(
        host=os.getenv("MYSQL_HOST"),
        port=int(os.getenv("MYSQL_PORT")),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        db=os.getenv("MYSQL_DATABASE"),
        charset="utf8mb4",
        minsize=1, maxsize=3,
    )
    
    async with pool.acquire() as conn:
        async with conn.cursor(DictCursor) as cur:
            
            print("=" * 70)
            print("【深入分析】product_sku 和 product_spu 表")
            print("=" * 70)
            
            # 1. 分析 SKU 表 - 找出有数据的字段
            print("\n[1] SKU 表字段数据分析")
            print("-" * 50)
            
            await cur.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN bar_code IS NOT NULL AND bar_code != '' THEN 1 ELSE 0 END) as has_barcode,
                    SUM(CASE WHEN supply_chain_code IS NOT NULL AND supply_chain_code != '' THEN 1 ELSE 0 END) as has_supply_code,
                    SUM(CASE WHEN supply_external_sku IS NOT NULL AND supply_external_sku != '' THEN 1 ELSE 0 END) as has_external_sku,
                    SUM(CASE WHEN properties IS NOT NULL AND properties != '' THEN 1 ELSE 0 END) as has_properties,
                    SUM(CASE WHEN pic_url IS NOT NULL AND pic_url != '' THEN 1 ELSE 0 END) as has_pic_url
                FROM product_sku
            """)
            stats = await cur.fetchone()
            print(f"总记录数: {stats['total']}")
            print(f"  bar_code 非空: {stats['has_barcode']} ({100*stats['has_barcode']/stats['total']:.1f}%)")
            print(f"  supply_chain_code 非空: {stats['has_supply_code']} ({100*stats['has_supply_code']/stats['total']:.1f}%)")
            print(f"  supply_external_sku 非空: {stats['has_external_sku']} ({100*stats['has_external_sku']/stats['total']:.1f}%)")
            print(f"  properties 非空: {stats['has_properties']} ({100*stats['has_properties']/stats['total']:.1f}%)")
            print(f"  pic_url 非空: {stats['has_pic_url']} ({100*stats['has_pic_url']/stats['total']:.1f}%)")
            
            # 2. 分析 SPU 表字段
            print("\n[2] SPU 表字段数据分析")
            print("-" * 50)
            
            await cur.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN name IS NOT NULL AND name != '' THEN 1 ELSE 0 END) as has_name,
                    SUM(CASE WHEN keyword IS NOT NULL AND keyword != '' THEN 1 ELSE 0 END) as has_keyword,
                    SUM(CASE WHEN introduction IS NOT NULL AND introduction != '' THEN 1 ELSE 0 END) as has_intro,
                    SUM(CASE WHEN description IS NOT NULL AND description != '' THEN 1 ELSE 0 END) as has_desc,
                    SUM(CASE WHEN supply_chain_code IS NOT NULL AND supply_chain_code != '' THEN 1 ELSE 0 END) as has_supply_code,
                    SUM(CASE WHEN supply_external_sku IS NOT NULL AND supply_external_sku != '' THEN 1 ELSE 0 END) as has_external_sku
                FROM product_spu
            """)
            stats = await cur.fetchone()
            print(f"总记录数: {stats['total']}")
            print(f"  name 非空: {stats['has_name']} ({100*stats['has_name']/stats['total']:.1f}%)")
            print(f"  keyword 非空: {stats['has_keyword']} ({100*stats['has_keyword']/stats['total']:.1f}%)")
            print(f"  introduction 非空: {stats['has_intro']} ({100*stats['has_intro']/stats['total']:.1f}%)")
            print(f"  description 非空: {stats['has_desc']} ({100*stats['has_desc']/stats['total']:.1f}%)")
            print(f"  supply_chain_code 非空: {stats['has_supply_code']} ({100*stats['has_supply_code']/stats['total']:.1f}%)")
            print(f"  supply_external_sku 非空: {stats['has_external_sku']} ({100*stats['has_external_sku']/stats['total']:.1f}%)")
            
            # 3. 采样分析 supply_chain_code 和 supply_external_sku
            print("\n[3] 供应链字段采样")
            print("-" * 50)
            
            await cur.execute("""
                SELECT DISTINCT supply_chain_code 
                FROM product_spu 
                WHERE supply_chain_code IS NOT NULL AND supply_chain_code != ''
                LIMIT 10
            """)
            codes = await cur.fetchall()
            print("supply_chain_code 值:")
            for c in codes:
                print(f"  - {c['supply_chain_code']}")
            
            await cur.execute("""
                SELECT DISTINCT supply_external_sku 
                FROM product_spu 
                WHERE supply_external_sku IS NOT NULL AND supply_external_sku != ''
                LIMIT 5
            """)
            external = await cur.fetchall()
            print("\nsupply_external_sku 样例:")
            for e in external:
                print(f"  - {e['supply_external_sku']}")
            
            # 4. 分析 properties JSON 结构
            print("\n[4] SKU properties JSON 结构分析")
            print("-" * 50)
            
            await cur.execute("""
                SELECT properties 
                FROM product_sku 
                WHERE properties IS NOT NULL AND properties != '' AND properties != '[]'
                LIMIT 3
            """)
            props = await cur.fetchall()
            for i, p in enumerate(props):
                print(f"样例 {i+1}: {p['properties'][:200]}...")
            
            # 5. 分析 keyword 分隔方式
            print("\n[5] SPU keyword 分隔方式分析")
            print("-" * 50)
            
            await cur.execute("""
                SELECT keyword 
                FROM product_spu 
                WHERE keyword IS NOT NULL AND keyword != ''
                LIMIT 10
            """)
            keywords = await cur.fetchall()
            for k in keywords:
                kw = k['keyword']
                # 统计分隔符
                comma_count = kw.count(',')
                space_count = kw.count(' ')
                and_count = kw.count('&')
                print(f"  [逗号:{comma_count}, 空格:{space_count}, &:{and_count}] {kw[:80]}...")
            
            # 6. 检查是否有其他别名表
            print("\n[6] 检查可能的别名相关表")
            print("-" * 50)
            
            await cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'lumapepe' 
                AND (table_name LIKE '%alias%' OR table_name LIKE '%name%' OR table_name LIKE '%tag%')
            """)
            tables = await cur.fetchall()
            if tables:
                for t in tables:
                    print(f"  找到: {t['table_name']}")
            else:
                print("  未找到别名相关表")
            
            # 7. 建议优化方案
            print("\n" + "=" * 70)
            print("【优化建议】")
            print("=" * 70)
            
            print("""
当前匹配限制分析:

1. bar_code (SKU) - 当前全为 NULL
   建议: 使用 supply_chain_code + supply_external_sku 作为替代
   - supply_chain_code: 供应商代码 (gigab2b)
   - supply_external_sku: 外部SKU编号 (如 W1578P447622)

2. product_alias 表不存在
   建议A: 从 SPU.keyword 自动提取常用词作为别名
   建议B: 从 SPU.introduction 提取关键词作为别名
   建议C: 利用 properties 中的 Main Color/Main Material 等作为变体别名

3. 匹配策略优化
   当前: fuzz.ratio (严格匹配)
   建议: fuzz.partial_ratio + token_sort_ratio 组合

4. 关键词提取优化
   当前: 按逗号分隔 keyword
   发现: 关键词中含大量 & 和空格，可进一步拆分
            """)
    
    pool.close()
    await pool.wait_closed()


asyncio.run(analyze_tables())
