"""
术语归一化模块 — 对提取的术语组进行标准化，生成替换映射。

优先级（从高到低）:
  ① 用户术语表 glossary: {source_term → target_term}
  ② 出现次数最多的变体
  ③ 首次出现的变体（按页码升序）
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from services.ai_service import chat_json_precise

logger = logging.getLogger("tri_doc.term_normalizer")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class NormalizeResult:
    """归一化结果。"""

    total_terms: int = 0  # 处理术语总数（组数）
    normalized: int = 0  # 实际归一化数量（存在变体的组）
    from_glossary: int = 0  # 来自术语表的匹配
    from_frequency: int = 0  # 来自频次优先
    from_first_occurrence: int = 0  # 来自首次出现
    from_gpt: int = 0  # GPT 语义判断解决
    skipped: int = 0  # 跳过的（拆解失败等）
    # 替换映射: {variant_text → standard_text}
    replacement_map: dict[str, str] = field(default_factory=dict)
    # 详细报告
    details: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 归一化主流程
# ---------------------------------------------------------------------------
def normalize_terms(
    term_groups: list[dict],
    glossary: dict[str, str] | None = None,
    *,
    language: str = "ja",
) -> NormalizeResult:
    """
    对 term_groups 中的每组术语，按优先级规则选出标准译法，
    构建 variant_text → standard_text 的全局替换映射。

    Args:
        term_groups: 术语分组（来自 term_extractor）
        glossary: 用户术语表 {source_term: target_term}
        language: 文档语言（用于 GPT prompt）

    Returns:
        NormalizeResult: 含替换映射和统计信息
    """
    if glossary is None:
        glossary = {}

    result = NormalizeResult()
    result.total_terms = len(term_groups)

    for group in term_groups:
        variants: list[dict] = group.get("variants", [])
        if not variants:
            result.skipped += 1
            continue

        standard = group.get("standard", "")
        chosen_method = "unchanged"

        # ----- 优先级 ①: 用户术语表 -----
        glossary_hit = _match_glossary(variants, standard, glossary)
        if glossary_hit:
            standard = glossary_hit
            chosen_method = "glossary"
            result.from_glossary += 1

        # ----- 优先级 ②: 出现次数最多 -----
        elif len(variants) > 1:
            # 先检查 GPT 是否已给出 standard
            gpt_provided = group.get("standard", "")
            if gpt_provided and any(v["text"] == gpt_provided for v in variants):
                # 用频次验证 GPT 的选择
                by_freq = _pick_by_frequency(variants)
                if by_freq == gpt_provided:
                    standard = gpt_provided
                    chosen_method = "gpt_verified_by_freq"
                    result.from_gpt += 1
                else:
                    # GPT 和频次不一致 → 用 GPT 二次确认
                    standard = _gpt_disambiguate(
                        variants, gpt_provided, by_freq, language
                    )
                    chosen_method = "gpt_disambiguated"
                    result.from_gpt += 1
            else:
                # 纯频次优先
                standard = _pick_by_frequency(variants)
                chosen_method = "frequency"
                result.from_frequency += 1

        # ----- 优先级 ③: 首次出现 -----
        elif len(variants) == 1:
            standard = variants[0]["text"]
            chosen_method = "single_variant"
        else:
            standard = _pick_by_first_page(variants)
            chosen_method = "first_occurrence"
            result.from_first_occurrence += 1

        # ----- 构建替换映射 -----
        if len(variants) > 1:
            result.normalized += 1
        for v in variants:
            if v["text"] != standard:
                result.replacement_map[v["text"]] = standard

        result.details.append(
            {
                "concept_cn": group.get("concept_cn", standard),
                "standard": standard,
                "variants": [v["text"] for v in variants],
                "method": chosen_method,
            }
        )

    logger.info(
        "归一化完成: total=%d normalized=%d glossary=%d freq=%d first=%d gpt=%d skipped=%d",
        result.total_terms,
        result.normalized,
        result.from_glossary,
        result.from_frequency,
        result.from_first_occurrence,
        result.from_gpt,
        result.skipped,
    )
    return result


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
def _match_glossary(
    variants: list[dict],
    current_standard: str,
    glossary: dict[str, str],
) -> str | None:
    """检查变体列表中是否有命中术语表的。"""
    for v in variants:
        if v["text"] in glossary:
            logger.debug("术语表命中: %s → %s", v["text"], glossary[v["text"]])
            return glossary[v["text"]]
    if current_standard and current_standard in glossary:
        return glossary[current_standard]
    return None


def _pick_by_frequency(variants: list[dict]) -> str:
    """选出现次数最多的变体，平局时选首个。"""
    return max(variants, key=lambda v: (v.get("count", 0), -min(v.get("pages", [999]))))[
        "text"
    ]


def _pick_by_first_page(variants: list[dict]) -> str:
    """选首次出现的变体（页码最小的）。"""
    best = min(variants, key=lambda v: min(v.get("pages", [999])))
    return best["text"]


def _gpt_disambiguate(
    variants: list[dict],
    gpt_choice: str,
    freq_choice: str,
    language: str,
) -> str:
    """
    GPT 和频次选择不一致时，让 DeepSeek 做最终仲裁。
    失败则回退到频次优先。
    """
    variant_texts = [v["text"] for v in variants]
    system_prompt = (
        f"你是一位术语标准化专家。当前处理 {language} 文档的术语归一化。\n"
        f"【情境】同一概念出现了多种译法变体，已有两个推荐方案，需要你做最终裁定。\n"
        f"【评判标准（按优先级）】\n"
        f"  1. 专业性：最能准确表达原概念的译法\n"
        f"  2. 一致性：与商务文档惯用表达一致\n"
        f"  3. 正式度：正式场合更得体的表达\n"
        f"  4. 简洁性：在同等条件下，更短的表达优先\n"
        f"【输出】JSON: {{\"chosen\": \"选择的译法\", \"reason\": \"选择理由（20字以内）\"}}"
    )
    user_message = (
        f"变体列表: {json.dumps(variant_texts, ensure_ascii=False)}\n"
        f"A方案(GPT推荐): {gpt_choice}\n"
        f"B方案(频次优先): {freq_choice}\n"
        f"\n请做最终裁定，在 A 或 B 中选一个，或从变体列表中另选更优的。"
    )
    try:
        raw = chat_json_precise(system_prompt, user_message)
        decision = json.loads(raw)
        chosen = decision.get("chosen", freq_choice)
        logger.debug("DeepSeek 仲裁: %s → %s (reason: %s)", variant_texts, chosen, decision.get("reason"))
        return chosen
    except Exception as exc:
        logger.warning("DeepSeek 仲裁失败: %s，回退到频次优先", exc)
        return freq_choice
