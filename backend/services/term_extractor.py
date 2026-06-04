"""
术语提取模块 — TF-IDF 提取高频 n-gram + GPT 识别术语变体。

输入：多页翻译结果 [{page, text}, ...]
输出：术语分组 {concept, variants: [{text, page, count}, ...]}
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from services.ai_service import chat_json_precise

logger = logging.getLogger("tri_doc.term_extractor")

# ---------------------------------------------------------------------------
# 语言检测 & n-gram 配置
# ---------------------------------------------------------------------------
_LANG_NGRAM = {
    "ja": (2, 4),  # 日文用字符级 2-4 gram
    "zh": (2, 4),  # 中文用字符级 2-4 gram
    "en": (1, 2),  # 英文用单词级 1-2 gram（word bigram）
}

# 英文停用词 — TF-IDF 时过滤
_EN_STOP_WORDS: set[str] = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "in", "on", "at", "to", "for", "of", "with", "by", "from",
    "and", "or", "not", "but", "if", "then", "else", "when",
    "this", "that", "these", "those", "it", "its", "he", "she",
    "they", "we", "you", "i", "all", "each", "every", "both",
    "has", "have", "had", "do", "does", "did", "will", "would",
    "can", "could", "may", "might", "shall", "should", "must",
}

# 需要过滤的纯标点/数字/含标点 n-gram
# Python re 不支持 \p{Unicode}，使用显式字符范围
_JUNK_RE = re.compile(
    r"^[\d\s"
    r" -/:-@[-`{-~"   # ASCII 标点
    r" -⁯"            # 通用标点
    r"　-〿"            # CJK 标点
    r"＀-￯"            # 全角标点
    r"]+$"
)

# 包含 CJK 标点的 n-gram（如 "す。"）也需要过滤
_CJK_PUNCT_RE = re.compile(
    r"["
    r"。、，．：；！？）］｝」』】〟"  # 日文标点
    r"…‥―‐⁓〜〰"
    r"]"
)


def _is_junk(term: str) -> bool:
    """过滤纯标点、纯数字、纯空白、含 CJK 标点的 n-gram。"""
    if len(term.strip()) <= 1:
        return True
    if _JUNK_RE.match(term):
        return True
    if _CJK_PUNCT_RE.search(term):
        return True
    return False


# ---------------------------------------------------------------------------
# TF-IDF 术语候选提取
# ---------------------------------------------------------------------------
def _detect_lang(pages: list[dict]) -> str:
    """简单启发式：看文本中 CJK 字符比例。"""
    all_text = " ".join(p["text"] for p in pages)
    cjk = sum(1 for ch in all_text if "一" <= ch <= "鿿" or "぀" <= ch <= "ヿ")
    if cjk / max(len(all_text), 1) > 0.15:
        return "ja"  # CJK 统一视为 ja/zh（TF-IDF 行为相似）
    return "en"


def _build_ngrams(text: str, n_min: int, n_max: int, lang: str) -> list[str]:
    """构建 n-gram。英文用单词级，日/中用字符级。"""
    if lang == "en":
        # 英文：单词级 n-gram
        words = [w.strip(".,;:()[]{}'\"?!-") for w in text.lower().split()]
        words = [w for w in words if w and w not in _EN_STOP_WORDS and len(w) > 1]
        ngrams: list[str] = []
        for n in range(n_min, n_max + 1):
            for i in range(len(words) - n + 1):
                gram = " ".join(words[i : i + n])
                if not _is_junk(gram):
                    ngrams.append(gram)
        return ngrams
    else:
        # 日/中：字符级 n-gram
        chars = list(text)
        ngrams = []
        for n in range(n_min, n_max + 1):
            for i in range(len(chars) - n + 1):
                gram = "".join(chars[i : i + n])
                if not _is_junk(gram):
                    ngrams.append(gram)
        return ngrams


def extract_tfidf_terms(
    pages: list[dict],
    top_k: int = 50,
) -> list[dict]:
    """
    TF-IDF 提取候选术语。

    返回: [{"text": str, "score": float, "pages": [page_num, ...]}]
    """
    if not pages:
        return []

    lang = _detect_lang(pages)
    n_min, n_max = _LANG_NGRAM.get(lang, (2, 3))

    # 为每页构建 n-gram 文档
    docs = [" ".join(_build_ngrams(p["text"], n_min, n_max, lang)) for p in pages]

    vectorizer = TfidfVectorizer(
        max_features=500,
        ngram_range=(1, 1),  # 已经手动 n-gram，这里不需要再拆
        token_pattern=r"\S+",
    )
    tfidf_matrix = vectorizer.fit_transform(docs)
    feature_names = vectorizer.get_feature_names_out()

    # 按平均 TF-IDF 排序
    avg_scores = np.array(tfidf_matrix.mean(axis=0)).flatten()
    top_indices = avg_scores.argsort()[-top_k:][::-1]

    candidates: list[dict] = []
    for idx in top_indices:
        term = feature_names[idx]
        score = float(avg_scores[idx])
        # 找出该 term 出现的页面
        col = tfidf_matrix[:, idx].toarray().flatten()
        present_pages = [pages[i]["page"] for i, v in enumerate(col) if v > 0]
        candidates.append({"text": term, "score": score, "pages": present_pages})

    logger.info("TF-IDF 提取 %d 个候选术语 (lang=%s)", len(candidates), lang)
    return candidates


# ---------------------------------------------------------------------------
# GPT 术语变体检测 & 分组
# ---------------------------------------------------------------------------
def _count_term_in_pages(term: str, pages: list[dict]) -> int:
    """统计 term 在所有页面中作为独立词出现的次数（\b 边界）。"""
    total = 0
    if " " in term:
        # 多词短语：直接计数
        for p in pages:
            total += p["text"].count(term)
    else:
        # 单词：用 \b 边界防止 substring 误匹配
        import re
        pat = re.compile(r"\b" + re.escape(term) + r"\b")
        for p in pages:
            total += len(pat.findall(p["text"]))
    return total


def extract_term_variants_gpt(
    pages: list[dict],
    page_texts: list[str],
    language: str = "ja",
    top_k: int = 30,
) -> list[dict]:
    """
    用 GPT 从翻译结果的全文上下文中识别术语变体。

    流程：
    1. TF-IDF 提取高频候选词
    2. 拼接所有页面文本摘要 → GPT 判断哪些术语存在变体
    3. GPT 按语义将变体分组

    返回:
    [
        {
            "concept": "Specification",
            "variants": [
                {"text": "Specification", "pages": [3], "count": 5},
                {"text": "Spec Document", "pages": [5, 8], "count": 3},
            ]
        },
        ...
    ]
    """
    # Step 1: TF-IDF 候选
    candidates = extract_tfidf_terms(page_texts, top_k=top_k)
    if not candidates:
        logger.info("无 TF-IDF 候选术语，跳过 GPT 检测")
        return []

    # Step 2: 构建 GPT prompt（摘录包含候选词的上下文句子）
    candidate_summary: list[str] = []
    for c in candidates[:top_k]:
        examples: list[str] = []
        for p in page_texts:
            text = p["text"]
            if c["text"] in text:
                # 取包含该词的句子
                idx = text.index(c["text"])
                start = max(0, idx - 30)
                end = min(len(text), idx + len(c["text"]) + 30)
                snippet = text[start:end].replace("\n", " ")
                examples.append(f"[页{p['page']}] ...{snippet}...")
        if examples:
            candidate_summary.append(
                f"候选词「{c['text']}」出现于: " + "; ".join(examples[:3])
            )

    # Step 3: DeepSeek 语义分组
    system_prompt = f"""你是一位多语言术语管理专家，当前分析的文档语言为 {language}。

【任务说明】
分析候选术语列表，完成以下三项工作：
1. 判定：哪些候选词是真正的"术语"（专有名词、技术词汇、业务概念）？排除泛用动词/形容词/助词
2. 分组：将同一概念的不同表达（变体）归入一组。判断依据：语义相同、指代同一事物
3. 定标：为每组选一个标准译法（优先级：最正式 > 出现最多 > 字符最短）

【输出格式】严格按以下 JSON 结构返回，不要用 markdown 代码块包裹：
{{
  "term_groups": [
    {{
      "concept_cn": "用中文简述该概念（便于人工审核）",
      "standard": "该组标准译法",
      "variants": [
        {{"text": "变体A文本", "pages": [页码列表], "estimated_count": 出现次数}},
        {{"text": "变体B文本", "pages": [页码列表], "estimated_count": 出现次数}}
      ]
    }}
  ],
  "filtered_out": ["泛用词1", "泛用词2"]
}}

【重要规则】
- variant 的 text 必须来自候选词列表或页面原文，不得编造
- 如果候选词是术语但无变体，也归入 term_groups（variants 只有一项）
- 泛用词示例：日语助词「する/ある/なる」、英语「the/a/is」、中文「的/了/是」
- 只输出 JSON object，不要任何额外说明"""

    user_message = "以下是候选术语及在文档中的出现位置：\n\n" + "\n".join(candidate_summary)

    try:
        raw = chat_json_precise(system_prompt, user_message)
        result = json.loads(raw)
    except Exception as exc:
        logger.warning("GPT 术语检测失败: %s，回退到纯 TF-IDF", exc)
        return _fallback_grouping(candidates, page_texts)

    term_groups: list[dict] = result.get("term_groups", [])
    # 补充真实 count（GPT 给的 estimated_count 不准）
    for group in term_groups:
        for v in group.get("variants", []):
            v["count"] = _count_term_in_pages(v["text"], page_texts)

    filtered = result.get("filtered_out", [])
    logger.info(
        "术语提取完成: %d 组术语, %d 被过滤",
        len(term_groups),
        len(filtered),
    )
    return term_groups


# ---------------------------------------------------------------------------
# 回退方案：纯 TF-IDF 分组（无 GPT）
# ---------------------------------------------------------------------------
def _fallback_grouping(
    candidates: list[dict],
    page_texts: list[dict],
) -> list[dict]:
    """
    GPT 不可用时：基于启发式规则分组。

    规则：
    1. 如果 term A 是 term B 的子串（如 "spec" ⊆ "specification"），归入一组
    2. 如果 term A 和 term B 共享前缀（前4字符相同），归入一组
    3. 其余各成一组
    """
    used: set[int] = set()
    groups: list[dict] = []

    for i, c in enumerate(candidates):
        if i in used:
            continue
        variants: list[dict] = [
            {
                "text": c["text"],
                "pages": c["pages"],
                "count": _count_term_in_pages(c["text"], page_texts),
            }
        ]
        used.add(i)

        # 找相关变体（子串关系或共享前缀）
        for j, other in enumerate(candidates):
            if j in used:
                continue
            a, b = c["text"].lower(), other["text"].lower()
            is_substring = a in b or b in a
            shares_prefix = a[:4] == b[:4] and len(a) >= 3 and len(b) >= 3
            if is_substring or shares_prefix:
                variants.append(
                    {
                        "text": other["text"],
                        "pages": other["pages"],
                        "count": _count_term_in_pages(other["text"], page_texts),
                    }
                )
                used.add(j)

        # 选标准译法：频次优先，但倾向更长的正式表达（长词权重更高）
        best = max(variants, key=lambda v: (v["count"], len(v["text"]))) if len(variants) == 1 else max(
            variants,
            key=lambda v: (len(v["text"]) * 0.6 + v["count"] * 0.4),
        )
        groups.append(
            {
                "concept_cn": best["text"],
                "standard": best["text"],
                "variants": variants,
            }
        )

    return groups
