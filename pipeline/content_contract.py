"""Build and validate a job-specific structural contract for generated content.

The contract converts search-intent analysis into machine-checkable requirements.
Only sections supported by the detected intent and job goal become required; there
is intentionally no universal list of mandatory H2s.
"""
from __future__ import annotations

import json
import re
from typing import Any


CONTRACT_VERSION = "1.0"

_COMPARISON_TERMS = ("比較検討", "比較・検討", "比較して", "比較したい", "おすすめ", "ランキング", "選びたい")
_CV_TERMS = ("cv", "コンバージョン", "自社商品", "自社サービス")
_PURCHASE_STAGES = ("比較検討", "購入・申込直前")
_SAFETY_TERMS = ("安全", "注意", "リスク", "失敗", "後悔", "トラブル", "身バレ", "危険")
_FIT_TERMS = ("向いて", "向き不向き", "適性", "自分に合う")
_GENERIC_HEADINGS = {
    "定義・基本情報", "重要な事実・データ", "主要な企業・サービス情報", "主要な企業・サービス・求人情報",
    "よくある誤解・注意点", "専門用語・キーワード", "確認済み情報源", "編集者への追記提案",
}


def _load_json(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _primary_line(intent_text: str) -> str:
    for line in intent_text.splitlines():
        if "Primary" in line or "メイン意図" in line:
            return line.strip()
    return ""


def _clean_heading_name(value: str) -> str:
    value = re.sub(r"^[#\s]+", "", value)
    value = re.sub(r"^[①-⑳❶-❿\d]+[.．、:)）:\s-]*", "", value)
    value = re.split(r"[｜|]", value, maxsplit=1)[0]
    value = re.sub(r"（[^）]{0,40}）|\([^)]{0,40}\)", "", value)
    return value.strip(" ：:【】[]-—")


def extract_service_candidates(fact_text: str, featured_service: str = "") -> list[str]:
    """Extract plausible service names from fact-sheet headings."""
    candidates: list[str] = []
    for line in fact_text.splitlines():
        if not re.match(r"^#{3,5}\s+", line):
            continue
        raw_name = re.sub(r"^#{3,5}\s+", "", line).strip()
        raw_name = re.sub(r"^[①-⑳❶-❿\d]+[.．、:)）：:\s-]*", "", raw_name)
        # Fact sheets usually use "カドル（Cuddle）の会員数" or
        # "既婚者クラブの料金". Keep only the product-name portion.
        product_match = re.match(
            r"^(.{2,30}?)(?:（[A-Za-z][^）]*）|\([A-Za-z][^)]*\))?"
            r"の(?:会員数|料金|特徴|機能|評判|安全性|マッチング数|利用者)",
            raw_name,
        )
        name = _clean_heading_name(product_match.group(1) if product_match else raw_name)
        if not name or name in _GENERIC_HEADINGS or len(name) > 45:
            continue
        if any(term in name for term in (
            "比較", "一覧", "料金の男女差", "市場", "人口", "法的", "誤解", "県内", "地方", "主要3", "一般アプリ"
        )):
            continue
        has_romanized_alias = bool(re.search(r"[（(][A-Za-z]", raw_name))
        has_product_descriptor = bool(product_match) and not any(term in name for term in ("新潟", "北海道", "広島", "沖縄"))
        if (
            has_romanized_alias
            or has_product_descriptor
            or bool(re.search(r"[A-Za-z]", name))
            or any(term in name for term in ("アプリ", "クラブ", "メール", "サービス"))
        ):
            candidates.append(name)
    if featured_service:
        candidates.insert(0, featured_service)
    result: list[str] = []
    for name in candidates:
        if name and not any(name == existing for existing in result):
            result.append(name)
    return result[:8]


def _section(key: str, *, reason: str, protected: bool = False, **kwargs: Any) -> dict[str, Any]:
    return {"key": key, "required": True, "protected": protected, "reason": reason, **kwargs}


def build_content_contract(
    *,
    keyword: str,
    intent_text: str,
    query_attrs_text: str | None,
    fact_text: str,
    job: dict[str, Any],
    service: dict[str, Any] | None = None,
    reference_structure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attrs = _load_json(query_attrs_text)
    primary = _primary_line(intent_text)
    stage = str(attrs.get("searcher_stage") or "不明")
    concerns = [str(v) for v in (attrs.get("key_concerns") or [])]
    purpose = str(job.get("article_purpose") or "")
    service_name = str((service or {}).get("name") or "")

    primary_comparison = any(term in primary for term in _COMPARISON_TERMS)
    keyword_comparison = any(term in keyword for term in ("比較", "おすすめ", "ランキング"))
    stage_comparison = stage in _PURCHASE_STAGES
    comparison_required = primary_comparison or keyword_comparison or stage_comparison

    is_cv = any(term in purpose.lower() for term in _CV_TERMS)
    is_commercial = "Commercial" in primary or comparison_required
    service_treatment = "none"
    if service_name and is_cv:
        service_treatment = "dedicated" if is_commercial or stage in _PURCHASE_STAGES else "integrated"

    candidates = extract_service_candidates(fact_text, service_name)
    required: list[dict[str, Any]] = []
    optional: list[dict[str, Any]] = []

    if comparison_required:
        minimum = min(3, len(candidates)) if candidates else 3
        required.append(_section(
            "named_service_comparison",
            reason=f"Primary意図または検索段階が比較検討を示す（Primary={primary or '不明'} / stage={stage}）",
            protected=True,
            minimum_named_items=max(2, minimum),
            candidate_services=candidates,
            requirements=["具体的なサービス名を並べる", "同じ比較軸の表を置く", "読者が選べる結論を示す"],
        ))

    if service_treatment == "dedicated":
        required.append(_section(
            "featured_service_dedicated",
            reason="CV目的かつ比較・申込に近い検索意図のため、選定理由を独立して説明する",
            protected=True,
            service_name=service_name,
            requirements=["サービス名をH2に含める", "特徴・向く人・料金または始め方を扱う"],
        ))
    elif service_treatment == "integrated":
        required.append(_section(
            "featured_service_integrated",
            reason="CV目的だが情報収集型のため、検索意図に合う既存章へ自然に統合する",
            protected=True,
            service_name=service_name,
            requirements=["関連するH2配下でサービスの具体的価値を説明する"],
        ))

    intent_and_concerns = intent_text + "\n" + "\n".join(concerns)
    if any(term in intent_and_concerns for term in _SAFETY_TERMS):
        optional.append({
            "key": "risk_or_caution",
            "required": False,
            "reason": "検索意図に安全・リスク系の関心がある場合の候補",
        })
    if any(term in intent_and_concerns for term in _FIT_TERMS):
        optional.append({
            "key": "reader_fit",
            "required": False,
            "reason": "検索意図に適性判断がある場合の候補",
        })
    if "差別化" in intent_text:
        optional.append({
            "key": "differentiation",
            "required": False,
            "reason": "競合分析の示唆がPrimary検索意図にも寄与する場合のみ採用する候補",
        })

    reference = reference_structure or {}
    return {
        "version": CONTRACT_VERSION,
        "keyword": keyword,
        "primary_intent": primary or "不明",
        "searcher_stage": stage,
        "key_concerns": concerns,
        "article_purpose": purpose or None,
        "featured_service": service_name or None,
        "service_treatment": service_treatment,
        "required_sections": required,
        "optional_sections": optional,
        "instruction_priority": [
            "事実・安全性", "Primary検索意図", "記事目的・CV対象", "追加指示", "参考構成", "文体・装飾"
        ],
        "reference": {
            "urls": reference.get("urls", []),
            "fetched_count": reference.get("fetched_count", 0),
            "usage": "構成の型として参照するが、Primary検索意図とrequired_sectionsを上書きしない",
        },
    }


def contract_prompt(contract: dict[str, Any]) -> str:
    return (
        "\n【コンテンツ構造契約（機械検証対象）】\n"
        "これはジョブ固有の検索意図から生成した要件です。required_sectionsだけを必須とし、"
        "optional_sectionsは記事に必要な場合のみ採用してください。固定テンプレとして全項目を追加しないでください。\n"
        "protected=trueの章は、文字数調整やレビューでも削除・H3への降格を禁止します。\n"
        f"```json\n{json.dumps(contract, ensure_ascii=False, indent=2)}\n```\n"
    )


def reference_prompt(reference: dict[str, Any]) -> str:
    """Render fetched reference pages as a bounded, subordinate structure hint."""
    pages = reference.get("pages") or []
    if not pages:
        return ""
    lines = [
        "\n【取得済み参考ページの構造】",
        "以下は構成の型・論点漏れを確認するための参考です。本文の事実根拠にはせず、",
        "Primary検索意図とコンテンツ構造契約に反する見出しは採用しないでください。",
    ]
    for page in pages[:3]:
        lines.append(f"\n- URL: {page.get('final_url') or page.get('url', '')}")
        if page.get("title"):
            lines.append(f"  タイトル: {page['title']}")
        headings = page.get("headings") or []
        for heading in headings[:40]:
            level = heading.get("level", 2)
            lines.append(f"  H{level}: {heading.get('text', '')}")
    return "\n".join(lines) + "\n"
