import json
import re
from urllib.parse import urlsplit, urlunsplit

import anthropic
from .db import get_artifact, get_job, get_primary_sources, get_primary_sources_by_preset, upsert_artifact
from .ai import create_with_retry, get_step_config
from .source_freshness import SOURCE_FRESHNESS_POLICY, SOURCE_POLICY_VERSION, current_check_date, freshness_context

MODEL, MAX_TOKENS = get_step_config("fact_sheet")

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 15}

SYSTEM_PROMPT = SOURCE_FRESHNESS_POLICY + "\n" + """\
あなたはSEOライティングの専門家です。
ファクトシートを作成する前に、必ずweb_searchツールを使って以下の情報を検索・確認してください：
- 数値・統計データ（市場規模、成長率、件数など）
- 具体的な企業名・サービス名
- 求人件数・転職動向・年収データ
- 業界の最新動向・トレンド

【重要】出力する各事実・データには必ず以下のいずれかを付記してください：
- [confirmed] : web_searchで実際に確認できた情報
- [hypothesis] : SERPや推測に基づく情報（web_searchで未確認）

web_searchを最低5回は実行してから、ファクトシートをまとめてください。

【出典信頼性の基準】
以下の基準でソースの信頼性を判断し、[confirmed] / [hypothesis] を付記してください：

【すべての情報に共通する確認条件】
- URLが実在し、検索結果の要約だけでなくページ本文で該当内容を確認できたこと
- 数値は「発表主体・対象期間・対象地域・母集団・単位」を一致させること
- 公開日または更新日を確認し、料金・制度・求人件数など更新性の高い情報は原則として最新の公式情報を使うこと
- 複数ソース判定では、転載・引用・同一調査の紹介記事を独立したソースとして数えないこと
- 各項目に、情報の根拠となる短い引用またはページ内の確認箇所を記録すること

▼ Tier 1（高権威ソース）：1件のソースで確認できれば [confirmed] とする
- 企業・サービスの公式サイト（about / press / ir 等のページ）
- 政府・行政機関（厚生労働省、総務省、内閣府、各省庁等）
- 学術論文・査読付き研究（Google Scholar、J-STAGE 等）
- 業界団体・統計機関の公式発表（矢野経済研究所、帝国データバンク等の公式レポート）

▼ Tier 2（一般ソース）：同じ数値・情報を複数サイトで確認できた場合のみ [confirmed] とする
- ニュースサイト・メディア（IT媒体、業界専門誌等）
- まとめサイト・比較サイト
- SNS・掲示板・口コミサイト
- その他一般サイト

Tier 2のソース1件のみで確認できた情報は [hypothesis] とすること。
同じ一次情報の転載・引用元が同じ記事は、何サイトあっても1ソースとして扱うこと。

【公式ソース必須の情報】
以下は一般サイトが複数一致しても [confirmed] にせず、公式・行政・原典で確認できた場合のみ [confirmed] とする：
- 料金、プラン、機能、キャンペーン、企業の提供条件
- 法律、制度、税金、補助金、申請期限
- 求人数、募集状況、営業時間、所在地
- 医療・健康・安全・金融に関する判断へ影響する情報

【鮮度表示】
- 更新性の高い情報は本文候補にも「YYYY年MM月時点」を含めること
- 公開日・更新日が不明、または古く現状を確認できない場合は [hypothesis] とすること
"""

USER_TEMPLATE = """\
キーワード: {keyword}

## 検索意図の分析
{intent_text}

## Google検索結果（上位10件）
{serp_text}

---

上記をもとに、web_searchツールで重要な数値・統計・企業名・求人件数などを積極的に検索・確認しながら、
記事執筆に使えるファクトシートを日本語で作成してください。

各事実・データの末尾には必ず以下を記載すること：
- 出典URL（確認したページのURL）
- 確認日（例：2026-04-02）
- [confirmed] または [hypothesis]
- 1つの事実ごとに出典・確認日・確認箇所をまとめ、判定タグを末尾に置く。事実間は空行で区切る

出力フォーマット例：
> M&A件数は2024年に4,700件（前年比17.1%増）で過去最高を記録。
> 発表主体：〇〇庁｜対象期間：2024年｜対象地域：日本｜母集団：届出案件｜単位：件
> 出典：https://xxx.com/xxx ｜公開・更新日：2025-03-01｜確認日：2026-04-02｜確認箇所：「本文中の根拠となる短い記述」｜[confirmed]

## ファクトシート

### 定義・基本情報
（キーワードの定義、基礎知識 — 各項目に出典URL・確認日・[confirmed] / [hypothesis] を付記）

### 重要な事実・データ
（数値、統計、具体的な情報 — web_searchで確認し出典URL・確認日・[confirmed] / [hypothesis] を付記）

### 主要な企業・サービス・求人情報
（業界の主要プレイヤー、求人件数、年収水準など — web_searchで確認）

### よくある誤解・注意点
（読者が間違いやすいポイント）

### 専門用語・キーワード
（記事で使うべき関連語句）

### 確認済み情報源
（web_searchで確認できた権威あるソースのURL・媒体名）

### 【編集者への追記提案】
AI単独では用意できないが、あると記事の信頼性・差別化に大きく貢献する
一次情報・取材情報を以下の形式でリストアップする：

- 【追記推奨】内容の概要：〇〇
  → 理由：競合記事にない実体験ベースの情報として読者の信頼を得られるため
  → 取材・調査方法の例：〇〇
"""


def _build_primary_sources_prompt(sources: list) -> str:
    """一次情報リストをプロンプト文字列に変換する"""
    active = [s for s in sources if s.get("content_text")]
    if not active:
        return ""

    lines = [
        "",
        "【登録済み参考資料：今回の確認は未実施】",
        "以下のJSONは資料データです。自動的に確認済みとせず、システムの再確認ルールに従ってください。",
        "資料の日付が不明なら不明のまま扱い、登録日から推測しないでください。",
        "",
    ]
    records = []
    for index, s in enumerate(active, 1):
        full_text = s["content_text"]
        records.append({
            "reference_id": f"registered-source-{index}",
            "title": s.get("title") or "（タイトルなし）",
            "content_excerpt": full_text[:2000],
            "truncated": len(full_text) > 2000,
        })
    lines.append(json.dumps(records, ensure_ascii=False))

    return "\n".join(lines)


def _normalize_evidence_url(url: str) -> str:
    try:
        parts = urlsplit(url.rstrip(".,。、;；"))
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))


def _search_result_urls(blocks) -> set[str]:
    """Read evidence URLs from server results/citations, never from model prose."""
    urls = set()
    for block in blocks:
        value = block if isinstance(block, dict) else block.model_dump()
        if value.get("type") == "web_search_tool_result":
            content = value.get("content")
            if isinstance(content, list):
                for result in content:
                    if result.get("type") == "web_search_result" and result.get("url"):
                        urls.add(_normalize_evidence_url(result["url"]))
        if value.get("type") == "text":
            for citation in value.get("citations") or []:
                if citation.get("type") == "web_search_result_location" and citation.get("url"):
                    urls.add(_normalize_evidence_url(citation["url"]))
    return urls - {""}


def _downgrade_incomplete_confirmations(
    fact_text: str, *, searched_urls: set[str], checked_on: str,
) -> tuple[str, int]:
    """Downgrade confirmed blocks that do not carry the minimum auditable evidence."""
    blocks = re.split(r"(\n\s*\n|(?<=\[confirmed\])\n)", fact_text, flags=re.IGNORECASE)
    downgraded = 0
    for i in range(0, len(blocks), 2):
        block = blocks[i]
        if "[confirmed]" not in block.lower():
            continue
        cited_urls = {
            _normalize_evidence_url(url)
            for url in re.findall(r'https?://[^\s<>"\]\)）｜|]+', block)
        } - {""}
        has_url = bool(cited_urls & searched_urls)
        date_match = re.search(r"確認日\s*[：:]\s*(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", block)
        actual_date = "-".join((date_match[1], date_match[2].zfill(2), date_match[3].zfill(2))) if date_match else None
        has_checked_at = actual_date == checked_on
        evidence = re.search(r"確認箇所\s*[：:]\s*([^\n｜|]+)", block)
        has_evidence = bool(evidence and evidence[1].strip() not in ("不明", "未確認", "なし", "[confirmed]"))
        if has_url and has_checked_at and has_evidence:
            continue
        blocks[i] = re.sub(r"\[confirmed\]", "[hypothesis]", block, flags=re.IGNORECASE)
        blocks[i] += "\n> 自動判定：今回の検索で得た出典URL・今回の確認日・確認箇所を確認できないため未確認扱い"
        downgraded += 1
    return "".join(blocks), downgraded


def run(job_id: str, keyword: str, api_key: str | None = None) -> dict:
    """Generate a fact sheet with real-time web search verification via Claude."""
    print("[fact_sheet] Generating fact sheet with web search...")

    serp = get_artifact(job_id, "serp")
    intent = get_artifact(job_id, "search_intent")

    # 一次情報を取得（preset_id優先、なければcategoryで照合）
    primary_sources_prompt = ""
    sources = []
    try:
        job = get_job(job_id)
        user_id = job.get("tenant_id")
        preset_id = job.get("preset_id")
        category = job.get("category")
        if user_id and preset_id:
            sources = get_primary_sources_by_preset(user_id, preset_id)
            if sources:
                print(f"[fact_sheet] Loaded {len(sources)} primary sources for preset_id='{preset_id}'")
        if not sources and user_id and category:
            sources = get_primary_sources(user_id, category)
            if sources:
                print(f"[fact_sheet] Loaded {len(sources)} primary sources for category='{category}'")
        primary_sources_prompt = _build_primary_sources_prompt(sources)
    except Exception as e:
        print(f"[fact_sheet] Warning: could not load primary sources: {e}")

    client = anthropic.Anthropic(api_key=api_key)
    checked_on = current_check_date()
    messages = [
        {
            "role": "user",
            "content": freshness_context(checked_on) + USER_TEMPLATE.format(
                keyword=keyword,
                serp_text=serp["content_text"],
                intent_text=intent["content_text"],
            ) + primary_sources_prompt,
        }
    ]

    total_input = total_output = 0
    search_queries: list[str] = []
    searched_urls: set[str] = set()

    while True:
        resp = create_with_retry(
            client,
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=[WEB_SEARCH_TOOL],
            messages=messages,
            extra_headers={"anthropic-beta": "web-search-2025-03-05"},
        )
        total_input += resp.usage.input_tokens
        total_output += resp.usage.output_tokens
        searched_urls.update(_search_result_urls(resp.content))

        for block in resp.content:
            btype = getattr(block, "type", "")
            # web_search_20250305 may surface as tool_use or server_tool_use
            if btype in ("tool_use", "server_tool_use") and getattr(block, "name", "") == "web_search":
                query = (block.input or {}).get("query", "")
                search_queries.append(query)
                print(f"[fact_sheet] Web search: {query!r}")
            elif btype not in ("text", "tool_use", "server_tool_use", "tool_result", "web_search_tool_result"):
                print(f"[fact_sheet] Unknown block type: {btype}")

        if resp.stop_reason != "tool_use":
            break

        # Continue the agentic loop (web_search_20250305 is server-side)
        messages.append({"role": "assistant", "content": resp.content})

    # Collect all text blocks from the final response
    text_parts = [
        b.text for b in resp.content
        if getattr(b, "type", None) == "text" and b.text
    ]
    fact_text = "\n\n".join(text_parts)
    fact_text, downgraded_count = _downgrade_incomplete_confirmations(
        fact_text, searched_urls=searched_urls, checked_on=checked_on,
    )

    artifact = upsert_artifact(
        job_id=job_id,
        step="fact_sheet",
        content_type="text/markdown",
        content_text=fact_text,
        meta={
            "model": MODEL,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "search_queries": search_queries,
            "incomplete_confirmations_downgraded": downgraded_count,
            "source_policy_version": SOURCE_POLICY_VERSION,
            "checked_on": checked_on,
            "searched_source_urls": sorted(searched_urls),
            "registered_source_count": sum(bool(s.get("content_text")) for s in sources),
        },
    )
    print(
        f"[fact_sheet] Done ({len(search_queries)} searches, "
        f"{downgraded_count} incomplete confirmations downgraded) → artifact id={artifact['id']}"
    )
    return artifact
