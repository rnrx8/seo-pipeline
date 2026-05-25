import re
import anthropic
from .ai import create_with_retry, get_step_config
from .db import get_artifact, upsert_artifact

MODEL, MAX_TOKENS = get_step_config("fact_review")

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 12}

SYSTEM_PROMPT = """\
あなたはSEO記事のファクトチェック専門編集者です。
提供された記事本文の数値・統計・固有名詞などの事実情報をweb_searchで再確認し、
確認できない情報を自動的に修正してください。

【ファクトチェック手順】
1. 記事中の数値・統計・具体的な企業名・件数・年収などをすべてリストアップする
2. 各情報をweb_searchで検索・再確認する（最低5回検索）
3. 確認結果に応じて以下のとおり対処する：
   ✅ Tier1ソース（公式サイト・政府機関・学術論文）1件で確認 → そのまま維持
   ✅ Tier2ソース（ニュースサイト・一般サイト）複数一致で確認 → そのまま維持
   ⚠️ 未確認（1ソースのみ、または確認できず） → 断言を避ける表現に変更
      例：「〜と言われています」「〜とも報告されています」「諸説あります」
   ❌ 別の数値・情報が正しいと判明 → 正しい情報に書き換える

【禁止事項】
- 確認できない情報を断言形式のまま残すこと
- 記事の構成・文体・見出しを不必要に変更すること
- [confirmed] / [hypothesis] タグを記事に出力すること

【出力フォーマット】（区切り文字を必ず使用すること）
===ARTICLE_START===
（修正済み記事本文をMarkdownで出力）
===ARTICLE_END===

===FACTCHECK_SUMMARY_START===
✅ 確認済み：〈情報〉（出典URL）
⚠️ 表現修正：「〈元の表現〉」→「〈修正後〉」（理由）
❌ 内容修正：〈内容〉（理由・正しい情報）
===FACTCHECK_SUMMARY_END===
"""

FACTCHECK_TEMPLATE = """\
以下の記事本文に含まれる事実情報をweb_searchで再確認し、確認できない情報を修正してください。

## 記事本文
{article_text}
"""


def _parse_response(text: str) -> tuple[str, str]:
    article_match = re.search(r"===ARTICLE_START===\n(.*?)\n===ARTICLE_END===", text, re.DOTALL)
    summary_match = re.search(r"===FACTCHECK_SUMMARY_START===\n(.*?)\n===FACTCHECK_SUMMARY_END===", text, re.DOTALL)
    article = article_match.group(1).strip() if article_match else text.strip()
    summary = summary_match.group(1).strip() if summary_match else "（サマリー取得失敗）"
    return article, summary


def run(job_id: str, keyword: str, api_key: str | None = None) -> dict:
    """Re-verify facts in the article via web_search and soften unverified claims."""
    print("[fact_review] Starting fact verification pass...")

    article_artifact = get_artifact(job_id, "article")
    article_text = article_artifact["content_text"]

    client = anthropic.Anthropic(api_key=api_key)
    messages = [{"role": "user", "content": FACTCHECK_TEMPLATE.format(article_text=article_text)}]

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
            if btype in ("tool_use", "server_tool_use") and getattr(block, "name", "") == "web_search":
                query = (block.input or {}).get("query", "")
                search_queries.append(query)
                print(f"[fact_review] Web search: {query!r}")

        if resp.stop_reason != "tool_use":
            break

        messages.append({"role": "assistant", "content": resp.content})

    text_parts = [b.text for b in resp.content if getattr(b, "type", None) == "text" and b.text]
    raw = "\n\n".join(text_parts)
    corrected_article, summary = _parse_response(raw)

    artifact = upsert_artifact(
        job_id=job_id,
        step="article",
        content_type="text/markdown",
        content_text=corrected_article,
        meta={
            "model": MODEL,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "fact_reviewed": True,
            "search_queries": search_queries,
        },
    )

    print(f"[fact_review] Done ({len(search_queries)} searches)")
    print()
    print("=== Fact Review Summary ===")
    print(summary)
    print("===========================")
    print(f"artifact id={artifact['id']}")

    return artifact
