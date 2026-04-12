"""
富文本文件解析器
支持 PDF / Word (.docx) / HTML 格式，自动提取文本内容供分块使用。
新增：Section 结构体、市场多值推断、语言自动推断、中英合并格式
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

# -------------------------------------------------------------------------
# 共享类型（供 chunker.py 引用）
# -------------------------------------------------------------------------
MARKET_KEYWORDS = {
    "CN": ["中国", "cn", "境内", "国内", "中文"],
    "VN": ["越南", "vietnam", "vn", "tiếng Việt", "Việt Nam", "越文"],
    "US": ["美国", "us", "english"],
}


def infer_markets(file_path: str, content: str = "") -> list[str]:
    """从路径和内容推断市场（支持多值）。默认返回 ["CN"]。"""
    combined = file_path + " " + (content[:500] if content else "")
    matched: set[str] = set()
    for market, keywords in MARKET_KEYWORDS.items():
        if any(kw.lower() in combined.lower() for kw in keywords):
            matched.add(market)
    return list(matched) or ["CN"]


def infer_lang(text: str) -> str:
    """
    从文本内容推断语言（补充规则，而非替代显式规则）。
    策略：汉字密度 + 关键词判断
    """
    if not text:
        return "zh"

    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    total_chars = max(len(text.replace(" ", "")), 1)

    zh_ratio = chinese_chars / total_chars

    if "Vietnam" in text or "tiếng Việt" in text or "Việt Nam" in text:
        return "vi"
    if "English" in text and zh_ratio < 0.1:
        return "en"
    if re.search(r"[\u4e00-\u9fff]", text) and zh_ratio > 0.3:
        return "zh"
    return "en"


def merge_zh_en(content_zh: str, content_en: str, primary_lang: str = "zh") -> str:
    """
    中英合并：格式直接影响 embedding 质量。
    策略：ZH→EN 交叉段落 + [ZH]/[EN] 前缀分隔符。
    返回合并后的文本。
    """
    lines_zh = content_zh.strip().split("\n")
    lines_en = content_en.strip().split("\n")

    result: list[str] = []
    max_len = max(len(lines_zh), len(lines_en))
    for i in range(max_len):
        if i < len(lines_zh) and lines_zh[i].strip():
            result.append(f"[ZH] {lines_zh[i]}")
        if i < len(lines_en) and lines_en[i].strip():
            result.append(f"[EN] {lines_en[i]}")

    return "\n".join(result)


# -------------------------------------------------------------------------
# 数据模型
# -------------------------------------------------------------------------
@dataclass
class Section:
    """单个章节"""
    text: str
    section_type: str = "body"    # DocSectionType.value
    level: int = 0                 # 标题层级（1=H1, 2=H2...）
    number_prefix: str = ""         # "1", "1.2", "2.3.1"


@dataclass
class ParsedFile:
    """解析后的文件内容（增强版）"""
    title: str
    content: str
    doc_type: str
    source: str
    metadata: dict
    # ---- 新增字段 ----
    sections: list[Section] = field(default_factory=list)
    markets: list[str] = field(default_factory=list)
    lang: str = ""


# -------------------------------------------------------------------------
# Section 解析（可被 TextChunker 复用）
# -------------------------------------------------------------------------
def parse_sections_from_text(text: str) -> list[Section]:
    """
    解析文本为 Section 列表。
    使用缩进级别 + 行首模式判断层级和类型。
    """
    lines = text.split("\n")
    sections: list[Section] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            sections.append(Section(text="", section_type="divider", level=0, number_prefix=""))
            continue

        indent = len(line) - len(line.lstrip())
        level = indent // 2

        if re.match(r"^#{1,6}\s", stripped):
            stype = "heading"
            md_level = len(re.match(r"^#+", stripped).group())
            level = md_level
        elif re.match(r"^[\*\-•·] ", stripped):
            stype = "bullet"
        elif re.match(r"^\d+[.)] ", stripped):
            stype = "ordered"
        elif re.match(r"^【", stripped) or stripped.startswith("> ") or re.match(r"^注[：:]", stripped):
            stype = "note"
        else:
            stype = "body"

        num_match = re.match(r"^([\d\.①②③a-zA-Z一二三四五六七八九十百千万壹贰叁肆伍陆柒捌玖拾]+[.、) ]+)(.*)", stripped)
        if num_match:
            prefix = num_match.group(1).strip()
            content = num_match.group(2)
        else:
            prefix = ""
            content = stripped

        sections.append(Section(
            text=content or stripped,
            section_type=stype,
            level=max(level, 1) if stype in ("heading", "ordered") else level,
            number_prefix=prefix,
        ))

    return sections


# -------------------------------------------------------------------------
# 解析器基类
# -------------------------------------------------------------------------
class FileParser(ABC):
    @abstractmethod
    def parse(self, file_path: str) -> ParsedFile:
        pass

    def _normalize_text(self, text: str) -> str:
        """通用文本清理"""
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _auto_fields(self, file_path: str, content: str) -> tuple[list[str], str]:
        """推断 markets 和 lang"""
        markets = infer_markets(file_path, content)
        lang = infer_lang(content)
        return markets, lang


# -------------------------------------------------------------------------
# PDF 解析器
# -------------------------------------------------------------------------
class PDFParser(FileParser):
    def parse(self, file_path: str) -> ParsedFile:
        import pdfplumber

        title = ""
        lines: list[str] = []
        metadata: dict = {}

        with pdfplumber.open(file_path) as pdf:
            if pdf.metadata:
                metadata = dict(pdf.metadata)
                title = metadata.get("Title", "") or metadata.get("Subject", "") or file_path

            for page in pdf.pages:
                text = page.extract_text() or ""
                if text.strip():
                    lines.append(text)

        content = self._normalize_text("\n".join(lines))
        if not title:
            title = file_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

        raw_content = "\n".join(lines)
        markets, lang = self._auto_fields(file_path, raw_content)
        sections = parse_sections_from_text(content)

        return ParsedFile(
            title=title,
            content=content,
            doc_type="policy",
            source=file_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
            metadata={"page_count": len(pdf.pages), **metadata},
            sections=sections,
            markets=markets,
            lang=lang,
        )


# -------------------------------------------------------------------------
# DOCX 解析器
# -------------------------------------------------------------------------
class DOCXParser(FileParser):
    def parse(self, file_path: str) -> ParsedFile:
        from docx import Document

        doc = Document(file_path)
        title = ""
        lines: list[str] = []

        for para in doc.paragraphs:
            if para.style.name.startswith("Heading") or para.style.name == "Title":
                if para.text.strip():
                    title = para.text.strip()
                    break

        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                lines.append(text)

        content = self._normalize_text("\n".join(lines))
        if not title:
            title = file_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].replace(".docx", "")

        raw_content = "\n".join(lines)
        markets, lang = self._auto_fields(file_path, raw_content)
        sections = parse_sections_from_text(content)

        return ParsedFile(
            title=title,
            content=content,
            doc_type="policy",
            source=file_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
            metadata={"paragraph_count": len(doc.paragraphs)},
            sections=sections,
            markets=markets,
            lang=lang,
        )


# -------------------------------------------------------------------------
# HTML 解析器
# -------------------------------------------------------------------------
class HTMLParser(FileParser):
    def parse(self, file_path: str) -> ParsedFile:
        from bs4 import BeautifulSoup

        with open(file_path, encoding="utf-8", errors="ignore") as f:
            html = f.read()

        soup = BeautifulSoup(html, "lxml")

        title = soup.title.string.strip() if soup.title else ""
        if not title:
            h1 = soup.find("h1")
            title = h1.get_text().strip() if h1 else file_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        content = self._normalize_text(text)
        markets, lang = self._auto_fields(file_path, text)
        sections = parse_sections_from_text(content)

        return ParsedFile(
            title=title,
            content=content,
            doc_type="faq",
            source=file_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
            metadata={},
            sections=sections,
            markets=markets,
            lang=lang,
        )


# -------------------------------------------------------------------------
# 统一摄取器
# -------------------------------------------------------------------------
class FileIngestor:
    _parsers: dict[str, FileParser] = {
        ".pdf": PDFParser(),
        ".docx": DOCXParser(),
        ".html": HTMLParser(),
        ".htm": HTMLParser(),
    }

    @classmethod
    def can_parse(cls, file_path: str) -> bool:
        ext = "." + file_path.rsplit(".", 1)[-1].lower()
        return ext in cls._parsers

    @classmethod
    def parse(cls, file_path: str) -> ParsedFile:
        ext = "." + file_path.rsplit(".", 1)[-1].lower()
        parser = cls._parsers.get(ext)
        if parser is None:
            raise ValueError(f"Unsupported file type: {ext}")
        return parser.parse(file_path)

    @classmethod
    def list_supported_types(cls) -> list[str]:
        return list(cls._parsers.keys())