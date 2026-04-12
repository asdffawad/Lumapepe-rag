"""
知识库摄取流水线测试
"""
import pytest
from app.utils.chunker import TextChunker, Chunk


class TestTextChunker:
    """文本分块器单元测试"""

    def setup_method(self):
        self.chunker = TextChunker(chunk_size=512, overlap=64)

    def test_chunk_by_paragraph_short(self):
        """单段落文本应合并为一个 chunk"""
        text = "这是测试内容。" * 20  # ~400 chars
        chunks = self.chunker.chunk_by_paragraph(text, doc_id=1, source="TEST001", doc_type="faq")
        assert len(chunks) == 1
        assert chunks[0].content == text

    def test_chunk_by_paragraph_multiple(self):
        """多段落文本应产生多个 chunk"""
        text = ("段落一内容。" * 100 + "\n\n" + "段落二内容。" * 100)
        chunks = self.chunker.chunk_by_paragraph(text, doc_id=1, source="TEST002", doc_type="faq")
        assert len(chunks) >= 1
        for c in chunks:
            assert isinstance(c, Chunk)
            assert c.doc_id == 1
            assert c.doc_type == "faq"

    def test_chunk_structured_product(self):
        """结构化商品 JSON 应正确分块"""
        product = {
            "product_id": "P001",
            "name": "iPhone 16 Pro Max",
            "category": "手机",
            "price": 9999.00,
            "description": "苹果旗舰手机，配备A18 Pro芯片。",
            "specs": {"屏幕": "6.9英寸", "芯片": "A18 Pro"},
            "tags": ["手机", "苹果", "iPhone"],
        }
        chunks = self.chunker.chunk_structured(product, doc_id=1, source="P001", doc_type="product")
        assert len(chunks) >= 1
        assert all(isinstance(c, Chunk) for c in chunks)
        # 内容应包含关键字段
        first_content = chunks[0].content
        assert "iPhone" in first_content or "name" in first_content or "描述" in first_content

    def test_chunk_structured_policy(self):
        """政策文档应正确处理 applicable/non_applicable 等字段"""
        policy = {
            "id": "POLICY001",
            "title": "七天无理由退货",
            "content": "自收到商品之日起7天内，如商品保持完好，消费者可申请退货。",
            "applicable": ["未使用的商品", "包装完整", "配件齐全"],
            "non_applicable": ["定制品", "鲜活易腐商品", "下载的数字化商品"],
        }
        chunks = self.chunker.chunk_structured(policy, doc_id=2, source="POLICY001", doc_type="policy")
        assert len(chunks) >= 1
        for c in chunks:
            assert c.doc_type == "policy"
            assert c.doc_id == 2

    def test_chunk_empty_text(self):
        """空文本应返回空列表"""
        chunks = self.chunker.chunk_by_paragraph("", doc_id=1, source="TEST", doc_type="faq")
        assert len(chunks) == 0

    def test_chunk_index_sequential(self):
        """chunk_index 应连续递增"""
        text = ("A" * 500 + "\n\n" + "B" * 500 + "\n\n" + "C" * 500)
        chunks = self.chunker.chunk_by_paragraph(text, doc_id=1, source="SEQ", doc_type="faq")
        indices = [c.chunk_index for c in chunks]
        assert indices == list(range(len(indices)))

    def test_chunk_size_limit(self):
        """每个 chunk 不应超过指定大小"""
        text = "X" * 1000
        chunks = self.chunker.chunk_by_paragraph(text, doc_id=1, source="SIZE", doc_type="faq")
        for c in chunks:
            assert len(c.content) <= 512 + 64  # 允许一点重叠

    def test_source_and_doc_type_preserved(self):
        """source 和 doc_type 应正确传递"""
        policy = {
            "id": "FAQ001",
            "title": "配送时效",
            "content": "同城配送预计1-2天。",
        }
        chunks = self.chunker.chunk_structured(policy, doc_id=99, source="FAQ001", doc_type="faq")
        for c in chunks:
            assert c.source == "FAQ001"
            assert c.doc_type == "faq"
            assert c.doc_id == 99


class TestChunkDataclass:
    """Chunk 数据类测试"""

    def test_chunk_creation(self):
        chunk = Chunk(
            content="测试内容",
            doc_id=1,
            chunk_index=0,
            source="TEST",
            doc_type="faq",
        )
        assert chunk.content == "测试内容"
        assert chunk.doc_id == 1
        assert chunk.chunk_index == 0
        assert chunk.source == "TEST"
        assert chunk.doc_type == "faq"
