import time
import anthropic
from .ai import create_with_retry, get_step_config
from .db import get_artifact, get_company_settings, get_job, upsert_artifact

MODEL, MAX_TOKENS = get_step_config("article")

PART_DELAY = 20  # seconds between API calls

SYSTEM_PROMPT = """\
あなたはSEOライティングの専門家です。
構成案とファクトシートに基づき、読者にとって価値が高くSEOにも強い記事を執筆してください。

【執筆ルール】

▼ 文体
- 本文はです・ます調に統一（見出し・表・リストは除く）
- 一文は最大60文字。超える場合は文を分割する（感情表現・共感表現は例外として残す）
- 不要な修飾語・接続詞は削除する（ただし「削っても意味が通じ、かつ読んだ時の温度感が変わらないもの」だけ削る）

▼ リード文
- 構成は3パートで書く：
  ①冒頭1〜2文：読者の感情・状況をそのまま代弁する
    （「〜と感じていませんか」「〜で迷っている方は多いはずです」
    のような共感ファーストの書き出し）
  ②中間1文：その不安が生まれる理由や背景を補強する
  ③末尾1文：この記事で何が解決するかを示す
- 全体200字以内
- 一文の目安は60字だが、感情表現・共感表現は例外として残す
- 「削る」より「読者に寄り添えているか」を優先する
- 文末は「〜はずです」「〜ではないでしょうか」など
  読者に語りかけるトーンを使ってよい
- 市場規模・背景などの数値的な前置きは書かない

▼ H2見出し
- 疑問詞を積極的に使う（〜とは？いくら？なぜ？どのくらい？向いてる人は？）

▼ H2直下の構成（必須・例外なし）
  ①章の結論を1文で断言（主語＋述語）
  ②（任意）配下H3の内容を箇条書きで示す場合は、
    リストの直前に「この章では〜について解説します」
    「〜のポイントは以下の通りです」など
    リストが何を示すのかを1文で必ず添える。
    H3が2つ以下、または①の結論文から自然につながる場合は
    リストを省略してよい。無理にリストを入れない。
  ③読者の不安・期待に寄り添う補完1文

▼ H3直下
- 必ず本文1文から始める（表・リストのみで始めることを禁止）
- 各H3は200字以上

▼ プレーンテキストの制限
- H3内でプレーンテキストが連続する場合、300字を超えたら必ず以下のいずれかで分割：
  - 箇条書きリスト
  - 比較表・データ表
  - H4見出しで分割
- 数値・統計が複数並ぶ箇所は必ず表にまとめ、
  本文はその表から「何が言えるか」を1〜2文で要約するだけにする

▼ H2末尾の感情補完文
- 少し砕けた口調でよい
- 「〜でしょう」「〜と言えます」より「〜ですよね」「〜のはずです」程度のトーンが望ましい
- 読者の不安・焦り・期待に直接語りかける形にする
  例：「自分にM&A業界が向いているか、まだ判断がつかない方も多いはずです。」

▼ H2末尾の補完文の注意点
- 共感文にするために無理に語尾を調整する必要は無し
- 語尾で共感をする場合はバリエーションを使い分ける：
  「〜はずです」「〜ではないでしょうか」
  「〜が大切です」「〜をおすすめします」

▼ ファクトの使い方
- [confirmed]タグのついた数値・データのみ本文に使用する
- [hypothesis]タグのものは本文に書かない
- 根拠のない数値・推測を断言しない

▼ 潜在ニーズの反映
- 検索意図で抽出した潜在ニーズ（生活不安・閉塞感・感情的障壁）を
  リード文と各H2末尾の補完文に必ず1箇所以上反映させる

▼ その他
- 各H2末尾に読者の不安を和らげる「安心1文」を入れる
- 向き不向き・後悔しないための視点を必ず含める
- 文字数は構成案の目標文字数に合わせる
"""

USER_TEMPLATE = """\
キーワード: {keyword}

## 検索意図の分析（潜在ニーズをリード文・H2末尾に反映すること）
{intent_text}

## 構成案
{outline_text}

## ファクトシート（[confirmed]のみ本文に使用）
{fact_text}

---

"""

PART1_INSTRUCTION = """\
上記の構成案に沿って、Markdownで記事本文を執筆してください。
今回はリード文〜H2の3つ目程度（記事全体の約1/3）を書いてください。
途中で終わってよいです。最後に【PART1_END】と書いてください。"""

PART2_INSTRUCTION = """\
上記の続きから、残りの約半分（H2の4つ目〜6つ目程度）を書いてください。
途中で終わってよいです。最後に【PART2_END】と書いてください。"""

PART3_INSTRUCTION = """\
上記の続きから最後のまとめセクションまで書いてください。
必ずまとめまで書き切ってください。最後に【PART3_END】と書いてください。"""


def _call(client: anthropic.Anthropic, messages: list) -> tuple[str, int, int]:
    """Single API call. Returns (text, input_tokens, output_tokens)."""
    msg = create_with_retry(
        client,
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=messages,
    )
    return msg.content[0].text, msg.usage.input_tokens, msg.usage.output_tokens


def _build_company_prompt(companies: list, restriction: str = "ai") -> str:
    """企業設定リストをプロンプト文字列に変換する（step_outlineと同じロジック）"""
    if not companies:
        return ""
    level_map = {
        5: ("最強おすすめ", "比較表1位・強い推薦文・CTA誘導を含める"),
        4: ("おすすめ", "比較表上位・推薦文あり"),
        3: ("条件付きおすすめ", "「〜な人向け」として条件付きで紹介"),
        2: ("消極的紹介", "特定ニーズがある人向けとして軽く触れる程度"),
        1: ("比較用掲載", "名前と特徴のみ記載・推薦文なし・他社を引き立てる文脈で登場"),
    }
    by_level: dict[int, list] = {}
    for c in companies:
        lv = c.get("recommend_level", 0)
        if lv == 0:
            continue
        by_level.setdefault(lv, []).append(c)

    if not by_level:
        return ""

    if restriction == "registered_only":
        intro = [
            "",
            "【紹介企業設定】",
            "【重要】記事内で紹介・比較する企業は以下のリストに含まれる企業のみとしてください。",
            "リストにない企業は一切紹介・言及しないでください。",
            "構成案で決まった比較表の順番・紹介文の方針を守りながら、以下の企業を本文に反映してください。",
            "おすすめレベルに応じて紹介文の強さを調整してください。",
            "",
        ]
    else:
        intro = [
            "",
            "【紹介企業設定】",
            "以下の企業を優先的に紹介してください。",
            "リスト外の企業も状況に応じて紹介してOKです。",
            "構成案で決まった比較表の順番・紹介文の方針を守りながら、以下の企業を本文に反映してください。",
            "おすすめレベルに応じて紹介文の強さを調整してください。",
            "",
        ]

    lines = intro
    for lv in sorted(by_level.keys(), reverse=True):
        label, instruction = level_map.get(lv, (str(lv), ""))
        lines.append(f"レベル{lv}（{label}）：")
        for c in by_level[lv]:
            name = c.get("name", "")
            url = c.get("affiliate_url", "")
            notes = c.get("notes", "")
            lines.append(f"- 会社名：{name} / URL：{url} / notes：{notes}")
        lines.append(f"  → {instruction}")
        lines.append("")
    return "\n".join(lines)


def run(job_id: str, keyword: str) -> dict:
    """Generate full article in 3 parts using Claude Opus."""
    print("[article] Generating article (3 parts)...")

    intent = get_artifact(job_id, "search_intent")
    outline = get_artifact(job_id, "outline")
    fact = get_artifact(job_id, "fact_sheet")

    # 企業設定を取得
    company_prompt = ""
    try:
        job = get_job(job_id)
        user_id = job.get("tenant_id")
        category = job.get("category")
        if user_id and category:
            companies = get_company_settings(user_id, category)
            restriction = job.get("company_restriction", "ai")
            company_prompt = _build_company_prompt(companies, restriction)
            if company_prompt:
                print(f"[article] Loaded {len(companies)} company settings for category='{category}'")
    except Exception as e:
        print(f"[article] Warning: could not load company settings: {e}")

    base_user = USER_TEMPLATE.format(
        keyword=keyword,
        intent_text=intent["content_text"],
        outline_text=outline["content_text"],
        fact_text=fact["content_text"],
    ) + company_prompt

    client = anthropic.Anthropic()
    total_input = total_output = 0

    # --- Part 1 ---
    print("[article] Part 1/3...")
    messages = [{"role": "user", "content": base_user + PART1_INSTRUCTION}]
    part1_text, ti, to = _call(client, messages)
    total_input += ti
    total_output += to
    print(f"[article] Part 1 done ({to} tokens)")

    print(f"  (waiting {PART_DELAY}s...)")
    time.sleep(PART_DELAY)

    # --- Part 2 ---
    print("[article] Part 2/3...")
    messages += [
        {"role": "assistant", "content": part1_text},
        {"role": "user", "content": PART2_INSTRUCTION},
    ]
    part2_text, ti, to = _call(client, messages)
    total_input += ti
    total_output += to
    print(f"[article] Part 2 done ({to} tokens)")

    print(f"  (waiting {PART_DELAY}s...)")
    time.sleep(PART_DELAY)

    # --- Part 3 ---
    print("[article] Part 3/3...")
    messages += [
        {"role": "assistant", "content": part2_text},
        {"role": "user", "content": PART3_INSTRUCTION},
    ]
    part3_text, ti, to = _call(client, messages)
    total_input += ti
    total_output += to
    print(f"[article] Part 3 done ({to} tokens)")

    # --- Combine & clean markers ---
    combined = part1_text + "\n" + part2_text + "\n" + part3_text
    for marker in ("【PART1_END】", "【PART2_END】", "【PART3_END】"):
        combined = combined.replace(marker, "")
    article_text = combined.strip()

    artifact = upsert_artifact(
        job_id=job_id,
        step="article",
        content_type="text/markdown",
        content_text=article_text,
        meta={
            "model": MODEL,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "parts": 3,
        },
    )
    print(f"[article] Done (total output: {total_output} tokens) → artifact id={artifact['id']}")
    return artifact
