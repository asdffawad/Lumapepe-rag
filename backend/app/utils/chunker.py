"""
文本分块器
支持按段落和固定长度两种切分策略，保留 overlap 以维持上下文连贯性
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class DocSectionType(str, Enum):
    """章节级类型 — 决定合并策略（只合并同类型）和 embedding 质量"""
    HEADING    = "heading"
    BODY       = "body"
    BULLET     = "bullet"
    ORDERED    = "ordered"
    TABLE_ROW  = "table_row"
    CODE_BLOCK = "code_block"
    NOTE       = "note"
    DIVIDER    = "divider"


@dataclass
class Section:
    """单个章节（新增结构体）"""
    text: str
    section_type: str = "body"
    level: int = 0
    number_prefix: str = ""


@dataclass
class Chunk:
    """单个文本块（重构后）"""
    content: str
    doc_id: int
    chunk_index: int
    source: str
    doc_type: str
    # ---- 新增字段 ----
    section_type: str = "body"
    markets: list[str] = field(default_factory=list)
    lang: str = ""
    hierarchy: dict = field(default_factory=dict)
    embedding_hint: str = ""


class TextChunker:
    """
    文本分块器（重构版）

    策略1 - 按段落切分：识别自然段落边界，只合并同 section_type
    策略2 - 固定长度切分：按 token 数切分，适合非结构化长文本
    """

    def __init__(self, chunk_size: int = 512, overlap: int = 50, unit: str = "token"):
        """
        Args:
            chunk_size: 每个 chunk 的最大单位（默认 512 tokens）
            overlap:    相邻 chunk 之间的重叠 tokens 数（保持上下文连贯）
            unit:       "token" | "char"，默认 token
        """
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.unit = unit  # "token" | "char"

    # -------------------------------------------------------------------------
    # Token 计数（估算）
    # -------------------------------------------------------------------------
    def _count_tokens(self, text: str) -> int:
        """估算 token 数（中文 ~1.5 字符/token，英文 ~0.25 token/字符）"""
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        other_chars = len(text) - chinese_chars
        return int(chinese_chars * 1.5 + other_chars * 0.25)

    def _count_tokens_fast(self, text: str) -> int:
        """更快（但精度略低）的 token 估算"""
        return self._count_tokens(text)

    def _truncate_to_tokens(self, text: str, max_tokens: int) -> str:
        """按 token 数截断文本（按字符逐个截断）"""
        if self.unit == "char":
            return text[:max_tokens]
        tokens = 0
        result = []
        for ch in text:
            cp = ord(ch)
            tok = 1.5 if 0x4e00 <= cp <= 0x9fff else 0.25
            if tokens + tok > max_tokens:
                break
            result.append(ch)
            tokens += tok
        return "".join(result)

    # -------------------------------------------------------------------------
    # Section 解析（替代正则硬拆编号）
    # -------------------------------------------------------------------------
    def _parse_sections(self, text: str) -> list[Section]:
        """
        解析文本为 Section 列表。
        使用缩进级别 + 行首模式判断层级和类型，而非正则拆解编号。
        兼容：一. 二. / 1. 2. / ①② / 1.2.3 / Markdown # / 无缩进中文
        """
        lines = text.split("\n")
        sections: list[Section] = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                sections.append(Section(text="", section_type="divider", level=0, number_prefix=""))
                continue

            indent = len(line) - len(line.lstrip())
            level = indent // 2  # 每缩进 2 个空格算一级

            # 判断 section_type（优先级：Markdown heading > bullet > ordered > note > body）
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

            # 提取编号前缀（保留给 hierarchy 用）
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

    def _build_hierarchy(self, sections: list[Section], flat_index: int) -> dict:
        """
        从 Section 列表构建层级索引。
        例如: [Heading("1 政策总则"), Body("..."), Heading("2 退款规则"), Body("...")]
        返回 {"1": "政策总则", "2": "退款规则", "_flat_index": N}
        使用 stack 维护当前路径，无需正则拆解编号。
        """
        stack: list[tuple[int, str]] = []
        result: dict = {"_flat_index": flat_index}

        for sec in sections:
            if sec.section_type == "divider":
                continue

            level = sec.level or 1
            prefix = sec.number_prefix

            # 弹出 >= 当前 level 的项（保持 stack 为当前路径）
            stack = [(l, t) for l, t in stack if l < level]
            stack.append((level, prefix or f"l{level}"))

            # 写入层级路径（去重时后者覆盖前者）
            path_key = ".".join(t for _, t in stack)
            result[path_key] = sec.text[:50]

        return result

    def _get_tail_text(self, sections: list[Section], max_tokens: int) -> str:
        """取最后几个 section 的末尾部分作为 overlap 文本"""
        if not sections:
            return ""
        result = []
        tokens = 0
        # 从后向前取
        for sec in reversed(sections):
            sec_tokens = self._count_tokens(sec.text)
            if tokens + sec_tokens <= max_tokens:
                result.insert(0, sec.text)
                tokens += sec_tokens
            else:
                truncated = self._truncate_to_tokens(sec.text, max_tokens - tokens)
                if truncated:
                    result.insert(0, truncated)
                break
        return "\n".join(result)

    def _make_chunk(
        self,
        sections: list[Section],
        doc_id: int,
        source: str,
        doc_type: str,
        markets: list[str] | None,
        lang: str,
        hierarchy: dict,
        flat_index: int,
    ) -> Chunk:
        """将 Section 列表合并为一个 Chunk"""
        content = "\n".join(s.text for s in sections if s.text)
        primary_type = sections[0].section_type if sections else "body"
        return Chunk(
            content=content,
            doc_id=doc_id,
            chunk_index=flat_index,
            source=source,
            doc_type=doc_type,
            section_type=primary_type,
            markets=markets or [],
            lang=lang,
            hierarchy=hierarchy,
            embedding_hint="",
        )

    # -------------------------------------------------------------------------
    # 主入口：按段落切分（只合并同 section_type）
    # -------------------------------------------------------------------------
    def chunk_by_paragraph(
        self,
        text: str,
        doc_id: int,
        source: str,
        doc_type: str,
        markets: list[str] | None = None,
        lang: str = "",
        section_type: str = "body",
        hierarchy: dict | None = None,
    ) -> list[Chunk]:
        """
        按段落切分文本，优先保证段落完整性。

        核心变化：
        - 只合并同 section_type 的段落（类型切换即落块）
        - 超长段落 fallback 到 _sliding_window
        - Token-based 截断（中英文公平）
        - Overlap 用 token 而非字符数

        Args:
            text:       原始文本
            doc_id:     文档ID
            source:     来源标识（如 "POLICY001"）
            doc_type:   文档类型（product/policy/faq/order）
            markets:    市场列表（如 ["CN", "VN"]）
            lang:       语言代码（zh/en/vi）
            section_type: 降级场景下默认类型
            hierarchy:   已有层级索引（传入则复用）

        Returns:
            Chunk 列表
        """
        sections = self._parse_sections(text)
        chunks: list[Chunk] = []
        current_group: list[Section] = []
        current_tokens = 0
        flat_index = 0

        for sec in sections:
            if sec.section_type == "divider":
                continue

            sec_tokens = self._count_tokens(sec.text)

            # 类型变化 → 立即落块（即使没满）
            if current_group and sec.section_type != current_group[0].section_type:
                hierarchy = self._build_hierarchy(current_group, flat_index)
                chunks.append(self._make_chunk(current_group, doc_id, source, doc_type, markets, lang, hierarchy, flat_index))
                flat_index += 1
                current_group = []
                current_tokens = 0

            # 超长段落 fallback 到 sliding window
            if sec_tokens > self.chunk_size:
                if current_group:
                    hierarchy = self._build_hierarchy(current_group, flat_index)
                    chunks.append(self._make_chunk(current_group, doc_id, source, doc_type, markets, lang, hierarchy, flat_index))
                    flat_index += 1
                    current_group = []
                    current_tokens = 0

                sw_start = len(chunks)
                chunks.extend(self._sliding_window_sections([sec], doc_id, source, doc_type, markets, lang, hierarchy or {}, flat_index))
                flat_index += len(chunks) - sw_start
                continue

            if current_tokens + sec_tokens <= self.chunk_size:
                current_group.append(sec)
                current_tokens += sec_tokens
            else:
                # 落块，然后用 overlap 开启新块
                if current_group:
                    hierarchy = self._build_hierarchy(current_group, flat_index)
                    chunks.append(self._make_chunk(current_group, doc_id, source, doc_type, markets, lang, hierarchy, flat_index))
                    flat_index += 1

                tail = self._get_tail_text(current_group, self.overlap)
                current_group = [Section(text=tail, section_type=current_group[-1].section_type if current_group else "body", level=0, number_prefix="")]
                current_tokens = self._count_tokens(tail) + sec_tokens
                current_group.append(sec)

        if current_group:
            hierarchy = self._build_hierarchy(current_group, flat_index)
            chunks.append(self._make_chunk(current_group, doc_id, source, doc_type, markets, lang, hierarchy, flat_index))

        # 重建 flat chunk_index（确保连续）
        for i, chunk in enumerate(chunks):
            chunk.chunk_index = i

        return chunks

    def _sliding_window_sections(
        self,
        sections: list[Section],
        doc_id: int,
        source: str,
        doc_type: str,
        markets: list[str] | None,
        lang: str,
        hierarchy: dict,
        start_index: int,
    ) -> list[Chunk]:
        """
        对超长 Section 使用滑动窗口切分。
        step = chunk_size - overlap（50% 重叠）。
        """
        # 合并所有文本
        full_text = "\n".join(s.text for s in sections if s.text)
        if not full_text:
            return []

        chunks = []
        step = max(self.chunk_size - self.overlap, 1)
        index = start_index

        if self.unit == "token":
            # 按 token 滑动
            pos = 0
            while pos < len(full_text):
                window_text = self._truncate_to_tokens(full_text[pos:], self.chunk_size)
                chunks.append(Chunk(
                    content=window_text.strip(),
                    doc_id=doc_id,
                    chunk_index=index,
                    source=source,
                    doc_type=doc_type,
                    section_type=sections[0].section_type if sections else "body",
                    markets=markets or [],
                    lang=lang,
                    hierarchy=hierarchy.copy(),
                    embedding_hint="",
                ))
                index += 1
                pos += max(self.chunk_size - self.overlap, 1)
        else:
            # 按字符滑动
            for i in range(0, len(full_text), step):
                window = full_text[i:i + self.chunk_size]
                chunks.append(Chunk(
                    content=window.strip(),
                    doc_id=doc_id,
                    chunk_index=index,
                    source=source,
                    doc_type=doc_type,
                    section_type=sections[0].section_type if sections else "body",
                    markets=markets or [],
                    lang=lang,
                    hierarchy=hierarchy.copy(),
                    embedding_hint="",
                ))
                index += 1

        return chunks

    # -------------------------------------------------------------------------
    # 保留旧方法（向后兼容）
    # -------------------------------------------------------------------------
    def _sliding_window(
        self,
        text: str,
        doc_id: int,
        source: str,
        doc_type: str,
        start_index: int,
    ) -> list[Chunk]:
        """对超长文本使用滑动窗口切分（旧兼容版）"""
        chunks = []
        step = max(self.chunk_size - self.overlap, 1)
        index = start_index

        if self.unit == "token":
            pos = 0
            while pos < len(text):
                window_text = self._truncate_to_tokens(text[pos:], self.chunk_size)
                chunks.append(Chunk(
                    content=window_text.strip(),
                    doc_id=doc_id,
                    chunk_index=index,
                    source=source,
                    doc_type=doc_type,
                ))
                index += 1
                pos += step
        else:
            for i in range(0, len(text), step):
                window = text[i:i + self.chunk_size]
                chunks.append(Chunk(
                    content=window.strip(),
                    doc_id=doc_id,
                    chunk_index=index,
                    source=source,
                    doc_type=doc_type,
                ))
                index += 1

        return chunks

    def chunk_structured(
        self,
        data: dict,
        doc_id: int,
        source: str,
        doc_type: str,
        markets: list[str] | None = None,
        lang: str = "",
    ) -> list[Chunk]:
        """
        将结构化 JSON 对象转换为文本再分块
        策略：提取关键字段拼接为文本，保留原始结构信息
        """
        lines: list[str] = []

        for key in ("title", "name", "category"):
            if data.get(key):
                lines.append(f"{key}: {data[key]}")

        if data.get("content"):
            lines.append(f"内容: {data['content']}")
        if data.get("description"):
            lines.append(f"描述: {data['description']}")

        if data.get("specs"):
            specs = ", ".join(f"{k}={v}" for k, v in data["specs"].items())
            lines.append(f"规格: {specs}")

        for field_name in ("price", "stock"):
            if field_name in data:
                lines.append(f"{field_name}: {data[field_name]}")

        for field_name in ("applicable", "non_applicable", "conditions", "steps", "tips", "solutions"):
            if data.get(field_name):
                items = data[field_name]
                if isinstance(items, list):
                    lines.append(f"{field_name}: {'; '.join(items)}")

        combined_text = "\n".join(lines)

        if self._count_tokens(combined_text) <= self.chunk_size:
            return [
                Chunk(
                    content=combined_text,
                    doc_id=doc_id,
                    chunk_index=0,
                    source=source,
                    doc_type=doc_type,
                    markets=markets or [],
                    lang=lang,
                )
            ]

        return self.chunk_by_paragraph(combined_text, doc_id, source, doc_type, markets, lang)

    def chunk_text(
        self,
        text: str,
        doc_id: int,
        source: str,
        doc_type: str,
    ) -> list[Chunk]:
        """通用入口，自动判断使用哪种策略"""
        return self.chunk_by_paragraph(text, doc_id, source, doc_type)