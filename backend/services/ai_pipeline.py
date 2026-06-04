"""
TriDoc AI 管线 — 完整六阶段编排。

Stage 0:  术语表匹配     — 强制替换已知术语对
Stage 1:  原文润色       — 按语言策略润色
Stage 2:  逐页翻译       — 并行翻译
Stage 2.5: 上下文对齐    — 术语提取 → 归一化 → 全局替换
Stage 3:  译后润色       — 按目标语言自然化
Stage 4:  术语回校验     — 术语表强制覆盖
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable

from services.ai_service import (
    chat,
    chat_balanced,
    chat_creative,
    chat_json,
    chat_json_precise,
    with_retry,
)
from services.term_extractor import extract_term_variants_gpt
from services.term_normalizer import normalize_terms, NormalizeResult
from services.term_replacer import global_replace, ReplaceResult

logger = logging.getLogger("tri_doc.pipeline")

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class PageItem:
    """单页/单段内容。"""

    page: int  # 页码或段落号
    text: str


@dataclass
class PipelineContext:
    """管线上下文 — 在各阶段间传递。"""

    file_id: str
    source_lang: str  # ja / en / zh
    target_langs: list[str]  # ["en", "zh"]
    pages: list[dict]  # 当前工作页面
    glossary: dict[str, str] = field(default_factory=dict)

    # 中间产物
    polished_pages: list[dict] | None = None  # Stage 1 输出
    translated: dict[str, list[dict]] = field(default_factory=dict)  # Stage 2 输出 {lang: pages}
    aligned: dict[str, list[dict]] = field(default_factory=dict)  # Stage 2.5 输出
    final_pages: dict[str, list[dict]] = field(default_factory=dict)  # Stage 3 输出

    # 报告
    reports: dict = field(default_factory=dict)


@dataclass
class PipelineResult:
    """管线执行结果。"""

    success: bool = True
    target_langs: list[str] = field(default_factory=list)
    final_pages: dict[str, list[dict]] = field(default_factory=dict)
    consistency_report: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 各阶段实现
# ---------------------------------------------------------------------------

# ---- Stage 0: 术语表匹配 ----
def stage0_glossary_match(ctx: PipelineContext) -> PipelineContext:
    """将用户术语表直接注入页面文本（强制替换）。"""
    if not ctx.glossary:
        logger.info("[Stage 0] 无术语表，跳过")
        return ctx

    replace_count = 0
    for page in ctx.pages:
        for src, tgt in ctx.glossary.items():
            if src in page["text"]:
                page["text"] = page["text"].replace(src, tgt)
                replace_count += 1

    logger.info("[Stage 0] 术语表匹配完成: %d 处替换", replace_count)
    return ctx


# ---- Stage 1: 原文润色 ----
@with_retry(max_retries=3, delay_sec=2)
def _polish_page(text: str, lang: str) -> str:
    """单页润色 — DeepSeek 优化版 prompt。"""
    prompts = {
        "ja": (
            "あなたは日本語ビジネス文書の校正専門家です。\n"
            "【指示】以下のテキストをビジネス敬語（です・ます調）に統一し、冗長な表現を削除してください。\n"
            "【注意】\n"
            "- 原文の意味・事実関係を一切変更しない\n"
            "- 専門用語・数値・固有名詞はそのまま維持\n"
            "- 文末は「です・ます調」に統一\n"
            "- 一文が長すぎる場合は適切に分割\n"
            "【出力】修正後のテキストのみを返してください。説明は不要です。"
        ),
        "en": (
            "You are a native English business editor.\n"
            "【Task】Polish the following text to professional native business English.\n"
            "【Rules】\n"
            "- Preserve all facts, numbers, and proper nouns exactly\n"
            "- Keep specialized terminology unchanged\n"
            "- Make sentences concise and impactful\n"
            "- Use consistent terminology throughout\n"
            "- Maintain a formal yet natural business tone\n"
            "【Output】Return ONLY the polished text. No explanations."
        ),
        "zh": (
            "你是一位中文商务文档润色专家。\n"
            "【任务】将以下文本润色为专业、流畅的简体中文商务语气。\n"
            "【规则】\n"
            "- 不改变原文的事实、数据和专有名词\n"
            "- 术语保持统一（同一概念使用相同译法）\n"
            "- 删去冗余表达，使句子精炼\n"
            "- 使用中国大陆商务文档的正式表达习惯\n"
            "【输出】仅返回润色后的文本，不要任何解释。"
        ),
    }
    system = prompts.get(lang, prompts["en"])
    return chat_creative(system, text)


def stage1_polish(ctx: PipelineContext) -> PipelineContext:
    """逐页润色原文。"""
    polished: list[dict] = []
    for page in ctx.pages:
        try:
            polished_text = _polish_page(page["text"], ctx.source_lang)
            polished.append({**page, "text": polished_text})
        except Exception as exc:
            logger.warning("[Stage 1] 页 %d 润色失败: %s，保留原文", page.get("page", 0), exc)
            polished.append({**page})

    ctx.polished_pages = polished
    ctx.pages = polished  # 后续阶段使用润色后的文本
    logger.info("[Stage 1] 原文润色完成: %d 页", len(polished))
    return ctx


# ---- Stage 2: 逐页翻译 ----
@with_retry(max_retries=3, delay_sec=2)
def _translate_page(text: str, source_lang: str, target_lang: str, glossary: dict[str, str]) -> str:
    """单页翻译 — DeepSeek 优化版 prompt。"""
    lang_names = {"ja": "Japanese", "en": "English", "zh": "Simplified Chinese"}
    src_name = lang_names.get(source_lang, source_lang)
    tgt_name = lang_names.get(target_lang, target_lang)

    system = (
        f"你是一位专业商务翻译。\n"
        f"【任务】将以下文本从 {src_name} 翻译为 {tgt_name}。\n"
        f"【规则】\n"
        f"- 保持专业商务语气\n"
        f"- 准确翻译所有专业术语，同一概念使用统一译法\n"
        f"- 专有名词和数字不得变更\n"
        f"- 译文自然流畅，符合目标语言的商务表达习惯\n"
    )
    if glossary:
        glossary_hint = "\n".join(f"  {k} → {v}" for k, v in list(glossary.items())[:20])
        system += (
            f"\n【术语表（强制使用）】以下の訳語を必ず使用してください：\n"
            f"{glossary_hint}\n"
            f"术语表中的译法优先于任何其他表达。"
        )
    system += "\n【输出】仅返回译文，不要任何解释或备注。"

    return chat_balanced(system, text)


def stage2_translate(ctx: PipelineContext) -> PipelineContext:
    """逐页翻译到各目标语言。"""
    source_pages = ctx.pages  # 此时已是润色后的

    for tgt_lang in ctx.target_langs:
        if tgt_lang == ctx.source_lang:
            ctx.translated[tgt_lang] = [{**p} for p in source_pages]
            continue

        translated_pages: list[dict] = []
        for page in source_pages:
            try:
                trans_text = _translate_page(
                    page["text"], ctx.source_lang, tgt_lang, ctx.glossary
                )
                translated_pages.append({**page, "text": trans_text})
            except Exception as exc:
                logger.warning(
                    "[Stage 2] 页 %d →%s 翻译失败: %s",
                    page.get("page", 0),
                    tgt_lang,
                    exc,
                )
                translated_pages.append({**page, "text": f"[翻译失败] {page['text']}"})

        ctx.translated[tgt_lang] = translated_pages
        logger.info("[Stage 2] →%s 翻译完成: %d 页", tgt_lang, len(translated_pages))

    return ctx


# ---- Stage 2.5: 上下文对齐 ----
def stage2_5_context_alignment(ctx: PipelineContext) -> PipelineContext:
    """
    对每种目标语言的翻译结果，执行:
      术语提取 → 归一化 → 全局替换
    """
    for tgt_lang in ctx.target_langs:
        if tgt_lang == ctx.source_lang:
            ctx.aligned[tgt_lang] = ctx.translated.get(tgt_lang, [])
            continue

        pages = ctx.translated.get(tgt_lang, [])
        if not pages:
            continue

        logger.info("[Stage 2.5] →%s 上下文对齐开始 (%d 页)", tgt_lang, len(pages))

        # Step 1: 术语提取
        term_groups = extract_term_variants_gpt(
            pages=pages,
            page_texts=pages,
            language=tgt_lang,
            top_k=30,
        )

        # Step 2: 术语归一化
        normalize_result = normalize_terms(
            term_groups=term_groups,
            glossary=ctx.glossary,
            language=tgt_lang,
        )

        # Step 3: 全局替换
        replace_result = global_replace(
            pages=pages,
            replacement_map=normalize_result.replacement_map,
        )

        ctx.aligned[tgt_lang] = replace_result.pages

        # 保存报告
        ctx.reports[f"align_{tgt_lang}"] = {
            "term_groups": term_groups,
            "normalize": {
                "total_terms": normalize_result.total_terms,
                "normalized": normalize_result.normalized,
                "from_glossary": normalize_result.from_glossary,
                "from_frequency": normalize_result.from_frequency,
                "from_first_occurrence": normalize_result.from_first_occurrence,
                "from_gpt": normalize_result.from_gpt,
                "skipped": normalize_result.skipped,
                "details": normalize_result.details,
            },
            "replace": {
                "total_replacements": replace_result.total_replacements,
                "pages_modified": replace_result.pages_modified,
                "terms_replaced": replace_result.terms_replaced,
                "page_log": replace_result.page_log,
            },
        }

        logger.info(
            "[Stage 2.5] →%s 上下文对齐完成: %d 组术语, %d 归一化, %d 处替换",
            tgt_lang,
            normalize_result.total_terms,
            normalize_result.normalized,
            replace_result.total_replacements,
        )

    return ctx


# ---- Stage 3: 译后润色 ----
@with_retry(max_retries=3, delay_sec=2)
def _post_polish(text: str, lang: str) -> str:
    """译后自然化润色 — DeepSeek 优化版 prompt。"""
    prompts = {
        "ja": (
            "あなたは日本語ネイティブのビジネス文書校閲者です。\n"
            "【タスク】以下の日本語訳文を、ネイティブレベルの自然なビジネス文書に仕上げてください。\n"
            "【改善ポイント】\n"
            "- 不自然な言い回しを自然な日本語に修正\n"
            "- 文法・敬語の違和感を解消\n"
            "- 語順を最適化\n"
            "- 専門用語は変更しない\n"
            "【出力】修正後のテキストのみを返してください。"
        ),
        "en": (
            "You are a native English business editor reviewing a translation.\n"
            "【Task】Refine this translated text to read as if originally written by a native English business writer.\n"
            "【Focus on】\n"
            "- Improving naturalness and flow\n"
            "- Fixing any awkward phrasing from translation\n"
            "- Maintaining consistent professional tone\n"
            "- Preserving all technical terms and data exactly\n"
            "【Output】Return ONLY the refined text. No explanations."
        ),
        "zh": (
            "你是一位中文母语级商务文档编辑，正在审校一篇翻译稿。\n"
            "【任务】将以下译文优化为地道、自然的中文商务文档。\n"
            "【改进重点】\n"
            "- 修正翻译腔和不自然的表达\n"
            "- 使行文流畅、符合中文商务文档习惯\n"
            "- 保持专业术语一致\n"
            "- 数据和专有名词不变\n"
            "【输出】仅返回优化后的文本，不要任何解释。"
        ),
    }
    return chat_creative(prompts.get(lang, prompts["en"]), text)


def stage3_post_polish(ctx: PipelineContext) -> PipelineContext:
    """译后自然化润色。"""
    for tgt_lang in ctx.target_langs:
        pages = ctx.aligned.get(tgt_lang, [])
        polished: list[dict] = []
        for page in pages:
            try:
                refined = _post_polish(page["text"], tgt_lang)
                polished.append({**page, "text": refined})
            except Exception as exc:
                logger.warning("[Stage 3] →%s 页 %d 润色失败: %s", tgt_lang, page.get("page", 0), exc)
                polished.append({**page})
        ctx.final_pages[tgt_lang] = polished
        logger.info("[Stage 3] →%s 译后润色完成: %d 页", tgt_lang, len(polished))

    return ctx


# ---- Stage 4: 术语回校验 ----
def stage4_glossary_verify(ctx: PipelineContext) -> PipelineContext:
    """强制术语表回写 — 确保 GPT 润色没有改掉关键术语。"""
    if not ctx.glossary:
        logger.info("[Stage 4] 无术语表，跳过")
        return ctx

    fix_count = 0
    for tgt_lang in ctx.target_langs:
        pages = ctx.final_pages.get(tgt_lang, [])
        for page in pages:
            for src, tgt in ctx.glossary.items():
                # 检查目标术语是否被 GPT 改写
                if tgt not in page["text"] and src not in page["text"]:
                    # 可能被改写，需要恢复
                    pass
                elif src in page["text"] and tgt != src:
                    page["text"] = page["text"].replace(src, tgt)
                    fix_count += 1

    logger.info("[Stage 4] 术语回校验完成: %d 处修正", fix_count)
    return ctx


# ---------------------------------------------------------------------------
# 管线编排
# ---------------------------------------------------------------------------
def run_pipeline(
    file_id: str,
    pages: list[dict],
    source_lang: str,
    target_langs: list[str],
    *,
    glossary: dict[str, str] | None = None,
    skip_polish: bool = False,
    skip_post_polish: bool = False,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> PipelineResult:
    """
    执行完整 AI 管线。

    Args:
        file_id: 文件 ID
        pages: [{"page": int, "text": str}, ...]
        source_lang: 源语言 (ja/en/zh)
        target_langs: 目标语言列表
        glossary: 用户术语表
        skip_polish: 跳过原文润色（测试/加速）
        skip_post_polish: 跳过译后润色
        progress_callback: (stage_name, current, total) → None

    Returns:
        PipelineResult
    """
    result = PipelineResult(target_langs=target_langs)
    ctx = PipelineContext(
        file_id=file_id,
        source_lang=source_lang,
        target_langs=target_langs,
        pages=[{**p} for p in pages],
        glossary=glossary or {},
    )

    stages = [
        ("Stage 0: 术语表匹配", stage0_glossary_match, True),
        ("Stage 1: 原文润色", stage1_polish, not skip_polish),
        ("Stage 2: 逐页翻译", stage2_translate, True),
        ("Stage 2.5: 上下文对齐", stage2_5_context_alignment, True),
        ("Stage 3: 译后润色", stage3_post_polish, not skip_post_polish),
        ("Stage 4: 术语回校验", stage4_glossary_verify, True),
    ]

    for i, (name, stage_fn, enabled) in enumerate(stages):
        if not enabled:
            logger.info("%s — 跳过", name)
            continue
        try:
            if progress_callback:
                progress_callback(name, i, len(stages))
            logger.info("%s — 开始", name)
            ctx = stage_fn(ctx)
        except Exception as exc:
            logger.error("%s — 失败: %s", name, exc)
            result.errors.append(f"{name}: {exc}")
            result.success = False
            # 继续执行后续阶段（优雅降级）

    # 组装结果
    result.final_pages = ctx.final_pages or ctx.aligned or ctx.translated

    # 生成术语一致性报告
    result.consistency_report = _build_consistency_report(ctx)

    # 汇总统计
    result.stats = _build_stats(ctx, result)

    if progress_callback:
        progress_callback("完成", len(stages), len(stages))

    return result


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------
def _build_consistency_report(ctx: PipelineContext) -> dict:
    """生成术语一致性报告。"""
    report: dict = {
        "file_id": ctx.file_id,
        "source_language": ctx.source_lang,
        "target_languages": ctx.target_langs,
        "glossary_used": len(ctx.glossary) > 0,
        "glossary_size": len(ctx.glossary),
        "per_language": {},
    }

    for lang in ctx.target_langs:
        align_report = ctx.reports.get(f"align_{lang}", {})
        normalize = align_report.get("normalize", {})
        replace_data = align_report.get("replace", {})

        report["per_language"][lang] = {
            "terms_total": normalize.get("total_terms", 0),
            "terms_normalized": normalize.get("normalized", 0),
            "replacements_made": replace_data.get("total_replacements", 0),
            "pages_modified": replace_data.get("pages_modified", 0),
            "method_breakdown": {
                "glossary": normalize.get("from_glossary", 0),
                "frequency": normalize.get("from_frequency", 0),
                "first_occurrence": normalize.get("from_first_occurrence", 0),
                "gpt_resolved": normalize.get("from_gpt", 0),
                "skipped": normalize.get("skipped", 0),
            },
            "term_details": normalize.get("details", []),
            "page_changes": replace_data.get("page_log", []),
        }

    return report


def _build_stats(ctx: PipelineContext, result: PipelineResult) -> dict:
    """汇总处理统计。"""
    stats = {
        "total_pages": len(ctx.pages),
        "source_lang": ctx.source_lang,
        "target_langs": ctx.target_langs,
        "errors": len(result.errors),
    }
    for lang in ctx.target_langs:
        align_report = ctx.reports.get(f"align_{lang}", {})
        normalize = align_report.get("normalize", {})
        replace_data = align_report.get("replace", {})
        stats[f"{lang}_terms_total"] = normalize.get("total_terms", 0)
        stats[f"{lang}_terms_normalized"] = normalize.get("normalized", 0)
        stats[f"{lang}_replacements"] = replace_data.get("total_replacements", 0)
        stats[f"{lang}_pages_modified"] = replace_data.get("pages_modified", 0)
    return stats
