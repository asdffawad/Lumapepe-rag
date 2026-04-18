"""商品信息解析器

功能：
- 解析商品简介（introduction）和详情（description）
- 支持纯文本、HTML、图片链接的混合内容
- 提取文本内容，图片链接单独标记

作者：AI助手
日期：2026-04-17
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, List
from html.parser import HTMLParser

try:
    from bs4 import BeautifulSoup  # type: ignore
    HAS_BEAUTIFULSOUP = True
except ImportError:
    HAS_BEAUTIFULSOUP = False


# ============== 数据类 ==============

@dataclass
class ParsedTextSegment:
    """解析后的文本片段"""
    type: str  # "text" | "image" | "unknown"
    content: str
    original: str


@dataclass
class ProductInfoParseResult:
    """商品信息解析结果"""
    raw_introduction: Optional[str] = None
    raw_description: Optional[str] = None
    parsed_introduction: List[ParsedTextSegment] = field(default_factory=list)
    parsed_description: List[ParsedTextSegment] = field(default_factory=list)
    has_images: bool = False
    has_html: bool = False

    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "raw_introduction": self.raw_introduction,
            "raw_description": self.raw_description,
            "parsed_introduction": [self._segment_to_dict(s) for s in self.parsed_introduction],
            "parsed_description": [self._segment_to_dict(s) for s in self.parsed_description],
            "has_images": self.has_images,
            "has_html": self.has_html,
        }

    def _segment_to_dict(self, seg: ParsedTextSegment) -> dict:
        return {
            "type": seg.type,
            "content": seg.content,
            "original": seg.original,
        }

    def get_plain_text(self) -> str:
        """获取纯文本摘要（合并所有文本片段）"""
        texts = []
        for seg in self.parsed_introduction + self.parsed_description:
            if seg.type == "text":
                texts.append(seg.content)
        return "\n".join(texts).strip()


# ============== 工具函数 ==============

def is_image_url(text: str) -> bool:
    """
    判断是否为图片链接

    常见图片格式：.jpg, .jpeg, .png, .gif, .webp, .bmp, .svg
    也支持URL模式：http(s)://.../image.jpg
    """
    text_lower = text.lower().strip()
    # 检查是否以http/https开头（完整URL）
    if text_lower.startswith(("http://", "https://")):
        # 图片扩展名
        image_exts = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg')
        return any(text_lower.endswith(ext) for ext in image_exts)
    return False


def is_html_content(text: str) -> bool:
    """
    判断是否包含HTML标签

    简单的启发式规则：检测 <tag> 或 </tag> 模式
    """
    if not text:
        return False
    # 检测HTML标签
    html_pattern = re.compile(r'<[^>]+>')
    return bool(html_pattern.search(text))


def strip_html_tags(html: str) -> str:
    """
    去除HTML标签，提取纯文本

    优先使用BeautifulSoup（如果可用），否则使用内置HTMLParser
    """
    if not html:
        return ""

    if HAS_BEAUTIFULSOUP:
        try:
            soup = BeautifulSoup(html, "html.parser")
            return soup.get_text(separator=" ", strip=True)
        except Exception:
            pass  # 回退到内置解析器

    # 使用内置HTMLParser
    class TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.texts = []

        def handle_data(self, data):
            self.texts.append(data)

        def get_text(self):
            return " ".join(self.texts).strip()

    extractor = TextExtractor()
    try:
        extractor.feed(html)
        return extractor.get_text()
    except Exception:
        # 最终回退：简单去除标签
        return re.sub(r'<[^>]+>', '', html).strip()


# ============== 核心解析器 ==============

class ProductInfoParser:
    """
    商品信息解析器

    用法示例：
        parser = ProductInfoParser()
        result = parser.parse(
            introduction="高清图片.jpg",
            description="<p>商品详情</p><img src='pic.png'>"
        )
        print(result.get_plain_text())  # 输出：商品详情
    """

    def __init__(self):
        # 匹配完整URL的图片（http/https开头，带查询参数）
        self._image_pattern_full = re.compile(
            r'https?://[^\s<>"\']+\.(?:jpg|jpeg|png|gif|webp|bmp|svg)(?:\?[^\s<>"\']*)?',
            re.IGNORECASE
        )
        # 匹配HTML标签中的src属性值（支持引号内和未加引号）
        self._image_pattern_html_src = re.compile(
            r'src\s*=\s*["\']?([^"\'<>\\s]+\.(?:jpg|jpeg|png|gif|webp|bmp|svg)(?:\?[^"\'<>\\s]*)?)',
            re.IGNORECASE
        )

    def parse(
        self,
        introduction: Optional[str] = None,
        description: Optional[str] = None,
    ) -> ProductInfoParseResult:
        """
        解析商品简介和详情

        Args:
            introduction: 商品简介（可能包含文本、图片、HTML）
            description: 商品详情（可能包含文本、图片、HTML）

        Returns:
            ProductInfoParseResult: 解析结果
        """
        result = ProductInfoParseResult(
            raw_introduction=introduction,
            raw_description=description,
        )

        # 解析简介
        if introduction:
            result.parsed_introduction = self._parse_content(introduction)

        # 解析详情
        if description:
            result.parsed_description = self._parse_content(description)

        # 统计标志
        result.has_images = any(
            seg.type == "image"
            for seg in result.parsed_introduction + result.parsed_description
        )
        result.has_html = any(
            seg.type == "text" and is_html_content(seg.original)
            for seg in result.parsed_introduction + result.parsed_description
        )

        return result

    def _parse_content(self, content: str) -> List[ParsedTextSegment]:
        """
        解析内容字符串，识别图片链接、HTML、纯文本片段

        策略：
        1. 先检查是否为纯HTML（包含标签）
        2. 如果是HTML，使用BeautifulSoup提取文本和图片
        3. 否则按文本+图片URL混合处理
        """
        segments: List[ParsedTextSegment] = []

        if not content or not content.strip():
            return segments

        # Step 1: 检查是否为HTML内容
        if is_html_content(content):
            segments.extend(self._parse_html_content(content))
            return segments

        # Step 2: 非HTML内容，按文本+图片URL混合处理
        # 查找所有完整URL的图片
        matches = list(self._image_pattern_full.finditer(content))
        last_end = 0

        for match in matches:
            # 图片链接前的文本（如果有）
            if match.start() > last_end:
                text_part = content[last_end:match.start()].strip()
                if text_part:
                    cleaned = self._clean_text(text_part)
                    if cleaned:
                        segments.append(ParsedTextSegment(
                            type="text",
                            content=cleaned,
                            original=text_part,
                        ))

            # 图片链接本身
            image_url = match.group().strip()
            segments.append(ParsedTextSegment(
                type="image",
                content=image_url,
                original=image_url,
            ))
            last_end = match.end()

        # 结尾剩余文本
        remaining = content[last_end:].strip()
        if remaining:
            cleaned = self._clean_text(remaining)
            if cleaned:
                segments.append(ParsedTextSegment(
                    type="text",
                    content=cleaned,
                    original=remaining,
                ))

        # 如果没有识别出任何片段，整个作为文本
        if not segments:
            cleaned = self._clean_text(content)
            if cleaned:
                segments.append(ParsedTextSegment(
                    type="text",
                    content=cleaned,
                    original=content,
                ))

        return segments

    def _parse_html_content(self, html: str) -> List[ParsedTextSegment]:
        """
        解析HTML内容，使用BeautifulSoup按顺序提取文本和图片
        """
        segments: List[ParsedTextSegment] = []

        # 优先使用fallback方法（正则表达式），它更可靠
        segments.extend(self._parse_html_content_fallback(html))
        
        # 如果BeautifulSoup可用，尝试提取更精确的文本
        if HAS_BEAUTIFULSOUP:
            try:
                from bs4 import BeautifulSoup, NavigableString
                soup = BeautifulSoup(html, "html.parser")
                
                # 提取所有文本
                texts = []
                def extract_text(node):
                    if node is None:
                        return
                    if isinstance(node, NavigableString):
                        text = str(node).strip()
                        if text:
                            texts.append(text)
                    elif hasattr(node, 'children'):
                        for child in node.children:
                            extract_text(child)
                
                extract_text(soup)
                
                if texts:
                    cleaned = self._clean_text(" ".join(texts))
                    if cleaned:
                        # 检查是否已有相同文本的segment
                        has_same_text = any(
                            s.type == "text" and s.content == cleaned 
                            for s in segments
                        )
                        if not has_same_text:
                            # 替换现有的文本segment（fallback的正则可能不完美）
                            text_segments = [s for s in segments if s.type == "text"]
                            for s in text_segments:
                                segments.remove(s)
                            segments.append(ParsedTextSegment(
                                type="text",
                                content=cleaned,
                                original=html,
                            ))
                
            except Exception:
                pass  # fallback结果已添加，继续使用
        
        return segments
    
    def _parse_html_content_fallback(self, html: str) -> List[ParsedTextSegment]:
        """HTML解析失败时的回退方案"""
        segments: List[ParsedTextSegment] = []
        
        # 提取所有src属性中的图片路径
        # 注意：在r-string的字符类中，\s会被保留为字面的\s，所以需要用\\s
        src_pattern = re.compile(r'src\s*=\s*["\']([^"\'<>\s]+\.(?:jpg|jpeg|png|gif|webp|bmp|svg)(?:\?[^"\'<>\s]*)?)["\']', re.IGNORECASE)
        for match in src_pattern.finditer(html):
            url = match.group(1)
            if url and url not in [s.content for s in segments]:
                segments.append(ParsedTextSegment(
                    type="image",
                    content=url,
                    original=url,
                ))
        
        # 也匹配不带引号的src
        src_unquoted = re.compile(r'src\s*=\s*([^\s<>"\']+\.(?:jpg|jpeg|png|gif|webp|bmp|svg)(?:\?[^\s<>"\']*)?)', re.IGNORECASE)
        for match in src_unquoted.finditer(html):
            url = match.group(1)
            if url and url not in [s.content for s in segments]:
                segments.append(ParsedTextSegment(
                    type="image",
                    content=url,
                    original=url,
                ))
        
        # 去除HTML标签提取文本
        text = re.sub(r'<[^>]+>', ' ', html)
        cleaned = self._clean_text(text)
        if cleaned:
            segments.append(ParsedTextSegment(
                type="text",
                content=cleaned,
                original=html,
            ))
        
        return segments

    def _clean_text(self, text: str) -> str:
        """
        清理文本：
        - 去除多余空白字符
        - 规范化换行和空格
        """
        if not text:
            return ""
        # 去除首尾空白
        text = text.strip()
        # 替换多个空白字符为单个空格
        text = re.sub(r'\s+', ' ', text)
        return text


# ============== 便捷函数 ==============

def parse_product_info(
    introduction: Optional[str] = None,
    description: Optional[str] = None,
) -> ProductInfoParseResult:
    """
    便捷函数：解析商品信息

    Args:
        introduction: 商品简介
        description: 商品详情

    Returns:
        解析结果对象
    """
    parser = ProductInfoParser()
    return parser.parse(introduction, description)
