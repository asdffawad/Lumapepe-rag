#!/usr/bin/env python3
"""
RAG 知识库数据摄取脚本
一键完成：JSON读取 → 分块 → 向量化 → Milvus入库 + MySQL元数据写入

使用方式:
    # 在 backend 目录下运行（需要先启动 MySQL/Redis）
    cd backend
    python -m scripts.ingest_data

    # 或从项目根目录
    PYTHONPATH=backend python backend/scripts/ingest_data.py

    # 指定 Milvus 主机
    python -m scripts.ingest_data --milvus-host 192.168.1.100

    # 指定 JSON 数据目录
    python -m scripts.ingest_data --data-dir ./data

    # 清除现有数据重新摄取
    python -m scripts.ingest_data --reset
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

# --------------------------------------------------------------------------- #
# 路径设置: 支持直接运行和模块导入两种方式
# --------------------------------------------------------------------------- #
SCRIPT_DIR = Path(__file__).resolve().parent           # scripts/
BACKEND_DIR = SCRIPT_DIR.parent / "backend"             # backend/
PROJECT_DIR = BACKEND_DIR.parent                       # 项目根目录

# 尝试添加路径
for _p in [str(BACKEND_DIR), str(PROJECT_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# --------------------------------------------------------------------------- #
# 依赖导入（延迟导入，避免路径问题）
# --------------------------------------------------------------------------- #


def _parse_file(file_path: str) -> _ParsedFileResult:
    """根据文件扩展名解析富文本文件。"""
    ext = "." + file_path.rsplit(".", 1)[-1].lower()
    file_stem = Path(file_path).stem  # 跨平台获取文件名（不含扩展名）

    if ext == ".pdf":
        try:
            import pdfplumber
        except ImportError:
            raise RuntimeError("pdfplumber 未安装：pip install pdfplumber")
        import re

        title = ""
        lines = []
        with pdfplumber.open(file_path) as pdf:
            if pdf.metadata:
                title = pdf.metadata.get("Title", "") or ""
            for page in pdf.pages:
                text = page.extract_text() or ""
                if text.strip():
                    lines.append(text)
        content = re.sub(r"[ \t]+", " ", "\n".join(lines)).strip()
        markets, lang = _infer_file_metadata(file_path, content)
        return _ParsedFileResult(title or file_stem, content, markets, lang)

    elif ext in (".html", ".htm"):
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            raise RuntimeError("beautifulsoup4 未安装：pip install beautifulsoup4 lxml")
        import re

        with open(file_path, encoding="utf-8", errors="ignore") as f:
            html = f.read()
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        title = soup.title.string.strip() if soup.title else file_stem
        text = soup.get_text(separator="\n", strip=True)
        content = re.sub(r"[ \t]+", " ", text).strip()
        markets, lang = _infer_file_metadata(file_path, content)
        return _ParsedFileResult(title or file_stem, content, markets, lang)

    elif ext == ".docx":
        try:
            from docx import Document
        except ImportError:
            raise RuntimeError("python-docx 未安装：pip install python-docx")
        import re

        doc = Document(file_path)
        title = ""
        lines = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if not title and (para.style.name.startswith("Heading") or para.style.name == "Title"):
                title = text
            if text:
                lines.append(text)
        content = re.sub(r"[ \t]+", " ", "\n".join(lines)).strip()
        markets, lang = _infer_file_metadata(file_path, content)
        # 使用 Path.stem 避免 Windows 路径兼容性问题
        fallback_title = Path(file_path).stem
        return _ParsedFileResult(title or fallback_title, content, markets, lang)

    else:
        raise ValueError(f"Unsupported file type: {ext}")


def _infer_file_metadata(file_path: str, content: str = "") -> tuple[list[str], str]:
    """从路径和内容推断 markets 和 lang（不引入外部依赖）"""
    import re

    MARKET_KW = {
        "CN": ["中国", "cn", "境内", "国内", "中文"],
        "VN": ["越南", "vietnam", "vn", "tiếng Việt", "Việt Nam", "越文"],
        "US": ["美国", "us", "english"],
    }

    combined = file_path + " " + (content[:500] if content else "")
    matched: set[str] = set()
    for market, keywords in MARKET_KW.items():
        if any(kw.lower() in combined.lower() for kw in keywords):
            matched.add(market)

    if not matched:
        matched.add("CN")

    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", content or ""))
    total_chars = max(len((content or "").replace(" ", "")), 1)
    zh_ratio = chinese_chars / total_chars

    if "Vietnam" in (content or "") or "tiếng Việt" in (content or "") or "Việt Nam" in (content or ""):
        lang = "vi"
    elif "English" in (content or "") and zh_ratio < 0.1:
        lang = "en"
    elif zh_ratio > 0.3:
        lang = "zh"
    else:
        lang = "en"

    return list(matched), lang


def _parse_file_safe(file_path: str) -> _ParsedFileResult | None:
    """
    安全解析文件：任意 parser 失败都不炸 pipeline。
    两级 fallback:
      1. 纯文本读取（绕过格式解析）
      2. 记录 skip 并继续
    """
    try:
        return _parse_file(file_path)
    except Exception:
        pass

    # Fallback 1: 尝试纯文本读取
    try:
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            raw = f.read()
        if "\x00" in raw[:100]:
            print(f"  [FALLBACK-SKIP] {file_path}: 二进制文件，无法解析")
            return None
        return _ParsedFileResult(
            title=Path(file_path).stem,
            content=raw[:10000],
            markets=["CN"],
            lang="zh",
        )
    except Exception:
        pass

    # Fallback 2: 记录错误但继续
    print(f"  [FALLBACK-SKIP] {file_path}: 解析失败")
    return None


class _ParsedFileResult:
    """内部用：文件解析结果（含 markets 和 lang）"""
    def __init__(self, title: str, content: str, markets: list[str] | None = None, lang: str = ""):
        self.title = title
        self.content = content
        self.markets = markets or ["CN"]
        self.lang = lang


async def run_ingestion(
    data_dir: str,
    milvus_host: str,
    milvus_port: int,
    chunk_size: int,
    chunk_overlap: int,
    batch_size: int,
    reset: bool,
    mysql_host: str,
    mysql_port: int,
    mysql_user: str,
    mysql_password: str,
    mysql_database: str,
    redis_url: str,
    dashscope_api_key: str,
    llm_base_url: str,
):
    """异步执行完整摄取流程"""
    import aiomysql
    import httpx
    from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility

    from app.utils.chunker import Chunk, TextChunker

    # =========================================================================
    # 1. 初始化 Milvus 连接
    # =========================================================================
    alias = "default"
    print(f"\n[1/6] 初始化 Milvus ({milvus_host}:{milvus_port})...")
    connections.connect(alias=alias, host=milvus_host, port=milvus_port,
        channel_kwargs={"grpc.max_receive_message_length": 32*1024*1024})

    collection_name = "knowledge_base"

    # 重置：如果 collection 已存在则删除
    if utility.has_collection(collection_name):
        if reset:
            print(f"  -> 删除已有 collection '{collection_name}'...")
            utility.drop_collection(collection_name)
        else:
            print(f"  -> Collection '{collection_name}' 已存在，跳过 Milvus 初始化。")

    # 确保 collection 存在
    if not utility.has_collection(collection_name):
        print(f"  -> 创建 collection '{collection_name}'...")
        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=128, is_primary=True),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=1024),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=4096),
            FieldSchema(name="doc_id", dtype=DataType.INT64),
            FieldSchema(name="chunk_index", dtype=DataType.INT64),
            FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="doc_type", dtype=DataType.VARCHAR, max_length=64),
            # ---- 新增字段 ----
            FieldSchema(name="section_type", dtype=DataType.VARCHAR, max_length=32),
            FieldSchema(name="markets", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="lang", dtype=DataType.VARCHAR, max_length=8),
            FieldSchema(name="hierarchy", dtype=DataType.VARCHAR, max_length=256),
        ]
        schema = CollectionSchema(fields=fields, description="RAG Knowledge Base - Production")
        collection = Collection(name=collection_name, schema=schema)
        print(f"  -> 创建向量索引 (IVF_FLAT / IP / nlist=128)...")
        collection.create_index(
            field_name="vector",
            index_params={"index_type": "IVF_FLAT", "metric_type": "IP", "params": {"nlist": 128}},
        )
        collection.load()
        print(f"  -> Collection 加载完成。")
    else:
        collection = Collection(collection_name)

    # =========================================================================
    # 2. 初始化 MySQL 连接池
    # =========================================================================
    print(f"\n[2/6] 初始化 MySQL ({mysql_host}:{mysql_port})...")
    mysql_pool: aiomysql.Pool = await aiomysql.create_pool(
        host=mysql_host,
        port=mysql_port,
        user=mysql_user,
        password=mysql_password,
        db=mysql_database,
        charset="utf8mb4",
        minsize=1,
        maxsize=5,
        autocommit=True,
    )

    # =========================================================================
    # 3. 读取 JSON 数据文件
    # =========================================================================
    print(f"\n[3/6] 读取数据文件...")
    data_path = Path(data_dir)
    docs_by_type: dict[str, list[tuple[str, dict]]] = {
        "product": [],
        "policy": [],
        "faq": [],
        "manual": [],
    }

    # 读取 products.json
    products_file = data_path / "products.json"
    if products_file.exists():
        with open(products_file, encoding="utf-8") as f:
            products = json.load(f)
            for item in products:
                docs_by_type["product"].append((item.get("product_id", item.get("id", "")), item))
        print(f"  -> products.json: {len(products)} 条商品")

    # 读取 knowledge_base.json
    kb_file = data_path / "knowledge_base.json"
    if kb_file.exists():
        with open(kb_file, encoding="utf-8") as f:
            kb_items = json.load(f)
            for item in kb_items:
                doc_id = item.get("id", "")
                category = item.get("category", "")
                if "退换货" in category or "退货" in category or "换货" in category:
                    doc_type = "policy"
                elif "价格保护" in category or "发票" in category:
                    doc_type = "policy"
                elif "配送" in category or "支付" in category:
                    doc_type = "faq"
                else:
                    doc_type = "faq"
                docs_by_type[doc_type].append((doc_id, item))
        print(f"  -> knowledge_base.json: {len(kb_items)} 条知识库条目")

    # 读取 orders.json
    orders_file = data_path / "orders.json"
    if orders_file.exists():
        with open(orders_file, encoding="utf-8") as f:
            orders = json.load(f)
            for item in orders:
                docs_by_type["manual"].append((item.get("order_id", ""), item))
        print(f"  -> orders.json: {len(orders)} 条订单")

    # =========================================================================
    # 3b. 读取富文本文件（PDF / DOCX / HTML）——带 fallback 兜底
    # =========================================================================
    supported_exts = {".pdf", ".docx", ".html", ".htm"}
    for ext in list(supported_exts):
        for file_path in data_path.rglob(f"*{ext}"):
            parsed = _parse_file_safe(str(file_path))
            if parsed is None:
                continue
            docs_by_type["policy"].append((str(file_path), {
                "title": parsed.title,
                "content": parsed.content,
                "category": "富文本文档",
                "_markets": parsed.markets,
                "_lang": parsed.lang,
            }))
            print(f"  -> {file_path.name}: {parsed.title[:40]} (PDF/DOCX/HTML)")

    total_docs = sum(len(v) for v in docs_by_type.values())
    print(f"  -> 共读取 {total_docs} 条文档（含富文本）")

    # =========================================================================
    # 4. 定义 Embedding 工具函数
    # =========================================================================
    print(f"\n[4/6] 配置向量化服务...")

    async def embed_texts(texts: list[str]) -> list[list[float]]:
        """调用 DashScope text-embedding-v3 API 向量化文本，失败时降级到 Mock 模式"""
        if not dashscope_api_key:
            return _mock_vectors(texts)

        headers = {
            "Authorization": f"Bearer {dashscope_api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": "text-embedding-v3", "input": texts}

        base_url = llm_base_url.rstrip("/")
        url = f"{base_url}/embeddings"

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return [e["embedding"] for e in data["data"]]
        except Exception as e:
            print(f"  [WARN] DashScope API 调用失败: {e}，使用 Mock 向量继续（仅供测试）")
            return _mock_vectors(texts)

    def _mock_vectors(texts: list[str]) -> list[list[float]]:
        """生成确定性伪随机向量（相同文本产生相同向量，便于调试）"""
        result = []
        for i, t in enumerate(texts):
            vec = [
                (hashlib.md5(f"{t}_{i}_{j}".encode()).digest()[j % 16] / 255.0) * 2 - 1
                for j in range(1024)
            ]
            result.append(vec)
        return result

    # =========================================================================
    # 5. 分批处理文档
    # =========================================================================
    chunker = TextChunker(chunk_size=chunk_size, overlap=chunk_overlap)
    total_chunks = 0
    total_vectors = 0
    start_time = time.time()

    async with mysql_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            for doc_type, items in docs_by_type.items():
                if not items:
                    continue

                for source_id, item in items:
                    title = item.get("title") or item.get("name") or item.get("order_id") or source_id

                    # 计算文件 hash 用于去重
                    content_str = json.dumps(item, ensure_ascii=False, sort_keys=True)
                    file_hash = hashlib.sha256(content_str.encode()).hexdigest()

                    # 检查是否已存在（通过 hash 去重）
                    await cur.execute(
                        "SELECT id, status FROM documents WHERE file_hash = %s",
                        (file_hash,),
                    )
                    existing = await cur.fetchone()

                    if existing and existing["status"] == "done" and not reset:
                        print(f"  [SKIP] {doc_type}/{source_id} 已索引，跳过")
                        continue

                    # 获取或创建文档记录
                    if existing:
                        doc_db_id = existing["id"]
                        await cur.execute(
                            "UPDATE documents SET status = 'processing' WHERE id = %s",
                            (doc_db_id,),
                        )
                    else:
                        await cur.execute(
                            "INSERT INTO documents (title, doc_type, file_hash, status) VALUES (%s, %s, %s, 'processing')",
                            (title, doc_type, file_hash),
                        )
                        doc_db_id = cur.lastrowid

                    print(f"  [INGEST] {doc_type}/{source_id}: {title[:40]}... (db_id={doc_db_id})")

                    # 分块（传入 markets 和 lang）
                    markets = item.get("_markets", [])
                    lang = item.get("_lang", "")
                    chunks: list[Chunk] = chunker.chunk_structured(
                        item, doc_db_id, source_id, doc_type, markets=markets, lang=lang
                    )
                    total_chunks += len(chunks)

                    # 批量向量化 + 入库
                    all_vectors: list[list[float]] = []
                    all_contents: list[str] = []
                    all_ids: list[str] = []
                    all_doc_ids: list[int] = []
                    all_indices: list[int] = []
                    all_sources: list[str] = []
                    all_doc_types: list[str] = []
                    all_section_types: list[str] = []
                    all_markets: list[str] = []
                    all_langs: list[str] = []
                    all_hierarchies: list[str] = []

                    for chunk in chunks:
                        all_ids.append(f"{chunk.doc_id}_{chunk.chunk_index}")
                        all_contents.append(chunk.content)
                        all_doc_ids.append(chunk.doc_id)
                        all_indices.append(chunk.chunk_index)
                        all_sources.append(chunk.source)
                        all_doc_types.append(chunk.doc_type)
                        all_section_types.append(chunk.section_type)
                        all_markets.append(json.dumps(chunk.markets, ensure_ascii=False))
                        all_langs.append(chunk.lang)
                        # 减小 hierarchy 体积：key 截断到 20，value 截断到 15，去掉 _flat_index
                        safe_hierarchy = {k[:20]: v[:15] for k, v in chunk.hierarchy.items() if k != "_flat_index"}
                        hierarchy_str = json.dumps(safe_hierarchy, ensure_ascii=False)
                        all_hierarchies.append(hierarchy_str[:256] if len(hierarchy_str) > 256 else hierarchy_str)

                    # 分批向量化
                    batch_vectors: list[list[float]] = []
                    for i in range(0, len(chunks), batch_size):
                        batch_texts = all_contents[i : i + batch_size]
                        vectors = await embed_texts(batch_texts)
                        batch_vectors.extend(vectors)

                    all_vectors = batch_vectors

                    # 写入 Milvus（扩展字段）
                    try:
                        collection.insert(
                            [
                                all_ids,
                                all_vectors,
                                all_contents,
                                all_doc_ids,
                                all_indices,
                                all_sources,
                                all_doc_types,
                                all_section_types,
                                all_markets,
                                all_langs,
                                all_hierarchies,
                            ]
                        )
                        collection.flush()
                        total_vectors += len(all_vectors)

                        # 更新 MySQL 状态
                        await cur.execute(
                            "UPDATE documents SET status = 'done', indexed_at = NOW(), chunk_count = %s WHERE id = %s",
                            (len(chunks), doc_db_id),
                        )
                        print(f"    -> {len(chunks)} chunks, {len(all_vectors)} vectors indexed OK")
                    except Exception as e:
                        await cur.execute(
                            "UPDATE documents SET status = 'failed' WHERE id = %s",
                            (doc_db_id,),
                        )
                        print(f"    -> ERROR: {e}")
                        raise

    mysql_pool.close()
    await mysql_pool.wait_closed()
    connections.disconnect(alias=alias)

    elapsed = time.time() - start_time
    print(f"\n[5/6] Milvus 写入完成: {total_vectors} 向量")
    print(f"[6/6] MySQL 元数据更新完成")

    # 验证结果
    print(f"\n========== 摄取完成 ==========")
    print(f"  文档总数: {total_docs}")
    print(f"  Chunk 总数: {total_chunks}")
    print(f"  向量总数: {total_vectors}")
    print(f"  耗时: {elapsed:.1f}s")
    print(f"  Milvus collection: {collection_name}")

    # 查询统计
    connections.connect(alias=alias, host=milvus_host, port=milvus_port,
        channel_kwargs={"grpc.max_receive_message_length": 32*1024*1024})
    verify_collection = Collection(collection_name)
    print(f"  Milvus 实有向量: {verify_collection.num_entities}")
    connections.disconnect(alias=alias)

    print(f"\n  知识库已就绪，可通过 RAG API 进行检索。")
    print(f"  访问 http://localhost:8000/api/v1/health 验证服务状态。")


def main():
    parser = argparse.ArgumentParser(
        description="RAG 知识库数据摄取脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m scripts.ingest_data                          # 默认配置
  python -m scripts.ingest_data --reset                   # 清除重建
  python -m scripts.ingest_data --milvus-host 192.168.1.100
  python -m scripts.ingest_data --chunk-size 256 --batch-size 32
        """,
    )

    # 路径配置
    parser.add_argument(
        "--data-dir",
        default=str(PROJECT_DIR / "data"),
        help="JSON 数据文件目录 (default: project_root/data)",
    )

    # Milvus 配置
    parser.add_argument("--milvus-host", default=os.getenv("MILVUS_HOST", "localhost"), help="Milvus host")
    parser.add_argument("--milvus-port", type=int, default=int(os.getenv("MILVUS_PORT", "19530")), help="Milvus port")

    # 分块配置
    parser.add_argument("--chunk-size", type=int, default=512, help="每个 chunk 的最大 tokens 数 (default: 512)")
    parser.add_argument("--chunk-overlap", type=int, default=50, help="相邻 chunk 重叠 tokens 数 (default: 50)")
    parser.add_argument("--batch-size", type=int, default=10, help="每批向量化文本数量 (default: 10)")

    # 重置选项
    parser.add_argument("--reset", action="store_true", help="删除已有 collection 后重新摄取")

    # MySQL 配置
    parser.add_argument("--mysql-host", default=os.getenv("DB_HOST") or os.getenv("MYSQL_HOST", ""), help="MySQL host")
    parser.add_argument("--mysql-port", type=int, default=int(os.getenv("DB_PORT") or os.getenv("MYSQL_PORT", "3306")), help="MySQL port")
    parser.add_argument("--mysql-user", default=os.getenv("DB_USER") or os.getenv("MYSQL_USER", ""), help="MySQL user")
    parser.add_argument(
        "--mysql-password", default=os.getenv("DB_PASSWORD") or os.getenv("MYSQL_PASSWORD", ""), help="MySQL password"
    )
    parser.add_argument(
        "--mysql-database",
        default=os.getenv("DB_NAME") or os.getenv("MYSQL_DATABASE", "lumapepe"),
        help="MySQL database name",
    )

    # Redis 配置
    parser.add_argument("--redis-url", default=os.getenv("REDIS_URL", "redis://localhost:6379"), help="Redis URL")

    # LLM/Embedding 配置
    parser.add_argument("--dashscope-api-key", default=os.getenv("DASHSCOPE_API_KEY", ""), help="DashScope API Key")
    parser.add_argument(
        "--llm-base-url",
        default=os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com"),
        help="LLM Base URL",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("  RAG 知识库数据摄取脚本")
    print("=" * 60)
    print(f"  数据目录: {args.data_dir}")
    print(f"  Milvus:   {args.milvus_host}:{args.milvus_port}")
    print(f"  MySQL:    {args.mysql_host}:{args.mysql_port}/{args.mysql_database}")
    print(f"  Chunk:    {args.chunk_size} tokens / {args.chunk_overlap} overlap (token-based)")
    print(f"  Batch:    {args.batch_size}")
    print(f"  Reset:    {args.reset}")
    print("=" * 60)

    try:
        asyncio.run(
            run_ingestion(
                data_dir=args.data_dir,
                milvus_host=args.milvus_host,
                milvus_port=args.milvus_port,
                chunk_size=args.chunk_size,
                chunk_overlap=args.chunk_overlap,
                batch_size=args.batch_size,
                reset=args.reset,
                mysql_host=args.mysql_host,
                mysql_port=args.mysql_port,
                mysql_user=args.mysql_user,
                mysql_password=args.mysql_password,
                mysql_database=args.mysql_database,
                redis_url=args.redis_url,
                dashscope_api_key=args.dashscope_api_key,
                llm_base_url=args.llm_base_url,
            )
        )
    except Exception as e:
        print(f"\n[FATAL] 摄取失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
