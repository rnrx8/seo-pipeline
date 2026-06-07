import json
import re as _re
import time
import anthropic
from .ai import create_with_retry, get_step_config
from .db import get_artifact, get_company_settings, get_job, get_service_by_id, get_cta_by_id, upsert_artifact

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
- 構成は3パートで書く（共感→背景→解決の骨格は維持）：
  ①冒頭1〜2文：読者が置かれた具体的な状況・事実・数字を描写して入る。
    感情を抽象的に代弁するだけで終わらせず、その感情を生む状況を具体的に描き、
    読者に自分で当てはめさせる（例：「修理費5万円は一人暮らしの食費2ヶ月分に
    あたります」）。共感・問いかけ（代弁）は使ってよいが、下記の一文目ルールに従う。
  ②中間1文：その状況が生まれる理由や背景を補強する
  ③末尾1文：この記事で何が解決するかを示す
- 全体200字以内 / 一文の目安は60字（感情表現は例外として残してよい）
- 一文目の「型」を毎回変える。特に「『〜』と〜ていませんか／感じていませんか」
  （引用＋同意要求の問いかけ）を既定の書き出しにしない。
  一文目は ①状況・事実の言い切り ②数字・データ ③情景描写 ④短い断片の列挙 など
  から、キーワードごとに選ぶ。代弁・問いかけはリード文の2〜3文目で使ってよい。
- 本筋と無関係な市場規模・背景の数値的な前置きは書かない。
  ただし読者の状況を察させる具体的な事実・数字は冒頭で使ってよい。
- concrete_phrase は原文のまま活かす（具体ワードで共感を作る方向と整合）。

▼ H2見出し
- 疑問詞を積極的に使う（〜とは？いくら？なぜ？どうやって？どのくらい？何時間？何日？おすすめなのはどんな人？）

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
- リード文・各H2末尾の補完文には、検索意図チェーンの concrete_phrase（具体化ワード）を
  最低1つ、原文の言葉のまま使うこと。
- 抽象ラベル（「安全欲求に訴求」「閉塞感を解消」等）ではなく、
  具体化された言葉そのもの（数字・比較・情景を含む表現）を本文に入れること。

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


def _parse_volume_design(outline_text: str) -> list[tuple[str, int, int]]:
    """Parse volume design table from outline. Returns [(h2_title, importance, word_count), ...]."""
    import unicodedata
    # 全角数字（３５００字）・全角｜区切り・全角，が混じってもパースできるよう正規化（表抽出専用）
    outline_text = unicodedata.normalize('NFKC', outline_text)
    results = []
    pattern = r'\|\s*([^|\n]+?)\s*\|\s*([1-5])\s*\|\s*([\d,]+)字\s*\|'
    for m in _re.finditer(pattern, outline_text):
        title = m.group(1).strip()
        if title in ('H2タイトル', 'H2', '---', ''):
            continue
        # Strip "H2-1：" or "H2-1:" numbering prefixes Claude may add
        title = _re.sub(r'^[Hh]\d+[-‐]\d+[：:]\s*', '', title).strip()
        title = _re.sub(r'^[Hh]\d+[：:]\s*', '', title).strip()
        if not title:
            continue
        importance = int(m.group(2))
        word_count = int(m.group(3).replace(',', ''))
        if word_count > 0:
            results.append((title, importance, word_count))
    return results


def _split_sections_into_parts(
    sections: list[tuple[str, int, int]]
) -> tuple[list, list, list]:
    """Divide volume sections into 3 roughly equal-weight parts by word count."""
    n = len(sections)
    if n == 0:
        return [], [], []
    if n <= 2:
        return sections[:1], [], sections[1:]

    total = sum(wc for _, _, wc in sections)
    t1, t2 = total / 3, 2 * total / 3

    cum = 0
    split1 = split2 = 0
    for i, (_, _, wc) in enumerate(sections):
        cum += wc
        if cum <= t1:
            split1 = i + 1
        if cum <= t2:
            split2 = i + 1

    split1 = max(1, min(split1, n - 2))
    split2 = max(split1 + 1, min(split2, n - 1))
    return sections[:split1], sections[split1:split2], sections[split2:]


def _parse_word_count(word_count_setting: str | None) -> int | None:
    """'3,000〜5,000字' などの文字列を数値（目標文字数）に変換する。相対指定はNoneを返す。"""
    import re
    if not word_count_setting:
        return None
    nums = [int(n.replace(',', '')) for n in re.findall(r'[\d,]+', word_count_setting)]
    if not nums:
        return None
    if len(nums) == 1:
        return nums[0]
    return (nums[0] + nums[1]) // 2


# Safety cap (chars) for one PART when neither volume design nor
# word_count_setting is available. Prevents runaway expansion now that
# MAX_TOKENS was raised for the P0 truncation hotfix.
FALLBACK_PART_CHARS = 4000


def _calc_part_max_tokens(word_count_setting: str | None, part_chars: int | None = None) -> int:
    """Per-PART max_tokens. Uses part_chars (from volume design) when available.

    Japanese text: ~1.5 tokens/char. Add 1.5x buffer so Claude can finish
    a PART gracefully without being cut off mid-sentence.
    """
    if part_chars is not None:
        tokens = int(part_chars * 1.5 * 1.5)
        return max(2000, min(tokens, MAX_TOKENS))
    total = _parse_word_count(word_count_setting)
    if total is None:
        # No machine-readable target at all. Cap each PART to a safe size so
        # the (raised) MAX_TOKENS does not let unbounded articles balloon
        # (≈9000 tokens ≈ part budget). Across 3 parts this stays ~12K字.
        return min(int(FALLBACK_PART_CHARS * 1.5 * 1.5), MAX_TOKENS)
    per_part_chars = total // 3
    tokens = int(per_part_chars * 1.5 * 1.5)
    return max(2000, min(tokens, MAX_TOKENS))


def _build_part_instructions(
    word_count_setting: str | None,
    volume_sections: list[tuple[str, int, int]] | None = None,
    service_map: dict | None = None,
    service_name: str = "",
    cta_content: str = "",
) -> tuple[str, str, str]:
    """各PARTの指示文を返す。ボリューム設計データがあればセクション別の目安を明示する。"""
    if volume_sections:
        part1_secs, part2_secs, part3_secs = _split_sections_into_parts(volume_sections)
        total = sum(wc for _, _, wc in volume_sections)

        def _sec_label(secs):
            return "、".join(f"「{t}」({wc:,}字)" for t, _, wc in secs)

        def _service_reminder(secs):
            """Per-part service/CTA injection based on service_map."""
            if not service_map:
                return ""
            lines = []
            primary_h2 = service_map.get("primary_h2", "")
            per_instructions = service_map.get("per_section_instructions", {})
            cta_h2s = service_map.get("cta_after_h2", [])

            sec_titles = [t for t, _, _ in secs]
            for title in sec_titles:
                # Service treatment instructions for this section
                if primary_h2 and _h2_title_matches(title, primary_h2):
                    inst = per_instructions.get(primary_h2, per_instructions.get(title, ""))
                    if inst:
                        lines.append(f"\n【重要・サービス指示】「{title}」セクション：{inst}")
                    elif service_name:
                        lines.append(f"\n【重要・サービス指示】「{title}」セクションでは{service_name}を重点的に紹介してください。")
                # CTA insertion reminder
                for cta_h2 in cta_h2s:
                    if _h2_title_matches(title, cta_h2) and cta_content:
                        lines.append(f"\n【CTA必須】「{title}」セクションの末尾に以下のCTAブロックを必ず挿入すること：\n{cta_content}")
                        break
            return "\n".join(lines)

        p1_total = sum(wc for _, _, wc in part1_secs)
        p2_total = sum(wc for _, _, wc in part2_secs)

        p1_rem = _service_reminder(part1_secs)
        p2_rem = _service_reminder(part2_secs)
        p3_rem = _service_reminder(part3_secs)

        part1 = (
            f"上記の構成案に沿って、Markdownで記事本文を執筆してください。\n"
            f"今回はリード文と以下のH2セクションを書いてください：{_sec_label(part1_secs)}\n"
            f"このパートの目安文字数は約{p1_total:,}字です。自然な区切りで終えてください。"
            f"{p1_rem}\n"
            f"最後に【PART1_END】と書いてください。"
        )
        part2 = (
            f"上記の続きから以下のH2セクションを書いてください：{_sec_label(part2_secs)}\n"
            f"このパートの目安文字数は約{p2_total:,}字です。自然な区切りで終えてください。"
            f"{p2_rem}\n"
            f"最後に【PART2_END】と書いてください。"
        )
        part3 = (
            f"上記の続きから以下のH2セクションとまとめまで書いてください：{_sec_label(part3_secs)}\n"
            f"記事全体の目標文字数は{total:,}字です。必ずまとめまで書き切ってください。"
            f"{p3_rem}\n"
            f"最後に【PART3_END】と書いてください。"
        )
        return part1, part2, part3

    # Fallback: word_count_setting only
    total = _parse_word_count(word_count_setting)
    if total is None:
        return PART1_INSTRUCTION, PART2_INSTRUCTION, PART3_INSTRUCTION

    p1 = total // 3
    part1 = (
        f"上記の構成案に沿って、Markdownで記事本文を執筆してください。\n"
        f"今回はリード文〜H2の3つ目程度を書いてください。このパートは約{p1:,}字で止めてください。\n"
        f"自然に区切れる箇所で終えて構いません。最後に【PART1_END】と書いてください。"
    )
    part2 = (
        f"上記の続きから、H2の4つ目〜6つ目程度を書いてください。このパートも約{p1:,}字を目安にしてください。\n"
        f"自然に区切れる箇所で終えて構いません。最後に【PART2_END】と書いてください。"
    )
    part3 = (
        f"上記の続きから最後のまとめセクションまで書いてください。\n"
        f"記事全体の目標は約{total:,}字です。必ずまとめまで書き切ってください。最後に【PART3_END】と書いてください。"
    )
    return part1, part2, part3


def _h2_title_matches(actual: str, target: str) -> bool:
    """Fuzzy-match two H2 titles (tolerates minor wording differences)."""
    def norm(s: str) -> str:
        s = _re.sub(r'[？?！!：:。、・〜～「」【】（）()\s]', '', s)
        return s.lower()
    a, t = norm(actual), norm(target)
    if not a or not t:
        return False
    if a == t or a in t or t in a:
        return True
    # character-set overlap ≥ 70%
    set_a, set_t = set(a), set(t)
    if len(set_t) and len(set_a & set_t) / len(set_t) >= 0.7:
        return True
    return False


def _call(client: anthropic.Anthropic, messages: list, max_tokens: int | None = None) -> tuple[str, int, int]:
    """Single API call. Returns (text, input_tokens, output_tokens)."""
    msg = create_with_retry(
        client,
        model=MODEL,
        max_tokens=max_tokens if max_tokens is not None else MAX_TOKENS,
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

    lines = intro + [
        "【各社紹介セクションの形式】各社をH3で個別紹介する場合、以下の形式を守ること：",
        "  ①本文の後にスペック表を設ける（項目例：求人数・強み・こんな人向け・料金・公式サイト）",
        "  　公式サイト行の値はURLをそのまま記載する（例：https://www.r-agent.com）",
        "  ②スペック表の直後に以下のリンクを1行追加する：",
        "  　[▶ {会社名}の公式サイトはこちら]({URL})",
        "  　（例：[▶ リクルートエージェントの公式サイトはこちら](https://www.r-agent.com)）",
        "  ③H3見出しにレベルに応じた【1位】【2位】等のラベルを付けてよい",
        "",
    ]
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


def _build_service_prompt(service: dict) -> str:
    lines = ["\n【紹介サービス設定】"]
    lines.append(f"サービス名：{service.get('name', '')}")
    if service.get("url"):
        lines.append(f"URL：{service['url']}")
    sps = service.get("selling_points") or []
    if sps:
        lines.append("セールスポイント：")
        for sp in sps:
            lines.append(f"  - {sp}")
    if service.get("raw_content"):
        lines.append(f"サービス詳細情報：\n{service['raw_content'][:1200]}")
    if service.get("must_include"):
        lines.append(f"必ず本文に含める内容：{service['must_include']}")
    if service.get("must_exclude"):
        lines.append(f"本文への記載禁止事項：{service['must_exclude']}")
    lines.append("上記のサービスを記事の主要な紹介対象として自然に取り上げてください。")
    return "\n".join(lines)


def _build_cta_prompt(cta: dict) -> str:
    lines = ["\n【CTA設定】"]
    lines.append(f"CTA名称：{cta.get('name', '')}")
    if cta.get("body"):
        lines.append(f"CTAの本文（原則そのまま使用すること）：\n{cta['body']}")
    if cta.get("button_text"):
        lines.append(f"ボタンテキスト：{cta['button_text']}")
    if cta.get("url"):
        lines.append(f"ボタンURL：{cta['url']}")
    lines.append("上記のCTAを記事の適切な箇所（H2末尾・まとめ直前など）に自然に組み込んでください。")
    return "\n".join(lines)


def _build_chains_prompt(chains: list) -> str:
    """検索意図chainsの具体化ワードをリード文・H2末尾反映用にプロンプト化する。"""
    if not chains:
        return ""
    phrases = [c for c in chains if c.get("concrete_phrase")]
    if not phrases:
        return ""
    lines = [
        "",
        "【検索意図チェーン：地の文に入れる具体化ワード】",
        "以下の具体化ワードを、リード文と各H2末尾の補完文に原文の言葉のまま最低1つずつ反映すること。",
        "抽象ラベルに言い換えず、数字・比較・情景を含む言葉そのものを使うこと。",
    ]
    for c in phrases:
        direction = c.get("direction", "")
        lines.append(f"- 「{c['concrete_phrase']}」（{direction}）")
    lines.append("")
    return "\n".join(lines)


def _build_extra_instructions(job: dict) -> str:
    """記事目的・文字数・ターゲット層・自由記述をプロンプトに追加する"""
    lines = []

    purpose = job.get("article_purpose")
    if purpose:
        lines.append(f"\n【記事目的】{purpose}")
        lines.append("上記の目的に合わせてCTA・誘導文・CV導線を本文中に自然に組み込むこと。")

    word_count = job.get("word_count_setting")
    if word_count:
        lines.append(f"\n【文字数指定】目標文字数は「{word_count}」とすること。")
        lines.append("調整が必要な場合は、まとめ・FAQ・CV導線を除き、潜在ニーズ対応セクションから優先的に増減すること。")

    target = job.get("target_audience")
    if target:
        lines.append(f"\n【ターゲット層】{target}")
        lines.append("上記のターゲットに合わせた言葉選び・視点・事例を使うこと。")

    custom = job.get("custom_prompt")
    if custom:
        lines.append(f"\n【追加指示】\n{custom}")

    must_urls = job.get("must_reference_urls")
    if must_urls:
        lines.append(f"\n【参照必須URL】\n以下のURLの内容を記事中で必ず参照・引用・リンクしてください：\n{must_urls}")

    never_urls = job.get("never_reference_urls")
    if never_urls:
        lines.append(f"\n【参照・言及禁止URL/サイト】\n以下のURLまたはドメインは記事中で一切紹介・リンク・言及しないでください：\n{never_urls}")

    citation_style = job.get("citation_style") or "none"
    if citation_style == "inline_footnote":
        lines.append(
            "\n【出典表示】インライン注記スタイル"
            "\n- ファクトシートの[confirmed]情報を引用した箇所の直後に ※1、※2 … と連番で注記を付ける"
            "\n- 記事末尾に ## 出典一覧 セクションを追加し、番号順に「※N: URL（確認日）」を列挙する"
            "\n- [hypothesis]の情報は引用しないこと"
        )
    elif citation_style == "bottom_list":
        lines.append(
            "\n【出典表示】末尾リストスタイル"
            "\n- 本文中に注記は付けない"
            "\n- 記事末尾に ## 出典一覧 セクションを追加し、本文中で参照した[confirmed]情報のURLを箇条書きで列挙する"
            "\n- [hypothesis]の情報は引用しないこと"
        )
    elif citation_style == "h2_block":
        lines.append(
            "\n【出典表示】H2セクション末尾ブロックスタイル"
            "\n- 本文中に注記は付けない"
            "\n- 各H2セクションの末尾（次のH2の前）に **参考情報** として、そのセクションで参照した[confirmed]情報のURLを箇条書きで追加する"
            "\n- 参照情報がないH2セクションには追加不要"
            "\n- [hypothesis]の情報は引用しないこと"
        )
    # citation_style == "none" の場合は出典表示なし（現在の動作）

    return "\n".join(lines)


def run(job_id: str, keyword: str, api_key: str | None = None) -> dict:
    """Generate full article in 3 parts using Claude Opus."""
    print("[article] Generating article (3 parts)...")

    intent = get_artifact(job_id, "search_intent")
    outline = get_artifact(job_id, "outline")
    fact = get_artifact(job_id, "fact_sheet")

    # 検索意図chains（具体化ワードの受け口）。無くてもパイプラインは継続。
    chains_prompt = ""
    try:
        chains_artifact = get_artifact(job_id, "intent_chains")
        chains = json.loads(chains_artifact["content_text"]).get("chains", [])
        chains_prompt = _build_chains_prompt(chains)
        if chains_prompt:
            print(f"[article] Loaded {len(chains)} intent chains for concrete phrases")
    except Exception:
        pass

    # 企業設定・サービス・CTA を取得
    company_prompt = ""
    extra_instructions = ""
    service_prompt = ""
    cta_prompt = ""
    job = {}
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
        extra_instructions = _build_extra_instructions(job)
        service_id = job.get("service_id")
        if service_id:
            service = get_service_by_id(service_id)
            if service:
                service_prompt = _build_service_prompt(service)
                print(f"[article] Loaded service: {service.get('name')}")
        cta_id = job.get("cta_id")
        if cta_id:
            cta = get_cta_by_id(cta_id)
            if cta:
                cta_prompt = _build_cta_prompt(cta)
                print(f"[article] Loaded CTA: {cta.get('name')}")
    except Exception as e:
        print(f"[article] Warning: could not load job settings: {e}")

    word_count_setting = job.get("word_count_setting") if job else None

    # Load volume design from outline and service_map if available
    volume_sections = _parse_volume_design(outline["content_text"])
    service_map: dict | None = None
    try:
        sm_artifact = get_artifact(job_id, "service_map")
        service_map = json.loads(sm_artifact["content_text"])
        print(f"[article] Loaded service_map: type={service_map.get('service_section_type')}, cta_after={service_map.get('cta_after_h2')}")
    except Exception:
        pass

    # CTA content for inline injection reminders
    _cta_content = ""
    if service_map and cta_prompt:
        # Extract a concise CTA block from cta_prompt for inline reminders
        try:
            job_cta_id = job.get("cta_id") if job else None
            if job_cta_id:
                _cta_obj = get_cta_by_id(job_cta_id)
                if _cta_obj:
                    body = _cta_obj.get("body", "")
                    btn = _cta_obj.get("button_text", "")
                    url = _cta_obj.get("url", "")
                    _cta_content = f"> {body}\n>\n> **[{btn}]({url})**" if btn and url else body
        except Exception:
            pass

    _service_name = ""
    if service_map:
        try:
            job_svc_id = job.get("service_id") if job else None
            if job_svc_id:
                _svc = get_service_by_id(job_svc_id)
                _service_name = _svc.get("name", "") if _svc else ""
        except Exception:
            pass

    part1_inst, part2_inst, part3_inst = _build_part_instructions(
        word_count_setting,
        volume_sections=volume_sections or None,
        service_map=service_map,
        service_name=_service_name,
        cta_content=_cta_content,
    )

    # Per-part max tokens from volume design splits
    if volume_sections:
        p1_secs, p2_secs, p3_secs = _split_sections_into_parts(volume_sections)
        p1_max = _calc_part_max_tokens(word_count_setting, sum(wc for _, _, wc in p1_secs) if p1_secs else None)
        p2_max = _calc_part_max_tokens(word_count_setting, sum(wc for _, _, wc in p2_secs) if p2_secs else None)
        p3_max = _calc_part_max_tokens(word_count_setting, sum(wc for _, _, wc in p3_secs) if p3_secs else None)
        total_vol = sum(wc for _, _, wc in volume_sections)
        print(f"[article] Volume design: {len(volume_sections)} H2s, total={total_vol:,}字 → max_tokens: {p1_max}/{p2_max}/{p3_max}")
    else:
        p1_max = p2_max = p3_max = _calc_part_max_tokens(word_count_setting)
        if word_count_setting:
            print(f"[article] word_count_setting={word_count_setting!r} → max_tokens per PART = {p1_max}")

    base_user = USER_TEMPLATE.format(
        keyword=keyword,
        intent_text=intent["content_text"],
        outline_text=outline["content_text"],
        fact_text=fact["content_text"],
    ) + chains_prompt + company_prompt + service_prompt + cta_prompt + extra_instructions

    client = anthropic.Anthropic(api_key=api_key)
    total_input = total_output = 0

    # --- Part 1 ---
    print("[article] Part 1/3...")
    messages = [{"role": "user", "content": base_user + part1_inst}]
    part1_text, ti, to = _call(client, messages, max_tokens=p1_max)
    total_input += ti
    total_output += to
    print(f"[article] Part 1 done ({to} tokens, {len(part1_text)}字)")

    print(f"  (waiting {PART_DELAY}s...)")
    time.sleep(PART_DELAY)

    # --- Part 2 ---
    print("[article] Part 2/3...")
    messages += [
        {"role": "assistant", "content": part1_text},
        {"role": "user", "content": part2_inst},
    ]
    part2_text, ti, to = _call(client, messages, max_tokens=p2_max)
    total_input += ti
    total_output += to
    print(f"[article] Part 2 done ({to} tokens, {len(part2_text)}字)")

    print(f"  (waiting {PART_DELAY}s...)")
    time.sleep(PART_DELAY)

    # --- Part 3 ---
    print("[article] Part 3/3...")
    messages += [
        {"role": "assistant", "content": part2_text},
        {"role": "user", "content": part3_inst},
    ]
    part3_text, ti, to = _call(client, messages, max_tokens=p3_max)
    total_input += ti
    total_output += to
    print(f"[article] Part 3 done ({to} tokens, {len(part3_text)}字)")

    # --- Combine & clean markers ---
    combined = part1_text + "\n" + part2_text + "\n" + part3_text
    for marker in ("【PART1_END】", "【PART2_END】", "【PART3_END】"):
        combined = combined.replace(marker, "")
    article_text = combined.strip()

    # Unescape backslash-escaped markdown that Claude sometimes outputs (\*\* → **)
    article_text = _re.sub(r'\\\*\\\*', '**', article_text)
    article_text = _re.sub(r'\\\*', '*', article_text)
    article_text = _re.sub(r'\\_', '_', article_text)

    # --- A: [hypothesis] validation ---
    hypothesis_hits = _re.findall(r'\[hypothesis\]', article_text, _re.IGNORECASE)
    if hypothesis_hits:
        print(f"[article] WARNING: {len(hypothesis_hits)} [hypothesis] tag(s) found in article — Claude may have included unverified facts.")
        raise ValueError(
            f"Article contains {len(hypothesis_hits)} [hypothesis] tag(s). "
            "Only [confirmed] facts are allowed in the article body. "
            "Please retry or review the fact sheet."
        )

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
    print(f"[article] Done ({total_output} tokens total, {len(article_text)}字) → artifact id={artifact['id']}")
    return artifact
