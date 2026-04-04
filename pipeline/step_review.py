import re
import anthropic
from .ai import create_with_retry, get_step_config
from .db import get_artifact, upsert_artifact

MODEL, MAX_TOKENS = get_step_config("review")

SYSTEM_PROMPT = """\
あなたはSEOライティングの品質レビュー担当編集者です。
提供された記事を以下のチェックリストに従って検査・修正し、
修正済み記事とレビューサマリーを指定フォーマットで出力してください。

【チェック・修正項目】

### 【重複・冗長】
1. 「ですよね」多用の修正
   - 1記事中に3回以上出現する場合
   - 3回目以降を以下のいずれかに書き換える：
     「〜はずです」「〜ではないでしょうか」「〜が大切です」「〜をおすすめします」
   - 自然に締まる場合は共感表現を省略してもよい

### 【構造チェック】
2. H2直下の結論文チェック
   - 結論文なしでリストや表から始まっているH2を検出
   - 「〜です。」形式の1文結論を冒頭に追加する

3. H3直下の本文チェック
   - 本文なしでリストや表から始まっているH3を検出
   - 導入文を1文追加する

4. 表・リスト直前の導入文チェック
   - 表やリストの直前に導入文がない場合を検出
   - 「〜は以下の通りです。」「〜を整理します。」などを追加する

### 【文字数・文体】
5. プレーンテキスト連続300字超の検出・修正
   - H3内でプレーンテキストが300字を超えて連続している箇所を検出
   - 箇条書きリスト・表・H4見出しのいずれかで分割する

6. 長文チェック
   - 一文が80字を超えている場合に検出
   - 自然な区切りで2文に分割する

7. 文体チェック
   - 本文（見出し・表・リスト以外）でです・ます調でない箇所を検出
   - です・ます調に修正する

【出力フォーマット】
以下の区切り文字を使って2つのブロックを出力すること。

===ARTICLE_START===
（修正済み記事本文をMarkdownで出力）
===ARTICLE_END===

===SUMMARY_START===
（レビューサマリーを以下の形式で出力）
✅ チェック項目名：説明
✅ チェック項目名：説明
⚠️ チェック項目名：説明（修正済み）
（修正がなかった項目は「変更なし」と書く）
===SUMMARY_END===
"""

REVIEW_TEMPLATE = """\
以下の記事をレビューし、チェック・修正を行ってください。

## 記事本文
{article_text}
"""


def _parse_response(text: str) -> tuple[str, str]:
    """区切り文字でarticleとsummaryを分割して返す。"""
    article_match = re.search(
        r"===ARTICLE_START===\n(.*?)\n===ARTICLE_END===",
        text,
        re.DOTALL,
    )
    summary_match = re.search(
        r"===SUMMARY_START===\n(.*?)\n===SUMMARY_END===",
        text,
        re.DOTALL,
    )
    article = article_match.group(1).strip() if article_match else text.strip()
    summary = summary_match.group(1).strip() if summary_match else "（サマリー取得失敗）"
    return article, summary


def run(job_id: str, keyword: str) -> dict:
    """Review and auto-fix the generated article."""
    print("[review] Starting article review...")

    article_artifact = get_artifact(job_id, "article")
    article_text = article_artifact["content_text"]

    client = anthropic.Anthropic()
    msg = create_with_retry(
        client,
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": REVIEW_TEMPLATE.format(article_text=article_text),
            }
        ],
    )

    raw = msg.content[0].text
    corrected_article, summary = _parse_response(raw)

    # Supabaseに修正済み記事を上書き
    artifact = upsert_artifact(
        job_id=job_id,
        step="article",
        content_type="text/markdown",
        content_text=corrected_article,
        meta={
            "model": MODEL,
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
            "reviewed": True,
        },
    )

    print("[review] Done")
    print()
    print("=== Review Summary ===")
    print(summary)
    print("======================")
    print(f"artifact id={artifact['id']}")

    return artifact
