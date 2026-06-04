"""
全局替换模块 — 将归一化后的术语映射逐页回写到翻译结果。

策略：
- 按术语长度降序替换（避免短词误匹配长词的一部分）
- 记录每页的替换明细
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("tri_doc.term_replacer")


@dataclass
class ReplaceResult:
    """替换结果。"""

    pages: list[dict] = field(default_factory=list)
    # 每页的替换记录
    page_log: list[dict] = field(default_factory=list)
    # 全局统计
    total_replacements: int = 0
    pages_modified: int = 0
    terms_replaced: int = 0  # 被替换的 distinct variant 数量


def global_replace(
    pages: list[dict],
    replacement_map: dict[str, str],
    *,
    text_key: str = "text",
) -> ReplaceResult:
    """
    逐页执行术语替换。

    Args:
        pages: [{"page": int, "text": str}, ...]
        replacement_map: {variant_text → standard_text}
        text_key: 文本字段名（默认 "text"）

    Returns:
        ReplaceResult: 替换后的页面 + 替换明细
    """
    if not replacement_map:
        logger.info("替换映射为空，无需替换")
        return ReplaceResult(
            pages=[{**p} for p in pages],
            page_log=[],
            total_replacements=0,
            pages_modified=0,
            terms_replaced=0,
        )

    # 构建单次扫描正则：按长度降序，每个位置只匹配一次
    # 使用 \b 单词边界避免子串误匹配（如 "Spec" 匹配到 "Specification" 内部）
    sorted_variants = sorted(
        replacement_map.items(),
        key=lambda kv: len(kv[0]),
        reverse=True,
    )

    # 按长度降序排列（最长的先写 → regex 交替匹配优先最长）
    sorted_variants = sorted(
        replacement_map.items(),
        key=lambda kv: len(kv[0]),
        reverse=True,
    )

    # 构建 pattern + 原始 variant → standard 映射
    pattern_parts: list[str] = []
    variant_to_standard: dict[str, str] = {}

    for variant, standard in sorted_variants:
        if variant == standard:
            continue
        if " " in variant:
            # 多词短语：直接匹配原文
            pat = re.escape(variant)
        else:
            # 单词：用 \b 边界避免子串误匹配
            pat = r"\b" + re.escape(variant) + r"\b"
        pattern_parts.append(f"({pat})")
        # key 使用 ORIGINAL variant（未 escape），因为 m.group(0) 返回原文
        variant_to_standard[variant] = standard

    if not pattern_parts:
        return ReplaceResult(
            pages=[{**p} for p in pages],
            page_log=[],
            total_replacements=0,
            pages_modified=0,
            terms_replaced=0,
        )

    combined_re = re.compile("|".join(pattern_parts))

    result = ReplaceResult()
    result.terms_replaced = len(set(replacement_map.values()))
    replaced_pages: list[dict] = []

    for page in pages:
        original_text = page.get(text_key, "")
        page_replacements: dict[str, dict] = {}  # variant → {standard, count}

        def _replacer(m: re.Match) -> str:
            matched_text = m.group(0)
            standard = variant_to_standard.get(matched_text, matched_text)
            if matched_text != standard:
                if matched_text not in page_replacements:
                    page_replacements[matched_text] = {"standard": standard, "count": 0}
                page_replacements[matched_text]["count"] += 1
            return standard

        modified_text = combined_re.sub(_replacer, original_text)

        replaced_in_page = sum(r["count"] for r in page_replacements.values())
        page_repl_list = [
            {"variant": v, "standard": r["standard"], "count": r["count"]}
            for v, r in page_replacements.items()
        ]

        result.total_replacements += replaced_in_page

        new_page = {**page, text_key: modified_text}
        replaced_pages.append(new_page)

        if page_replacements:
            result.pages_modified += 1
            result.page_log.append(
                {
                    "page": page.get("page", page.get("slide", 0)),
                    "replacements": page_repl_list,
                    "total_in_page": replaced_in_page,
                }
            )
            logger.debug(
                "页 %d: %d 处替换 — %s",
                page.get("page", page.get("slide", 0)),
                replaced_in_page,
                [(r["variant"][:20], r["standard"][:20]) for r in page_repl_list],
            )

    result.pages = replaced_pages
    logger.info(
        "全局替换完成: %d 处替换, %d/%d 页被修改, %d 个术语被归一化",
        result.total_replacements,
        result.pages_modified,
        len(pages),
        result.terms_replaced,
    )
    return result
