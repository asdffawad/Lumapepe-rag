#!/usr/bin/env python
"""在容器内直接测试"""
import pymysql

conn = pymysql.connect(
    host='localhost',
    port=3307,
    user='root',
    password='rag2026',
    database='rag_customer_service',
    charset='utf8mb4'
)

try:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.id, s.spu_id, p.name
            FROM product_sku s
            LEFT JOIN product_spu p ON s.spu_id = p.id
            WHERE s.id = 209
        """)
        row = cur.fetchone()
        print(f"查询结果: {row}")
        if row:
            print(f"SKU: {row[0]}, SPU: {row[1]}, Name: {row[2][:50]}")
finally:
    conn.close()
