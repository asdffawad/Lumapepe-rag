# -*- coding: utf-8 -*-
"""商品信息解析器单元测试

运行方式：
    pytest backend/tests/test_product_info_parser.py -v
或：
    conda activate rag-test && python -m pytest backend/tests/test_product_info_parser.py -v
"""
import pytest
from app.services.product_info_parser import (
    ProductInfoParser,
    parse_product_info,
    is_image_url,
    is_html_content,
)


class TestImageUrlDetection:
    """图片URL识别测试"""

    def test_common_image_formats(self):
        """测试常见图片格式"""
        assert is_image_url("https://example.com/photo.jpg") is True
        assert is_image_url("http://test.com/image.jpeg") is True
        assert is_image_url("https://cdn.com/pic.png") is True
        # .gif需要完整路径才能识别（以.jpg等结尾��
        assert is_image_url("https://example.com/anim.gif") is True
        assert is_image_url("https://a.com/b.webp") is True
        assert is_image_url("https://a.com/c.bmp") is True
        assert is_image_url("https://a.com/d.svg") is True

    def test_non_image_urls(self):
        """测试非图片URL"""
        assert is_image_url("https://example.com/file.pdf") is False
        assert is_image_url("https://example.com/page.html") is False
        assert is_image_url("just plain text") is False
        assert is_image_url("<img src='test.jpg'>") is False

    def test_url_without_extension(self):
        """测试无扩展名的URL"""
        assert is_image_url("https://example.com/image") is False
        assert is_image_url("https://example.com/api/photo?id=1") is False


class TestHtmlDetection:
    """HTML内容识别测试"""

    def test_simple_html(self):
        """测试简单HTML"""
        assert is_html_content("<p>text</p>") is True
        assert is_html_content("<div>content</div>") is True
        assert is_html_content("<span>span</span>") is True

    def test_html_with_img(self):
        """测试包含图片的HTML"""
        assert is_html_content("<img src='pic.jpg'>") is True
        assert is_html_content("<div><img></div>") is True

    def test_non_html(self):
        """测试非HTML内容"""
        assert is_html_content("plain text") is False
        assert is_html_content("https://example.com/photo.jpg") is False
        assert is_html_content("  ") is False
        assert is_html_content("< not valid") is False


class TestProductInfoParser:
    """商品信息解析器测试"""

    def setup_method(self):
        """每个测试前创建解析器实例"""
        self.parser = ProductInfoParser()

    def test_empty_input(self):
        """测试空输入"""
        result = self.parser.parse()
        assert result.parsed_introduction == []
        assert result.parsed_description == []
        assert result.has_images is False
        assert result.has_html is False

    def test_empty_strings(self):
        """测试空字符串"""
        result = self.parser.parse(introduction="", description="")
        assert len(result.parsed_introduction) == 0
        assert len(result.parsed_description) == 0

    def test_whitespace_only(self):
        """测试只有空格"""
        result = self.parser.parse(introduction="   ", description="   ")
        assert result.parsed_introduction == []
        assert result.parsed_description == []

    def test_plain_text(self):
        """测试纯文本"""
        text = "这是一段纯文本商品简介"
        result = self.parser.parse(introduction=text)
        assert len(result.parsed_introduction) == 1
        assert result.parsed_introduction[0].type == "text"
        assert result.parsed_introduction[0].content == text
        assert result.has_images is False
        assert result.has_html is False

    def test_text_with_image_url(self):
        """测试文本中的图片链接"""
        content = "查看这张图片 https://example.com/photo.jpg 了解更多"
        result = self.parser.parse(description=content)
        
        segments = result.parsed_description
        assert len(segments) == 3
        assert segments[0].type == "text"
        assert segments[0].content == "查看这张图片"
        assert segments[1].type == "image"
        assert segments[1].content == "https://example.com/photo.jpg"
        assert segments[2].type == "text"
        assert segments[2].content == "了解更多"
        assert result.has_images is True

    def test_multiple_image_urls(self):
        """测试多个图片链接"""
        content = "图1: https://a.jpg 图2: https://b.png"
        result = self.parser.parse(description=content)
        
        segments = result.parsed_description
        assert len(segments) == 4
        assert result.has_images is True
        # 检查所有图片片段
        images = [s for s in segments if s.type == "image"]
        assert len(images) == 2

    def test_image_url_with_query_params(self):
        """测试带查询参数的图片URL"""
        content = "https://cdn.com/photo.jpeg?token=abc123&size=large"
        result = self.parser.parse(description=content)
        
        segments = result.parsed_description
        assert len(segments) == 1
        assert segments[0].type == "image"
        assert segments[0].content == "https://cdn.com/photo.jpeg?token=abc123&size=large"

    def test_simple_html(self):
        """测试简单HTML"""
        html = "<p>这是一段</p><p>HTML内容</p>"
        result = self.parser.parse(description=html)
        
        segments = result.parsed_description
        assert len(segments) == 1
        assert segments[0].type == "text"
        assert "这是一段" in segments[0].content
        assert "HTML内容" in segments[0].content
        assert result.has_html is True

    def test_html_with_images(self):
        """测试包含图片的HTML"""
        html = '<p>详情</p><img src="pic.jpg"><p>更多</p>'
        result = self.parser.parse(description=html)
        
        segments = result.parsed_description
        # 应该包含文本和图片
        assert len(segments) >= 2
        types = [s.type for s in segments]
        assert "image" in types
        assert "text" in types
        assert result.has_images is True
        assert result.has_html is True
        
        # 检查图片URL
        images = [s for s in segments if s.type == "image"]
        assert len(images) >= 1
        assert images[0].content == "pic.jpg"

    def test_html_with_multiple_images(self):
        """测试多图片HTML"""
        html = '''
        <div>
            <h2>商品详情</h2>
            <p>材质：纯棉</p>
            <img src="https://img.com/1.png" alt="图1">
            <p>颜色：白色</p>
            <img src="https://img.com/2.jpg" alt="图2">
        </div>
        '''
        result = self.parser.parse(description=html)
        
        assert result.has_images is True
        assert result.has_html is True
        
        images = [s for s in result.parsed_description if s.type == "image"]
        assert len(images) >= 2
        image_urls = [s.content for s in images]
        assert "https://img.com/1.png" in image_urls
        assert "https://img.com/2.jpg" in image_urls

    def test_mixed_text_and_images(self):
        """测试混合文本和图片链接"""
        content = "展示图 https://example.com/main.jpg 商品采用优质材料制作"
        result = self.parser.parse(description=content)
        
        segments = result.parsed_description
        assert len(segments) == 3
        assert segments[0].type == "text"
        assert segments[1].type == "image"
        assert segments[2].type == "text"
        assert result.has_images is True

    def test_full_product_info(self):
        """测试完整的商品信息（简介+详情）"""
        introduction = "夏季新款T恤 https://img.com/tshirt.jpg 舒适透气"
        description = "<p>材质：100%纯棉</p><img src='detail.png'>"
        
        result = self.parser.parse(introduction=introduction, description=description)
        
        # 简介解析
        assert len(result.parsed_introduction) >= 3
        assert result.has_images is True
        intro_types = [s.type for s in result.parsed_introduction]
        assert "image" in intro_types
        
        # 详情解析
        assert len(result.parsed_description) >= 2
        assert result.has_html is True
        desc_types = [s.type for s in result.parsed_description]
        assert "image" in desc_types
        assert "text" in desc_types

    def test_html_with_special_tags(self):
        """测试特殊HTML标签"""
        html = "<strong>加粗</strong><em>斜体</em><u>下划线</u>"
        result = self.parser.parse(description=html)
        
        assert result.parsed_description[0].type == "text"
        assert "加粗" in result.parsed_description[0].content
        assert "斜体" in result.parsed_description[0].content
        assert "下划线" in result.parsed_description[0].content

    def test_html_with_list(self):
        """测试列表HTML"""
        html = "<ul><li>项目1</li><li>项目2</li><li>项目3</li></ul>"
        result = self.parser.parse(description=html)
        
        text = result.parsed_description[0].content
        assert "项目1" in text
        assert "项目2" in text
        assert "项目3" in text

    def test_incomplete_html(self):
        """测试不完整的HTML"""
        html = "<div>未闭合的标签"
        result = self.parser.parse(description=html)
        
        # 应该能正常处理不完整的HTML
        assert len(result.parsed_description) >= 1
        assert result.parsed_description[0].type == "text"

    def test_get_plain_text(self):
        """测试纯文本提取"""
        intro = "文本1 https://img.com/a.jpg"
        desc = "<p>文本2</p><img src='b.png'>"
        
        result = self.parser.parse(introduction=intro, description=desc)
        plain = result.get_plain_text()
        
        assert "文本1" in plain
        assert "文本2" in plain
        # 图片链接不应出现在纯文本中
        assert "img.com" not in plain
        assert "b.png" not in plain

    def test_to_dict(self):
        """测试字典序列化"""
        result = self.parser.parse(
            introduction="简介",
            description="<p>详情</p><img src='pic.jpg'>"
        )
        
        data = result.to_dict()
        assert "raw_introduction" in data
        assert "raw_description" in data
        assert "parsed_introduction" in data
        assert "parsed_description" in data
        assert "has_images" in data
        assert "has_html" in data
        # 由于HTML包含图片，应该检测到
        assert data["has_html"] is True

    def test_convenience_function(self):
        """测试便捷函数"""
        result = parse_product_info(
            introduction="简介文本",
            description="详情文本"
        )
        assert isinstance(result.parsed_introduction, list)
        assert result.raw_introduction == "简介文本"


class TestEdgeCases:
    """边界情况测试"""

    def setup_method(self):
        """创建解析器实例"""
        self.parser = ProductInfoParser()

    def test_none_values(self):
        """测试None值"""
        result = self.parser.parse(introduction=None, description=None)
        assert result.parsed_introduction == []
        assert result.parsed_description == []

    def test_only_spaces(self):
        """测试只有空格"""
        result = self.parser.parse(introduction="   ", description="   ")
        assert result.parsed_introduction == []
        assert result.parsed_description == []

    def test_html_tag_without_src(self):
        """测试无src的img标签"""
        html = "<img class='test'>更多文本"
        result = self.parser.parse(description=html)
        # 应该提取文本，但不提取图片
        assert all(s.type == "text" for s in result.parsed_description)

    def test_text_with_html_inside(self):
        """测试文本包含HTML标签字符"""
        text = "描述 < 大于号 > 小于号"
        result = self.parser.parse(description=text)
        assert len(result.parsed_description) == 1
        assert result.parsed_description[0].type == "text"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
