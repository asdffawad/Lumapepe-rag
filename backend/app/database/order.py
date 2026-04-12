"""
orders 表 CRUD 操作（aiomysql 版本）
"""
from __future__ import annotations

import json as json_module
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import aiomysql


@dataclass
class OrderRecord:
    order_id: str
    user_id: str
    product_name: str
    product_id: Optional[str]
    quantity: int
    total_price: float
    status: str
    payment_method: Optional[str]
    address: Optional[str]
    logistics_no: Optional[str]
    logistics_company: Optional[str]
    created_at: datetime
    paid_at: Optional[datetime]
    shipped_at: Optional[datetime]
    delivered_at: Optional[datetime]

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "user_id": self.user_id,
            "product_name": self.product_name,
            "product_id": self.product_id,
            "quantity": self.quantity,
            "total_price": self.total_price,
            "status": self.status,
            "payment_method": self.payment_method,
            "address": self.address,
            "logistics_no": self.logistics_no,
            "logistics_company": self.logistics_company,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
            "shipped_at": self.shipped_at.isoformat() if self.shipped_at else None,
            "delivered_at": self.delivered_at.isoformat() if self.delivered_at else None,
        }

    @property
    def status_text(self) -> str:
        mapping = {
            "pending": "待支付",
            "paid": "已支付",
            "shipped": "配送中",
            "delivered": "已完成",
            "cancelled": "已取消",
            "refunding": "退款中",
            "refunded": "已退款",
        }
        return mapping.get(self.status, self.status)


class OrderRepository:
    def __init__(self, pool: aiomysql.Pool):
        self.pool = pool

    def _row_to_record(self, row: dict) -> OrderRecord:
        return OrderRecord(
            order_id=row["order_id"],
            user_id=row["user_id"],
            product_name=row["product_name"],
            product_id=row.get("product_id"),
            quantity=row.get("quantity", 1),
            total_price=float(row["total_price"]),
            status=row["status"],
            payment_method=row.get("payment_method"),
            address=row.get("address"),
            logistics_no=row.get("logistics_no"),
            logistics_company=row.get("logistics_company"),
            created_at=row["created_at"],
            paid_at=row.get("paid_at"),
            shipped_at=row.get("shipped_at"),
            delivered_at=row.get("delivered_at"),
        )

    async def find_by_user(
        self, user_id: str, limit: int = 10
    ) -> list[OrderRecord]:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT * FROM orders WHERE user_id=%s ORDER BY created_at DESC LIMIT %s",
                    (user_id, limit),
                )
                rows = await cur.fetchall()
                return [self._row_to_record(r) for r in rows]

    async def find_by_order_id(self, order_id: str) -> Optional[OrderRecord]:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT * FROM orders WHERE order_id=%s", (order_id,)
                )
                row = await cur.fetchone()
                return self._row_to_record(row) if row else None

    async def find_by_logistics(self, logistics_no: str) -> Optional[OrderRecord]:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT * FROM orders WHERE logistics_no=%s", (logistics_no,)
                )
                row = await cur.fetchone()
                return self._row_to_record(row) if row else None

    async def update_status(self, order_id: str, status: str) -> bool:
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE orders SET status=%s WHERE order_id=%s",
                    (status, order_id),
                )
                return cur.rowcount > 0

    async def count_by_user(self, user_id: str) -> int:
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT COUNT(*) as cnt FROM orders WHERE user_id=%s", (user_id,)
                )
                row = await cur.fetchone()
                return int(row[0]) if row else 0
