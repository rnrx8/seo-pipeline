import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit, urlunsplit

import anthropic
from .db import get_artifact, get_optional_artifact, get_job, get_primary_sources, get_primary_sources_by_preset, upsert_artifact
from .ai import create_with_retry, get_step_config
from .source_freshness import SOURCE_FRESHNESS_POLICY, SOURCE_POLICY_VERSION, current_check_date, freshness_context

from .fresh_sources import FreshSources, load_settings, run_with_fetch

MODEL, MAX_TOKENS = get_step_config("fact_sheet")

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 15}


def restore_research_sources(fresh, pages):
    """Reserve up to 40 new pages per retry, with a 200-page lifetime cap."""
    for page in pages:
        fresh.pages[page['url']] = page
    fresh.max_urls = min(200, max(120, len(fresh.pages) + 40))
    fresh.browser_attempts = sum(bool(p.get('browser_attempted')) for p in pages)

def resumable_pages(artifact, fresh, checked_on):
    """Same job, same date, still-allowed sources only; never promote old material."""
    pages=[]
    for page in json.loads(artifact['content_text']) if artifact else []:
        try:
            date=datetime.fromisoformat(page['fetched_at']).astimezone(ZoneInfo('Asia/Tokyo')).date().isoformat()
            fresh.check_allowed(page['url'])
        except (KeyError,ValueError,TypeError):continue
        if date==checked_on:pages.append(page)
    return pages


def restore_resume_sources(fresh, artifact, checked_on):
    """Keep today's bodies and refresh older URLs before replacing the source set."""
    pages = json.loads(artifact['content_text']) if artifact else []
    restore_research_sources(fresh, resumable_pages(artifact, fresh, checked_on))
    fresh.max_urls = min(200, max(fresh.max_urls, len(pages) + 40))
    for page in pages:
        url = page.get('url', '')
        try:
            fresh.check_allowed(url)
        except (ValueError, TypeError):
            continue
        if url not in fresh.pages:
            # Never relabel yesterday's body as freshly verified. Failed refreshes
            # remain explicit source records instead of silently losing the URL.
            fresh.fetch(url)


SYSTEM_PROMPT = SOURCE_FRESHNESS_POLICY + "\n" + """\
あなたは記事の根拠を収集する調査担当です。
与えられた対象と質問に必要な資料だけを検索・直接取得する。無関係な求人・統計・トレンドを機械的に調べない。
検索回数を目標にしない。取得済み索引と公式ページのリンクを使い、各質問に対応する本文まで確認する。
検索枠と直接取得ツールは別。検索枠を使い切っても既知の関連URLはfetch_current_pageで取得できる。

【重要】出力する各事実・データには必ず以下のいずれかを付記してください：
- [confirmed] : 今回直接取得した本文で根拠を確認できた情報
- [hypothesis] : SERPや推測に基づく情報（web_searchで未確認）

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

Tier 2のソース1件のみで確認できた情報は原則 [hypothesis] とすること。ただし調査計画がexpert_allowedの一般的な専門解説は共通基準の例外を適用し、分野・氏名・資格・執筆/監修の関与を取得本文で確認した専門家解説1件でも、その説明範囲に限り [confirmed] としてよい。資格・関与の確認箇所も原文で残す。
同じ一次情報の転載・引用元が同じ記事は、何サイトあっても1ソースとして扱うこと。

【商業情報の代替根拠】
料金・無料範囲・機能・提供条件は共通基準に従い、公式探索後の独立した第三者本文2件以上でも確認可。
各根拠の対象・条件・適用時期の整合、独立性、公式の確認範囲を共通基準に従って記録する。
法律・制度・税・医療・金融・安全性の判断は公式・行政・原典・適切な専門資料を要求する。

【鮮度表示】
- 更新性の高い情報は確認日・出典を記録する。記事の注記は必要な比較表や章でまとめ、全記述への年月注記は義務にしない。
- 過去情報は根拠に明示された時期・条件に限定して採用可。通常の掲載情報に適用開始日を追加要求しない。明示された過去の条件を現在の条件へ読み替えない。
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
    fact_text: str, *, searched_urls: set[str], checked_on: str, fresh: FreshSources | None = None,
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
        date_label = r"(?:確認日|取得日)" if fresh is not None else "確認日"
        date_match = re.search(date_label + r"\s*[：:]\s*(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", block)
        actual_date = "-".join((date_match[1], date_match[2].zfill(2), date_match[3].zfill(2))) if date_match else None
        has_checked_at = actual_date == checked_on
        evidence = re.search(r"確認箇所\s*[：:]\s*([^\n｜|]+)", block)
        has_evidence = bool(evidence and evidence[1].strip() not in ("不明", "未確認", "なし", "[confirmed]"))
        if fresh is not None:
            has_url = has_evidence = fresh.evidence_matches(block)
        if has_url and has_checked_at and has_evidence:
            continue
        blocks[i] = re.sub(r"\[confirmed\]", "[hypothesis]", block, flags=re.IGNORECASE)
        reasons = []
        if not has_checked_at: reasons.append("今回の確認日が不足・不一致")
        if not has_url or not has_evidence: reasons.append("出典URLと連続した8〜240文字の引用が直接取得本文に一致しない（省略・言い換え・短すぎる引用を確認）")
        blocks[i] += "\n> 自動判定：" + "、".join(reasons) + "ため未確認扱い"
        downgraded += 1
    return "".join(blocks), downgraded


def run(job_id: str, keyword: str, api_key: str | None = None, research_gaps: str = '') -> dict:
    """Generate a fact sheet with real-time web search verification via Claude."""
    print("[fact_sheet] Generating fact sheet with web search...")

    # 一次情報を取得（preset_id優先、なければcategoryで照合）
    primary_sources_prompt = ""
    sources = []
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
    fresh = FreshSources(job, load_settings(job, sources), max_urls=120, render_dynamic=True, max_browser_attempts=20, retry_failed=bool(research_gaps))
    checked_on = current_check_date()
    previous=get_optional_artifact(job_id,'fresh_sources')
    if previous:
        upsert_artifact(job_id=job_id,step='research_sources_before_resume',
            content_type='application/json',content_text=previous['content_text'],meta=previous.get('meta',{}))
        restore_resume_sources(fresh,previous,checked_on)
    fresh.prefetch()
    fresh.save(job_id)

    client = anthropic.Anthropic(api_key=api_key)
    from .research_requirements import load_plan
    from .research_collection import collect
    # Collection owns the bounded question list and targeted retry, not a full-sheet rewrite.
    collection_prompt = freshness_context(checked_on) + 'キーワード: ' + keyword + primary_sources_prompt
    try:
        resp, fact_text, search_queries, observed = collect(job_id, client, plan=load_plan(job_id),fresh=fresh,
            system=SYSTEM_PROMPT,prompt=collection_prompt,model=MODEL,search_tool=WEB_SEARCH_TOOL,gaps=research_gaps)
    finally:
        fresh.save(job_id)
    return save_collected_notes(job_id, fresh, resp, fact_text, search_queries, observed, checked_on, sources)


def save_collected_notes(job_id, fresh, resp, fact_text, search_queries, observed, checked_on, sources):
    """Persist raw evidence notes; only research_completeness can accept answers."""
    searched_urls = _search_result_urls(observed)
    total_input, total_output = resp.usage.input_tokens, resp.usage.output_tokens
    fresh.fetch_confirmed_citations(fact_text)
    fact_text, downgraded_count = _downgrade_incomplete_confirmations(
        fact_text, searched_urls=searched_urls, checked_on=checked_on, fresh=fresh,
    )

    failures = [p for p in fresh.pages.values() if p['status'] != 'success']
    if failures:
        fact_text += "\n\n### URL再取得で確認できなかった資料\n" + "\n".join(
            f"- {p['url']}：{p['reason']}。登録値は確認済みとして使用しない。" for p in failures)

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
            "directly_fetched_urls": [p["url"] for p in fresh.pages.values() if p["status"] == "success"],
            "direct_fetch_failures": len(failures),
            "registered_source_count": sum(bool(s.get("content_text")) for s in sources),
        },
    )
    print(
        f"[fact_sheet] Done ({len(search_queries)} searches, "
        f"{downgraded_count} incomplete confirmations downgraded) → artifact id={artifact['id']}"
    )
    return artifact
