import re
import anthropic
from .ai import create_with_retry, get_step_config
from .db import get_artifact, get_job, get_learned_style_rules, upsert_artifact

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

### 【見出しの可読性】
8. H2見出しの可読性チェック（記事完成後、全H2を通しで点検）
   - 以下を検出し、表現だけ整える（主題＝その章が何の話かは変更しない）：
     (a) 30〜35字を大きく超える見出し → 情緒語（vocab）を削って主題を優先
     (b) 装飾過多（【】・｜・""強調 が2種類以上、または多用）→ 装飾は最大1種類に減らす
     (c) 先頭に情緒語（vocab）が来ている → 主題を先頭に出し、vocabは末尾に回す
     (d) 全H2のトーンが不揃い → 装飾・語調を揃える
   - 主題自体は変えず、語順・装飾・冗長語の調整にとどめること

### 【リード文の話法】
9. リード文（最初のH2より前の本文）の代弁問いかけ語尾を除去
   - 次の語尾・表現が残っていたら平叙文（言い切り）に直す：
     「〜ていませんか」「〜ませんか」「〜のではないでしょうか」「〜ではないでしょうか」
     「〜ですよね」「〜ますよね」「〜方は多い（はずです）」
   - 感情を代弁・同意要求する形をやめ、状況・事実・数字の描写に置き換える（意味は保つ）
   - 上記項目1（「ですよね」多用の言い換え）はリード文には適用せず、本項目を優先する
   - この処理はリード文のみ。H2末尾の感情補完文の語尾は変更しない

### 【学習済み執筆ルール】
10. 学習済み執筆ルールの適用（このアカウント独自の文体・表現・表記の好み）
   - ユーザーメッセージ末尾の「## 学習済み執筆ルール」に列挙があれば、記事全文へ一貫して適用する
   - 文体・語尾・表現・表記の統一にとどめ、記事の内容・意味・構成・見出し・事実は変えない
   - Markdown構造（見出しレベル・リスト・表・リンク）は保持する
   - 列挙が無い場合、または該当箇所が無い場合はこの項目を「変更なし」とする
   - 項目1・9（語尾・話法）と競合する場合は、このアカウント独自ルールを優先する

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
{word_count_instruction}
## 記事本文
{article_text}
{learned_rules_block}"""

WORD_COUNT_INSTRUCTION = """\
## 文字数調整（最優先）
現在の記事は {actual:,}字です。目標は「{target}」です。
{action}

"""

def _word_count_action(actual: int, target_str: str) -> str | None:
    """目標文字数と実文字数を比較し、調整指示文を返す。範囲内ならNone。"""
    import re as _re
    nums = [int(n.replace(',', '')) for n in _re.findall(r'[\d,]+', target_str)]
    if not nums:
        return None
    lo = nums[0] if len(nums) >= 1 else 0
    hi = nums[1] if len(nums) >= 2 else nums[0]
    if actual <= hi * 1.1:
        return None  # ±10%以内は調整不要
    # 超過している場合
    excess = actual - hi
    return (
        f"目標上限（{hi:,}字）を約{excess:,}字超過しています。\n"
        f"以下の優先順位でH2またはH3セクションを丸ごと削除し、{hi:,}字以内に収めてください。\n"
        f"削除優先順位（低優先から）: 補足・コラム系 > 潜在ニーズ対応 > 差別化 > 注意点・後悔しない系 > 向き不向き\n"
        f"セクションを薄く書き直すのではなく、優先度の低いセクションを丸ごと削除してください。"
    )


def _build_learned_rules_block(rules: list) -> str:
    """テナントの学習済み執筆ルール（校正からの学習）をレビュー指示用に整形する。"""
    texts = [r.get("rule_text", "").strip() for r in rules if r.get("rule_text", "").strip()]
    if not texts:
        return ""
    lines = [
        "",
        "## 学習済み執筆ルール（このアカウントの過去の校正から学習）",
        "以下を記事全文へ一貫適用してください（文体・表現・表記のみ。内容・構成・事実は変えない）：",
    ]
    for t in texts:
        lines.append(f"- {t}")
    return "\n".join(lines)


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


def run(job_id: str, keyword: str, api_key: str | None = None) -> dict:
    """Review and auto-fix the generated article."""
    print("[review] Starting article review...")

    article_artifact = get_artifact(job_id, "article")
    article_text = article_artifact["content_text"]
    actual_count = len(article_text)

    # 文字数調整指示・学習済み執筆ルールを構築
    word_count_instruction = ""
    learned_rules_block = ""
    try:
        job = get_job(job_id)
        target_str = job.get("word_count_setting")
        if target_str:
            action = _word_count_action(actual_count, target_str)
            if action:
                word_count_instruction = WORD_COUNT_INSTRUCTION.format(
                    actual=actual_count, target=target_str, action=action
                )
                print(f"[review] 文字数調整あり: {actual_count}字 → 目標「{target_str}」")
            else:
                print(f"[review] 文字数OK: {actual_count}字（目標「{target_str}」）")
        user_id = job.get("tenant_id")
        if user_id:
            learned_rules = get_learned_style_rules(user_id)
            learned_rules_block = _build_learned_rules_block(learned_rules)
            if learned_rules_block:
                print(f"[review] Loaded {len(learned_rules)} learned style rules")
    except Exception as e:
        print(f"[review] Warning: could not load job: {e}")

    client = anthropic.Anthropic(api_key=api_key)
    msg = create_with_retry(
        client,
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": REVIEW_TEMPLATE.format(
                    article_text=article_text,
                    word_count_instruction=word_count_instruction,
                    learned_rules_block=learned_rules_block,
                ),
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
