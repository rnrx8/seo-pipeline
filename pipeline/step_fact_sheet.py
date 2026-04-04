import anthropic
from .db import get_artifact, upsert_artifact
from .ai import create_with_retry, get_step_config

MODEL, MAX_TOKENS = get_step_config("fact_sheet")

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 8}

SYSTEM_PROMPT = """\
あなたはSEOライティングの専門家です。
ファクトシートを作成する前に、必ずweb_searchツールを使って以下の情報を検索・確認してください：
- 数値・統計データ（市場規模、成長率、件数など）
- 具体的な企業名・サービス名
- 求人件数・転職動向・年収データ
- 業界の最新動向・トレンド

【重要】出力する各事実・データには必ず以下のいずれかを付記してください：
- [confirmed] : web_searchで実際に確認できた情報
- [hypothesis] : SERPや推測に基づく情報（web_searchで未確認）

web_searchを最低3回は実行してから、ファクトシートをまとめてください。
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

出力フォーマット例：
> M&A件数は2024年に4,700件（前年比17.1%増）で過去最高を記録。
> 出典：https://xxx.com/xxx ｜確認日：2026-04-02 ｜[confirmed]

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


def run(job_id: str, keyword: str) -> dict:
    """Generate a fact sheet with real-time web search verification via Claude."""
    print("[fact_sheet] Generating fact sheet with web search...")

    serp = get_artifact(job_id, "serp")
    intent = get_artifact(job_id, "search_intent")

    client = anthropic.Anthropic()
    messages = [
        {
            "role": "user",
            "content": USER_TEMPLATE.format(
                keyword=keyword,
                serp_text=serp["content_text"],
                intent_text=intent["content_text"],
            ),
        }
    ]

    total_input = total_output = 0
    search_queries: list[str] = []

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
        },
    )
    print(f"[fact_sheet] Done ({len(search_queries)} searches) → artifact id={artifact['id']}")
    return artifact
