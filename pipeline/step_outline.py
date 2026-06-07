import json
import anthropic
from .ai import create_with_retry, get_step_config
from .db import get_artifact, get_company_settings, get_job, get_service_by_id, get_cta_by_id, upsert_artifact

MODEL, MAX_TOKENS = get_step_config("outline")

SYSTEM_PROMPT = """\
あなたはSEOライティングの専門家です。
検索意図・ファクトシート・競合データをもとに、上位表示を狙える記事構成を作成してください。
"""

USER_TEMPLATE = """\
キーワード: {keyword}

## 検索意図の分析
{intent_text}

## ファクトシート
{fact_text}

## SERP上位10件のデータ
{serp_text}

---

以下の形式で記事構成案を作成してください。

## 記事構成案

### 目標文字数
{word_count_instruction}

### タイトル案（3パターン）
- 対策キーワードを左端に配置
- 32文字以内を目安、最大36文字
- 疑問詞（とは・いくら・なぜ・どのくらい・向いてる？）を少なくとも1案に使う

### リード文の設計方針
- 読者の潜在ニーズ・感情的障壁を起点にする（高年収・転職・不安などの感情から入る）
- 200字以内
- 末尾でこの記事を読むと何が解決するかを1文で示す

### H1 / H2 / H3 構成

各H2には以下を必ず記載すること：
- H2タイトル（疑問詞を積極使用：〜とは？いくら？なぜ？どうやって？どのくらい？何時間？何日？おすすめなのはどんな人？など）
  【H2タイトルの可読性ルール（必守）】
  - 先頭は必ず主題（その章が何の話か）を述べる。heading_vocab を見出しの先頭に置かない。主題を述べた後、必要な場合のみ末尾に heading_vocab を添える。
  - 1見出しに入れる heading_vocab は最大1つ。複数の情緒語を詰め込まない。
  - 装飾（【】・｜区切り・""強調 など）は控えめに。1見出しにつき装飾は最大1種類まで。全H2を通して装飾を多用し、トーンがうるさくならないようにする。
  - 長さの目安は30〜35字。これを大きく超える場合は heading_vocab を削って主題を優先する。
  - 良い例：「30代向け転職エージェントの選び方（失敗しないために）」
  - 悪い例：「【タイプ別診断】30代が"失敗しない"あなたに合う転職エージェントの選び方｜後悔しない」（先頭にvocab・装飾過多・vocab過多・長すぎ）
- H2直下方針：①章の結論（1文断言）→②配下H3の概要箇条書き→③読者の不安に寄り添う補完1文
- 配下のH3リスト

※以下のH2を必ず含めること：
- 向き不向き・適性に関するH2（潜在ニーズ対応）
- 後悔しない・失敗しないための注意点H2（感情的障壁の解消）
- 差別化H2（競合が扱っていない視点、step5の差別化ポイントから1つ以上）

### FAQ（任意）
- H1〜H3の構成を確定させてから、以下の条件をすべて満たす場合のみ追加する
- ①本文の見出しで扱っていない内容であること
- ②ファクトシートの[confirmed]タグのある内容で回答できること
- ③以下のいずれかに該当すること：
  - 競合が扱っていて検索意図とも合致するのに、本文で扱っていない疑問
  - 競合が扱っていない質問で、検索意図と合致する疑問（独自性のある切り口）
- 上記を満たさない場合はFAQを設けない

### セクション別ボリューム設計

H1〜H3構成（FAQを含む全セクション）を確定した後、各H2セクションの重要度と推奨文字数を以下のMarkdownテーブルで**必ず**出力すること。

| H2タイトル | 重要度(1-5) | 推奨文字数 | 根拠（1文） |
|-----------|------------|-----------|-----------|

重要度の基準：
- 5：CV直結・メイン比較セクション・差別化の核心
- 4：検索意図に直結・潜在ニーズの中心的対応
- 3：基礎説明・文脈形成・補足
- 2：向き不向き・注意点（必須だが簡潔でよい）
- 1：FAQ・まとめ（短くまとめる）

全H2の推奨文字数合計が「目標文字数」セクションで設定した値と一致するよう調整すること。
H2タイトル列は上記H1/H2/H3構成で書いたタイトルをそのままコピーすること（「H2-1：」のような番号付けは禁止）。
"""


def _build_company_prompt(companies: list, restriction: str = "ai") -> str:
    """企業設定リストをプロンプト文字列に変換する"""
    if not companies:
        return ""
    level_map = {
        5: ("最強おすすめ", "比較表1位・強い推薦文・CTA誘導を含める"),
        4: ("おすすめ", "比較表上位・推薦文あり"),
        3: ("条件付きおすすめ", "「〜な人向け」として条件付きで紹介"),
        2: ("消極的紹介", "特定ニーズがある人向けとして軽く触れる程度"),
        1: ("比較用掲載", "名前と特徴のみ記載・推薦文なし・他社を引き立てる文脈で登場"),
    }
    # level 0 は掲載しない
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
            "おすすめレベルに応じて比較表の順番・紹介文の強さを調整してください。",
            "",
        ]
    else:
        intro = [
            "",
            "【紹介企業設定】",
            "以下の企業を優先的に紹介してください。",
            "リスト外の企業も状況に応じて紹介してOKです。",
            "おすすめレベルに応じて比較表の順番・紹介文の強さを調整してください。",
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
    if service.get("must_include"):
        lines.append(f"必ず構成に含める内容：{service['must_include']}")
    if service.get("must_exclude"):
        lines.append(f"構成への記載禁止事項：{service['must_exclude']}")
    lines.append("上記のサービスを記事の主要な紹介対象として構成を設計してください。")
    return "\n".join(lines)


def _build_cta_prompt(cta: dict) -> str:
    lines = ["\n【CTA設定】"]
    lines.append(f"CTA名称：{cta.get('name', '')}")
    if cta.get("button_text") and cta.get("url"):
        lines.append(f"ボタンテキスト：{cta['button_text']} / URL：{cta['url']}")
    lines.append("上記のCTAを記事の適切なH2末尾に挿入することを構成案に明記してください。")
    return "\n".join(lines)


def _build_chains_prompt(chains: list) -> str:
    """検索意図chainsの見出し語彙をH2タイトル指示に追加する。serp_grounded=trueを優先採用。"""
    if not chains:
        return ""
    grounded = [c for c in chains if c.get("serp_grounded") and c.get("heading_vocab")]
    if not grounded:
        return ""
    lines = [
        "",
        "【検索意図チェーン：見出しの言葉選び】",
        "以下はSERPの実語彙に接地した見出し語彙です。疑問詞だけに頼らず、関連するH2タイトルに織り込むこと。",
        "ただし上記「H2タイトルの可読性ルール」を厳守：主題を先頭に置き、heading_vocab は末尾に最大1つ添えるだけにする（先頭に置かない・複数詰め込まない・装飾過多にしない）。",
        "（例:「30代向け転職エージェントの選び方」→「30代向け転職エージェントの選び方（失敗しないために）」）",
    ]
    for c in grounded:
        origin = c.get("origin", "")
        lines.append(f"- 「{c['heading_vocab']}」（起点: {origin}）")
    lines.append("※ SERP非接地の語彙は見出しに使わず本文側に委ねるため、ここには含めていません。")
    lines.append("")
    return "\n".join(lines)


def _build_extra_instructions(job: dict) -> str:
    """記事目的・ターゲット層・自由記述をプロンプトに追加する（文字数は目標文字数セクションで設定済みのため除外）"""
    lines = []

    purpose = job.get("article_purpose")
    if purpose:
        lines.append(f"\n【記事目的】{purpose}")
        lines.append("上記の目的に合わせてCTA・誘導文・CV導線の設計を行うこと。")

    target = job.get("target_audience")
    if target:
        lines.append(f"\n【ターゲット層】{target}")
        lines.append("上記のターゲットに合わせた言葉選び・視点・事例を使うこと。")

    custom = job.get("custom_prompt")
    if custom:
        lines.append(f"\n【追加指示】\n{custom}")

    must_urls = job.get("must_reference_urls")
    if must_urls:
        lines.append(f"\n【参照必須URL】\n以下のURLの内容を記事中で必ず参照・引用・リンクしてください（[confirmed]情報として扱う）：\n{must_urls}")

    never_urls = job.get("never_reference_urls")
    if never_urls:
        lines.append(f"\n【参照・言及禁止URL/サイト】\n以下のURLまたはドメインは記事中で一切紹介・リンク・言及しないでください：\n{never_urls}")

    return "\n".join(lines)


def run(job_id: str, keyword: str, api_key: str | None = None) -> dict:
    """Generate article outline using Claude."""
    print("[outline] Generating outline...")

    serp = get_artifact(job_id, "serp")
    intent = get_artifact(job_id, "search_intent")
    fact = get_artifact(job_id, "fact_sheet")

    # 検索意図chains（見出し語彙の受け口）。無くてもパイプラインは継続。
    chains_prompt = ""
    try:
        chains_artifact = get_artifact(job_id, "intent_chains")
        chains = json.loads(chains_artifact["content_text"]).get("chains", [])
        chains_prompt = _build_chains_prompt(chains)
        if chains_prompt:
            print(f"[outline] Loaded {len(chains)} intent chains for heading vocab")
    except Exception:
        pass

    # 企業設定・サービス・CTA を取得
    company_prompt = ""
    service_prompt = ""
    cta_prompt = ""
    extra_instructions = ""
    word_count_instruction = "- SERP上位10件の本文文字数を推定し、その平均値±10%を目標文字数として明示する\n- （推定できない場合は「4,000〜6,000字」とする）"
    try:
        job = get_job(job_id)
        user_id = job.get("tenant_id")
        category = job.get("category")
        if user_id and category:
            companies = get_company_settings(user_id, category)
            restriction = job.get("company_restriction", "ai")
            company_prompt = _build_company_prompt(companies, restriction)
            if company_prompt:
                print(f"[outline] Loaded {len(companies)} company settings for category='{category}'")
        word_count = job.get("word_count_setting")
        if word_count:
            word_count_instruction = (
                f"- 目標文字数は「{word_count}」とする（SERP平均ではなくこの指定値を使うこと）\n"
                f"- この文字数に収まるようにH2・H3のセクション数を調整すること\n"
                f"- 必須H2（向き不向き・注意点・差別化）は残し、補足・潜在ニーズ対応などの優先度が低いセクションから削る\n"
                f"- 全セクションを薄く書くより、優先度の高いセクションを充実させる構成を選ぶこと"
            )
            print(f"[outline] word_count_setting={word_count!r}")
        extra_instructions = _build_extra_instructions(job)
        service_id = job.get("service_id")
        if service_id:
            service = get_service_by_id(service_id)
            if service:
                service_prompt = _build_service_prompt(service)
                print(f"[outline] Loaded service: {service.get('name')}")
        cta_id = job.get("cta_id")
        if cta_id:
            cta = get_cta_by_id(cta_id)
            if cta:
                cta_prompt = _build_cta_prompt(cta)
                print(f"[outline] Loaded CTA: {cta.get('name')}")
    except Exception as e:
        print(f"[outline] Warning: could not load job settings: {e}")

    client = anthropic.Anthropic(api_key=api_key)
    message = create_with_retry(
        client,
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": USER_TEMPLATE.format(
                    keyword=keyword,
                    intent_text=intent["content_text"],
                    fact_text=fact["content_text"],
                    serp_text=serp["content_text"],
                    word_count_instruction=word_count_instruction,
                ) + chains_prompt + company_prompt + service_prompt + cta_prompt + extra_instructions,
            }
        ],
    )
    outline_text = message.content[0].text

    artifact = upsert_artifact(
        job_id=job_id,
        step="outline",
        content_type="text/markdown",
        content_text=outline_text,
        meta={"model": MODEL, "input_tokens": message.usage.input_tokens, "output_tokens": message.usage.output_tokens},
    )
    print(f"[outline] Done → artifact id={artifact['id']}")
    return artifact
