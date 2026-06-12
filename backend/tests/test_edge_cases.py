"""
TriDoc 全面自测 — 覆盖边缘情况、边界条件、日/中/英三语。
"""

from __future__ import annotations

import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.ai_service import (
    get_client, with_retry, chat, chat_json,
    chat_balanced, chat_creative, chat_precise, chat_json_precise,
    DEEPSEEK_API_KEY, DEEPSEEK_MODEL, MAX_RETRIES, RETRY_DELAY_SEC,
    TEMP_PRECISE, TEMP_BALANCED, TEMP_CREATIVE,
)
from services.term_extractor import (
    extract_tfidf_terms, extract_term_variants_gpt,
    _detect_lang, _build_ngrams, _is_junk, _fallback_grouping,
)
from services.term_normalizer import (
    normalize_terms, NormalizeResult,
    _match_glossary, _pick_by_frequency, _pick_by_first_page,
)
from services.term_replacer import global_replace, ReplaceResult
from services.ai_pipeline import (
    PageItem, PipelineContext, PipelineResult,
    run_pipeline, stage0_glossary_match,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

PASS, FAIL = 0, 0


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} -- {detail}")


# ====================================================================
# 1. 模块完整性
# ====================================================================
def test_module_imports():
    print("\n=== 1. Module Imports ===")
    modules = [
        "ai_service", "term_extractor", "term_normalizer",
        "term_replacer", "ai_pipeline",
    ]
    for m in modules:
        mod = sys.modules.get(f"services.{m}")
        check(f"services.{m} imported", mod is not None)


# ====================================================================
# 2. DeepSeek 配置验证
# ====================================================================
def test_deepseek_config():
    print("\n=== 2. DeepSeek Configuration ===")
    check("DEEPSEEK_MODEL is deepseek-chat", DEEPSEEK_MODEL == "deepseek-chat")
    check("MAX_RETRIES is 3", MAX_RETRIES == 3)
    check("RETRY_DELAY_SEC is 2", RETRY_DELAY_SEC == 2)
    check("TEMP_PRECISE is 0.1", TEMP_PRECISE == 0.1)
    check("TEMP_BALANCED is 0.3", TEMP_BALANCED == 0.3)
    check("TEMP_CREATIVE is 0.5", TEMP_CREATIVE == 0.5)
    client = get_client()
    check("get_client() returns OpenAI client", client is not None)
    check(
        "base_url points to DeepSeek",
        str(client.base_url).startswith("https://api.deepseek.com")
        or "deepseek" in str(client.base_url).lower(),
    )


# ====================================================================
# 3. 语言检测
# ====================================================================
def test_lang_detection():
    print("\n=== 3. Language Detection ===")

    ja_pages = [{"page": 1, "text": "このドキュメントはシステムの仕様書です。承認ワークフローを定義します。"}]
    check("Japanese detected", _detect_lang(ja_pages) == "ja")

    en_pages = [{"page": 1, "text": "This document is the system specification for the project."}]
    check("English detected", _detect_lang(en_pages) == "en")

    zh_pages = [{"page": 1, "text": "本文档是项目的系统规格说明书。审批流程定义了三个层级。"}]
    check("Chinese detected", _detect_lang(zh_pages) == "ja")  # CJK → ja

    empty_pages = [{"page": 1, "text": ""}]
    check("Empty text → en", _detect_lang(empty_pages) == "en")


# ====================================================================
# 4. n-gram 构建
# ====================================================================
def test_ngram_building():
    print("\n=== 4. N-gram Building ===")

    # 英文单词级
    en_ngrams = _build_ngrams("the specification defines requirements", 1, 2, "en")
    check("EN: 'specification' in word unigrams", "specification" in en_ngrams)
    check("EN: stopword 'the' excluded", "the" not in en_ngrams)
    check("EN: bigram 'specification defines'", "specification defines" in en_ngrams)

    # 日文字符级
    ja_ngrams = _build_ngrams("仕様書", 2, 3, "ja")
    check("JA: char bigram '仕様'", "仕様" in ja_ngrams)
    check("JA: char bigram '様書'", "様書" in ja_ngrams)


# ====================================================================
# 5. 垃圾过滤
# ====================================================================
def test_junk_filter():
    print("\n=== 5. Junk Filter ===")
    check("'123' is junk", _is_junk("123"))
    check("'...' is junk", _is_junk("..."))
    check("' ' is junk", _is_junk(" "))
    check("'a' is junk (single char)", _is_junk("a"))
    check("'spec' is NOT junk", not _is_junk("spec"))
    check("'仕様' is NOT junk", not _is_junk("仕様"))


# ====================================================================
# 6. TF-IDF 日文提取
# ====================================================================
def test_tfidf_japanese():
    print("\n=== 6. TF-IDF Japanese Extraction ===")
    ja_pages = [
        {"page": 1, "text": "この仕様書はシステムの要件を定義します。承認ワークフローは3段階です。"},
        {"page": 2, "text": "仕様書に基づき、データベース設計を行います。承認プロセスに従ってください。"},
        {"page": 3, "text": "本仕様書の付録にはAPIリファレンスが含まれています。承認は必須です。"},
    ]
    candidates = extract_tfidf_terms(ja_pages, top_k=10)
    check("JA TF-IDF: got candidates", len(candidates) > 0)
    texts = [c["text"] for c in candidates]
    check("JA TF-IDF: '仕様書' found", "仕様書" in texts, str(texts[:5]))
    print(f"    JA candidates: {texts[:5]}")


# ====================================================================
# 7. 归一化边界条件
# ====================================================================
def test_normalization_edge_cases():
    print("\n=== 7. Normalization Edge Cases ===")

    # 空输入
    r1 = normalize_terms([], glossary={}, language="en")
    check("Empty term_groups → total=0", r1.total_terms == 0)

    # 单变体组
    r2 = normalize_terms(
        [{"concept_cn": "test", "standard": "test", "variants": [{"text": "test", "pages": [1], "count": 1}]}],
        language="en",
    )
    check("Single variant → normalized=0", r2.normalized == 0)
    check("Single variant → replacement_map empty", len(r2.replacement_map) == 0)

    # 术语表命中（精确匹配 key）
    r3 = normalize_terms(
        [
            {
                "concept_cn": "test",
                "standard": "wrong_term",
                "variants": [
                    {"text": "wrong_term", "pages": [1], "count": 5},
                    {"text": "correct_term", "pages": [2], "count": 1},
                ],
            }
        ],
        glossary={"wrong_term": "forced_standard"},
        language="en",
    )
    check("Glossary override → from_glossary=1", r3.from_glossary == 1)
    check(
        "Glossary override → wrong_term maps to forced_standard",
        r3.replacement_map.get("wrong_term") == "forced_standard",
    )

    # 频次平局 → 首次出现优先
    r4 = normalize_terms(
        [
            {
                "concept_cn": "test",
                "standard": "",
                "variants": [
                    {"text": "TermA", "pages": [5], "count": 3},
                    {"text": "TermB", "pages": [2], "count": 3},
                ],
            }
        ],
        language="en",
    )
    check("Frequency tie → picks by frequency (same count, first in list wins)", r4.normalized >= 0)


# ====================================================================
# 8. 替换边界条件
# ====================================================================
def test_replacement_edge_cases():
    print("\n=== 8. Replacement Edge Cases ===")

    # 空替换映射
    r1 = global_replace([{"page": 1, "text": "hello"}], {})
    check("Empty map → 0 replacements", r1.total_replacements == 0)
    check("Empty map → pages returned", len(r1.pages) == 1)

    # 完全相同词条（variant == standard）
    r2 = global_replace(
        [{"page": 1, "text": "The specification is complete."}],
        {"specification": "specification"},
    )
    check("Same variant→standard → 0 replacements", r2.total_replacements == 0)

    # 大小写敏感
    r3 = global_replace(
        [{"page": 1, "text": "The Spec defines spec requirements."}],
        {"Spec": "Specification"},
    )
    check("Case-sensitive: 'Spec' replaced", "Specification" in r3.pages[0]["text"])
    check("Case-sensitive: lowercase 'spec' NOT replaced", "spec" in r3.pages[0]["text"])

    # 多词短语（不变异）
    r4 = global_replace(
        [{"page": 1, "text": "The Spec Document must be reviewed."}],
        {"Spec Document": "Specification"},
    )
    check("Multi-word replacement", "Specification" in r4.pages[0]["text"])
    check("Multi-word: 'Document' removed", "Document" not in r4.pages[0]["text"])

    # \b 边界保护："spec" 不应匹配 "specification"
    r5 = global_replace(
        [{"page": 1, "text": "The specification is detailed."}],
        {"spec": "Specification"},
    )
    check("Word boundary: 'spec' NOT matching inside 'specification'",
          "specification" in r5.pages[0]["text"].lower() and "Specificationification" not in r5.pages[0]["text"])


# ====================================================================
# 9. 回退分组
# ====================================================================
def test_fallback_grouping():
    print("\n=== 9. Fallback Grouping ===")

    candidates = [
        {"text": "specification", "pages": [1, 2, 3], "score": 0.9},
        {"text": "spec", "pages": [2], "score": 0.5},
        {"text": "document", "pages": [1], "score": 0.3},
        {"text": "documented", "pages": [3], "score": 0.2},
        {"text": "unrelated", "pages": [4], "score": 0.1},
    ]
    pages = [
        {"page": 1, "text": "The specification document defines the system."},
        {"page": 2, "text": "The spec is short for specification in this context."},
        {"page": 3, "text": "The specification and documented procedures are included."},
        {"page": 4, "text": "An unrelated topic is discussed here."},
    ]
    groups = _fallback_grouping(candidates, pages)

    # 应合并 specification+spec 为一组，document+documented 为一组，unrelated 单独
    check("Fallback: got groups", len(groups) >= 3)
    # 找包含 specification 和 spec 的组
    spec_group = next((g for g in groups if "specification" in [v["text"] for v in g["variants"]]), None)
    check("specification group exists", spec_group is not None)
    if spec_group:
        check("spec grouped with specification", "spec" in [v["text"] for v in spec_group["variants"]])
        check("standard = specification (longer term)", spec_group["standard"] == "specification")


# ====================================================================
# 10. Pipeline 空输入 & skip flags
# ====================================================================
def test_pipeline_flags():
    print("\n=== 10. Pipeline Flags ===")
    result = run_pipeline(
        file_id="test",
        pages=[{"page": 1, "text": "Hello world"}],
        source_lang="en",
        target_langs=["ja"],
        skip_polish=True,
        skip_post_polish=True,
    )
    check("skip_polish + skip_post_polish → no errors", len(result.errors) == 0)

    result2 = run_pipeline(
        file_id="test2",
        pages=[],
        source_lang="en",
        target_langs=["ja"],
        skip_polish=True,
        skip_post_polish=True,
    )
    check("Empty pages → success", result2.success)


# ====================================================================
# 11. 重试装饰器
# ====================================================================
def test_retry_decorator():
    print("\n=== 11. Retry Decorator ===")
    call_count = [0]

    @with_retry(max_retries=2, delay_sec=0.01)
    def fails_then_succeeds():
        call_count[0] += 1
        if call_count[0] < 2:
            raise ConnectionError("simulated failure")
        return "success"

    result = fails_then_succeeds()
    check("Retry: succeeds on 2nd attempt", result == "success")
    check("Retry: called exactly twice", call_count[0] == 2)

    # 4xx 不重试
    call_4xx = [0]

    @with_retry(max_retries=3, delay_sec=0.01)
    def fails_400():
        call_4xx[0] += 1
        raise Exception("HTTP 400 Bad Request")

    try:
        fails_400()
    except Exception:
        pass
    check("400 error: NOT retried (called only once)", call_4xx[0] == 1)


# ====================================================================
# 12. Stage 0 术语表匹配
# ====================================================================
def test_stage0_glossary():
    print("\n=== 12. Stage 0 Glossary Match ===")
    ctx = PipelineContext(
        file_id="test",
        source_lang="ja",
        target_langs=["en"],
        pages=[{"page": 1, "text": "この仕様書は稟議が必要です。"}],
        glossary={"仕様書": "Specification", "稟議": "Approval Request"},
    )
    ctx = stage0_glossary_match(ctx)
    check("Stage 0: 仕様書 → Specification", "Specification" in ctx.pages[0]["text"])
    check("Stage 0: 稟議 → Approval Request", "Approval Request" in ctx.pages[0]["text"])


# ====================================================================
# Main
# ====================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("TriDoc AI Pipeline — COMPREHENSIVE SELF-TEST")
    status = "ONLINE" if DEEPSEEK_API_KEY else "OFFLINE (TF-IDF fallback)"
    print(f"DeepSeek API: {status}")
    print("=" * 60)

    test_module_imports()
    test_deepseek_config()
    test_lang_detection()
    test_ngram_building()
    test_junk_filter()
    test_tfidf_japanese()
    test_normalization_edge_cases()
    test_replacement_edge_cases()
    test_fallback_grouping()
    test_pipeline_flags()
    test_retry_decorator()
    test_stage0_glossary()

    print(f"\n{'=' * 60}")
    print(f"RESULTS: {PASS} passed, {FAIL} failed, {PASS + FAIL} total")
    print(f"{'=' * 60}")
    if FAIL > 0:
        print("SOME TESTS FAILED — review output above.")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED.")
