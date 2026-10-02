from .section_identity import bind_sections
from .fresh_sources import WRITING_POLICY
from .content_quality import ContentQualityError, source_evidence, writing_evidence
import json
import re
import anthropic
from .article_quality import parse_length_budget, validate_delivery
from .ai import create_with_retry, get_step_config
from .db import get_artifact, get_job, get_learned_style_rules, upsert_artifact
from .content_contract import contract_prompt
from .step_structure_guard import validate_structure

MODEL, MAX_TOKENS = get_step_config("review")

SYSTEM_PROMPT = WRITING_POLICY + "\n" + """\
あなたはSEOライティングの品質レビュー担当編集者です。
提供された記事を以下のチェックリストに従って検査・修正し、
修正済み記事とレビューサマリーを指定フォーマットで出力してください。

【登録情報への巻き戻り防止】
- 本文・比較表・CTA内のサービスの料金・件数・機能・条件を、今回のファクトシートと照合する。
- 今回の[confirmed]と矛盾する古い登録値は修正し、根拠がない断定は削除する。CTAのリンク先・配置は保持する。
- 以下の「内容・事実は変えない」は文体整形時の原則。今回の確認済み事実と矛盾する記述の修正は実施し、サマリーに記録する。

【チェック・修正項目】

### 【文章の意味と自然さ】
- 誤字脱字、主述や修飾のねじれ、意味不明な指示語、途切れた文、不自然な語の組合せを修正する。
- 読者分析のラベルを読者への呼びかけに転記せず、具体的な状況として伝える。
- 各見出しの問いに本文が答えているか確認し、未回答を導入文だけで取り繕わない。
- 同じ説明・締め文を新しい情報なく反復しない。必要な条件の再掲・要約は残す。
- CTAの配置を増やさない。根拠のない安全保証は本文・見出し・まとめを通じて修正する。

### 【重複・冗長】
1. 「ですよね」多用の修正
   - 1記事中に3回以上出現する場合
   - 3回目以降を以下のいずれかに書き換える：
     「〜はずです」「〜ではないでしょうか」「〜が大切です」「〜をおすすめします」
   - 自然に締まる場合は共感表現を省略してもよい

### 【構造チェック】
2. H2直下の結論文チェック（見出しへの直答・順序）
   - 結論文なしでリストや表から始まっているH2を検出 →「〜です。」形式の1文結論を冒頭に追加する
   - 一文目が前置き・背景・遠回しから始まっている（見出しの問いに直接答えていない）H2を検出し、
     見出しに端的に答える一文を冒頭に置き直す（背景説明は後段へ移す）
   - 寄り添い・共感文が答えより前に来ている場合は、答え→（リスト/表）→寄り添いの順に並べ直す
   - 内容・事実は変えず、順序と一文目の端的さのみ調整する

3. H3直下の本文チェック
   - 本文なしでリストや表から始まっているH3を検出
   - 導入文を1文追加する

4. 表・リスト直前の導入文チェック
   - 表やリストの直前に導入文がない場合を検出
   - 「〜は以下の通りです。」「〜を整理します。」などを追加する

### 【文字数・文体】
5. プレーンテキスト連続300字超の検出・修正
   - H2直下・H3・H4内でプレーンテキストが300字を超えて連続している箇所を検出
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

9. 紹介サービス見出しの重複チェック
   - 同じサービスを主題にしたH2が複数ある場合は、記事の流れに合う1つへ統合する
   - 比較H2内のサービス別H3で十分に説明できる場合、汎用的な「特徴・料金・始め方」H2を追加しない
   - 料金・始め方は検索意図や利用判断に必要な場合だけ残し、機械的に必須扱いしない

### 【学習済み執筆ルール】
10. 学習済み執筆ルールの適用（このアカウント独自の文体・表現・表記の好み）
   - ユーザーメッセージ末尾の「## 学習済み執筆ルール」に列挙があれば、記事全文へ一貫して適用する
   - 文体・語尾・表現・表記の統一にとどめ、記事の内容・意味・構成・見出し・事実は変えない
   - Markdown構造（見出しレベル・リスト・表・リンク）は保持する
   - 列挙が無い場合、または該当箇所が無い場合はこの項目を「変更なし」とする
   - 項目1（語尾・話法）と競合する場合は、このアカウント独自ルールを優先する

### 【リスト・表の整形】
11. 並列・列挙内容のリスト化チェック
   - 文章で連ねられた並列・列挙（複数のポイント／比較対象／注意点／メリット・デメリット／
     手順・ステップ）を検出し、箇条書きリストまたは表に整形する
   - 比較対象が複数並ぶ箇所は表に、手順・順序があるものは番号付きリストにする
   - リスト・表の直前に、それが何を示すかの導入文を1文添える
   - 要素が2つ以下、または1文で自然に収まる箇所は無理にリスト化しない（内容・事実は変えない）

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
{content_contract_block}
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
    budget = parse_length_budget(target_str)
    if budget is None:
        return None
    if actual < budget.minimum:
        return (
            f'目標{budget.target:,}字に対し大幅に不足しています。構成案の未執筆項目と比較対象ごとの説明を補完してください。'
            '確認済みの根拠だけを使用し、同じ説明の反復や水増しは禁止です。'
        )
    if actual <= budget.maximum:
        return None
    hi = budget.maximum
    # 超過している場合
    excess = actual - hi
    return (
        f"目標上限（{hi:,}字）を約{excess:,}字超過しています。\n"
        f"重複説明、冗長な例、長い導入、同内容のH3から先に圧縮し、{hi:,}字以内を目指してください。\n"
        "コンテンツ構造契約のprotected=trueは情報要件の保護です。独立H2の固定ではありません。\n"
        "要件を満たす説明は残しつつ、重複H2は統合し、optional_sectionsの補助説明から圧縮してください。\n"
        f"それでも収まらない場合だけoptional_sectionsに属する補助H2を削除できます。"
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


def _parse_response(text: str) -> tuple[str, str, bool]:
    """区切り文字でarticleとsummaryを分割して返す。

    第3要素はARTICLEマーカーが見つかったか（=パース成功）の真偽。
    Falseのとき呼出側は壊れた応答で元記事を上書きしないこと。
    """
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
    return article, summary, bool(article_match)


def run(job_id: str, keyword: str, api_key: str | None = None) -> dict:
    """Review and auto-fix the generated article."""
    print("[review] Starting article review...")

    article_artifact = get_artifact(job_id, "article")
    article_text = article_artifact["content_text"]
    actual_count = len(article_text)
    fact_sheet_text = writing_evidence(get_artifact(job_id, "fact_sheet")["content_text"], source_evidence(get_artifact(job_id, "fresh_sources")))
    outline_text = get_artifact(job_id, 'outline')['content_text']
    job = get_job(job_id)
    try:
        contract = json.loads(get_artifact(job_id, "content_contract")["content_text"])
    except Exception as exc:
        raise ValueError(f"content_contract artifact is required before review: {exc}") from exc
    content_contract_block = contract_prompt(contract)

    # 文字数調整指示・学習済み執筆ルールを構築
    word_count_instruction = ""
    learned_rules_block = ""
    try:
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
                    content_contract_block=content_contract_block,
                    learned_rules_block=learned_rules_block,
                ) + '\n## 完成すべき構成案\n' + outline_text
                + '\n構成のH2/H3/H4を省略せず、見出しの主題を維持する。内部メモ・要確認の比較表は完成品に残さない。\n'
                + "\n## 今回のファクトシート\n" + fact_sheet_text,
            }
        ],
    )

    raw = msg.content[0].text
    corrected_article, summary, article_ok = _parse_response(raw)

    # 破損ガード: 応答がtruncation(stop_reason=max_tokens)、もしくは
    # ===ARTICLE_START===/END=== マーカーが欠落している場合は、壊れた応答で
    # レビュー前の良い記事を上書きしない。元記事(article_text)を保持する。
    truncated = getattr(msg, "stop_reason", None) == "max_tokens"
    review_ok = (not truncated) and article_ok
    review_skipped_reason = None
    if not review_ok:
        review_skipped_reason = "truncated" if truncated else "parse_failed"
        print(
            f"[review] WARNING: response truncated={truncated} marker_found={article_ok}. "
            "Keeping pre-review article."
        )
        corrected_article = article_text
        summary = (
            f"⚠️ レビュー応答が不完全（理由: {review_skipped_reason}）のため、"
            "レビュー前の記事を保持しました。\n" + summary
        )

    structure_violations = validate_structure(corrected_article, contract, outline=False)
    structure_violations += validate_delivery(corrected_article, outline_text, job.get('word_count_setting'), contract=contract)
    if structure_violations:
        print(f"[review] WARNING: review broke protected structure: {structure_violations}. Keeping pre-review article.")
        corrected_article = article_text
        review_ok = False
        review_skipped_reason = "structure_contract_violation"
        summary = "⚠️ レビュー後に保護対象の構造欠落を検出したため、レビュー前の記事を保持しました。\n" + summary

    upsert_artifact(
        job_id=job_id,
        step="article_reviewed",
        content_type="text/markdown",
        content_text=corrected_article,
        meta={"review_model": MODEL, "reviewed": review_ok, "review_skipped_reason": review_skipped_reason},
    )

    # Supabaseに修正済み記事を上書き（破損時は元記事を保持）
    meta = {
        **(article_artifact.get("meta") or {}),
        "review_model": MODEL,
        "review_input_tokens": msg.usage.input_tokens,
        "review_output_tokens": msg.usage.output_tokens,
        "reviewed": review_ok,
        "section_map": bind_sections(corrected_article, outline_text, contract),
    }
    if review_skipped_reason:
        meta["review_skipped_reason"] = review_skipped_reason
    artifact = upsert_artifact(
        job_id=job_id,
        step="article",
        content_type="text/markdown",
        content_text=corrected_article,
        meta=meta,
    )

    print("[review] Done")
    print()
    print("=== Review Summary ===")
    print(summary)
    print("======================")
    print(f"artifact id={artifact['id']}")
    if not review_ok:
        raise ContentQualityError('校閲が完了しませんでした。元の本文を保持し、完了扱いを停止しました。')
    return artifact
