from .fresh_sources import WRITING_POLICY
from .content_quality import ContentQualityError, source_evidence, writing_evidence
import json
import re
import anthropic
from .article_quality import parse_length_budget, outline_sections, normalize_outline_headings
from .ai import create_with_retry, get_step_config, message_text
from .content_contract import contract_prompt, reference_prompt
from .db import (
    get_artifact,
    get_company_settings,
    get_job,
    get_service_by_id,
    get_cta_by_id,
    update_job_word_count_setting,
    upsert_artifact,
)

MODEL, MAX_TOKENS = get_step_config("outline")

SYSTEM_PROMPT = WRITING_POLICY + "\n" + """\
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
- 【必守】対策キーワードの語と語を直接隣接させない。語に分解し、語と語の間に必ず助詞
  （に・の・で・向け・なら 等）を1つ以上挟んで自然な句にする（語順は入れ替えてよい）。
  「おすすめ」「ランキング」「比較」等の語も直接くっつけず助詞で接続すること。
  例:「転職エージェント／おすすめ／30代」→「30代におすすめの転職エージェント」。
  - キーワードの主要語はすべて含めること（SEOのため）。前方に寄せてよいが、ベタ貼りにしない。
  - 良い例：「30代におすすめの転職エージェント7社比較｜目的別に失敗しない選び方【2026年版】」
  - 悪い例：「転職エージェントおすすめ30代｜…」（語を直接連結している＝禁止。必ず助詞を挟む）
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
- H2直下方針（順序厳守）：①見出しの問いに直接答える端的な結論を一文目に（前置き・遠回し禁止）→②並列要素はリスト/表で整理→③読者の不安に寄り添う補完・意図の汲み取りは後段に置く（答えより前に出さない）
- H2直下方針の直後に、そのH2全体について以下を必ず記載すること：
  - セクション内容：H2全体で伝える結論、扱う範囲、読者が得られる判断材料を1〜3文で具体的に記載する
  - 表現形式：H2直下で使う文章／箇条書き／番号付きリスト／比較表／データ表のうち最適なもの。表・リストを使う場合は何を解説するためかも記載する
  - 掲載項目：H2直下の表なら列項目、リストなら列挙する観点を具体的に記載する。文章のみの場合は「なし」とする
  - 使用する根拠：提供された[confirmed]要約と直接取得本文から、対象・条件を照合してH2直下で実際に使用する事実と出典URLを「根拠となる内容：URL」の形式で列挙する。根拠がない場合は「該当資料なし」とする
- 配下の各H3について、以下を必ず記載すること：
  - H3タイトル
  - セクション内容：そのH3で伝える結論、解説する論点、読者が得られる判断材料を1〜3文で具体的に記載する。「概要を説明する」のような抽象的な記述だけで終わらせない
  - 表現形式：文章／箇条書き／番号付きリスト／比較表／データ表のうち最適なもの。表・リストを使う場合は、何を解説するために使うかも記載する
  - 掲載項目：表なら列項目、リストなら列挙する観点や手順を具体的に記載する。文章のみの場合は「なし」とする
  - 使用する根拠：ファクトシート内の[confirmed]情報から、このH3で実際に使用する事実と出典URLを「根拠となる内容：URL」の形式で列挙する

【H3の根拠URLルール（必守）】
- ファクトシートに実在するURLを原文のまま使い、推測・補完・改変しない
- 確認済み要約または今回直接取得した原文のURLを割り当てる。[hypothesis]の主張自体は根拠に使わない
- URLだけを記載せず、そのURLが何の根拠になるかを併記する
- 表を使う場合は、どの列・比較項目をどのURLで裏付けるか分かるようにする
- 同じURLは、実際に複数セクションの根拠になる場合のみ重複してよい
- 使用できる[confirmed]情報がないH3ではURLを創作せず「該当資料なし」と明記する
- ファクトシートにある情報を全H3へ機械的に割り振らず、各セクションの主張と直接対応するものだけを選ぶ

各H2・H3は必ず次の形式で出力すること。H3タイトルを箇条書きにしてはならない：
### H2：見出しタイトル

**H2直下方針**：〜

- セクション内容：〜
- 表現形式：〜（〜を解説するため）
- 掲載項目：〜
- 使用する根拠：
  - 根拠となる内容：https://example.com/source

#### H3：見出しタイトル

- セクション内容：〜
- 表現形式：〜（〜を解説するため）
- 掲載項目：〜
- 使用する根拠：
  - 根拠となる内容：https://example.com/source

※根拠がない場合は「- 使用する根拠：該当資料なし」と1行で書く。空の「-」や、内容のない入れ子リストを絶対に出力しない

H3をさらに分割する必要がある場合のみH4を使用し、次の形式でH3と視覚的に区別すること：
##### H4：見出しタイトル

- セクション内容：〜
- 使用する根拠：該当資料なし

H4タイトルも箇条書きにしてはならない。H4が不要な場合は無理に追加しない

※必須H2は末尾の「コンテンツ構造契約」のrequired_sectionsだけとする。
向き不向き・注意点・差別化などは固定テンプレとして毎回追加せず、検索意図と論点に必要な場合のみ採用する。

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
- 5：コンテンツ構造契約の保護対象・CV直結・Primary検索意図の核心
- 4：検索意図に直結・潜在ニーズの中心的対応
- 3：基礎説明・文脈形成・補足
- 2：検索意図との関連が弱い補助的な章
- 1：FAQ・まとめ（短くまとめる）

全H2の推奨文字数合計が「目標文字数」セクションで設定した値と一致するよう調整すること。
H2タイトル列は上記H1/H2/H3構成で書いたタイトルをそのままコピーすること（「H2-1：」のような番号付けは禁止）。
※推奨文字数列は必ず「3,500字」のように半角数字＋「字」で書くこと。
"""


def _calc_target_word_count(serp_text: str) -> tuple[str, int] | None:
    """SERP競合本文の文字数から機械可読な目標文字数を算出する。

    serp_text は step_serp が保存した structured JSON 文字列。
    competitor_headings[].word_count（空白除外char_count）から
    fetch_status=='success' かつ 0<word_count<=30000 の値のみ収集し、
    異常値除外平均（n>=4なら最小1・最大1を除外）を取る。
    target = round(avg*1.2), hard_cap = round(avg*1.3)。
    返り値: (word_count_setting文字列, hard_cap)。算出不能ならNone。
    """
    try:
        data = json.loads(serp_text)
    except Exception:
        return None
    headings = data.get("competitor_headings") or []
    counts = []
    for h in headings:
        if not h or h.get("fetch_status") != "success":
            continue
        wc = h.get("word_count") or 0
        # 0/欠損/極端値（本文以外混入の保険）を除外
        if 0 < wc <= 30000:
            counts.append(wc)
    if not counts:
        return None
    counts.sort()
    if len(counts) >= 4:
        trimmed = counts[1:-1]
    else:
        trimmed = counts
    if not trimmed:
        return None
    avg = sum(trimmed) / len(trimmed)
    target = round(avg * 1.2)
    hard_cap = round(avg * 1.3)
    word_count_setting = f"{target:,}字（上限{hard_cap:,}字）"
    return word_count_setting, hard_cap


def _target_chars(setting: str | None) -> int:
    budget = parse_length_budget(setting)
    return budget.target if budget else 5000


def ensure_complete_volume_design(outline_text: str, word_count_setting: str | None) -> tuple[str, bool]:
    """Rebuild a truncated/incomplete volume table from the actual H2 list."""
    original = outline_text
    outline_text = normalize_outline_headings(outline_text)
    normalized = outline_text != original
    h2s = [
        match.group(1).strip()
        for match in re.finditer(
            r"^#{2,4}\s+H2(?:[-−]?\d+)?\s*[.．:：｜|]\s*(.+)$", outline_text, re.MULTILINE
        )
    ]
    if not h2s:
        return outline_text, normalized
    marker = re.search(r"^###\s+セクション別ボリューム設計\s*$", outline_text, re.MULTILINE)
    existing_titles: list[str] = []
    existing_chars: list[int] = []
    if marker:
        for line in outline_text[marker.end():].splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) >= 4 and re.fullmatch(r"[1-5]", cells[1]) and re.fullmatch(r"[\d,]+字", cells[2]):
                existing_titles.append(cells[0])
                existing_chars.append(int(cells[2][:-1].replace(',', '')))
    target = _target_chars(word_count_setting)
    sections = outline_sections(outline_text)
    children = [sum(s['level'] == 3 and s['parent'] == title for s in sections) for title in h2s]
    floors = [150 + count * 200 for count in children]
    if (len(existing_titles) == len(h2s) and all(title in existing_titles for title in h2s)
            and .9 * target <= sum(existing_chars) <= 1.1 * target
            and all(existing_chars[existing_titles.index(title)] >= floor
                    for title, floor in zip(h2s, floors))):
        return outline_text, normalized

    if sum(floors) > target:
        raise ContentQualityError('文字数目標に対して構成の子見出しが多すぎます。構成を絞って再生成してください。')
    weights = [5 if any(term in title for term in ("比較", "おすすめ", "ランキング")) else 4 for title in h2s]
    # Reserve space for every H3 before distributing the rest by depth/importance.
    depth_weights = [weight * (count + 1) for weight, count in zip(weights, children)]
    remaining = target - sum(floors)
    allocations = [floor + int(remaining * weight / sum(depth_weights))
                   for floor, weight in zip(floors, depth_weights)]
    allocations[-1] += target - sum(allocations)
    rows = [
        f"| {title} | {weight} | {chars:,}字 | 検索意図と構造契約に基づく配分 |"
        for title, weight, chars in zip(h2s, weights, allocations)
    ]
    table = (
        "### セクション別ボリューム設計\n\n"
        "| H2タイトル | 重要度(1-5) | 推奨文字数 | 根拠（1文） |\n"
        "|-----------|------------|-----------|-----------|\n"
        + "\n".join(rows)
        + "\n"
    )
    prefix = outline_text[:marker.start()].rstrip() if marker else outline_text.rstrip()
    return prefix + "\n\n" + table, True


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
        lines.append(f"必ず構成に含める内容（事実・数値は今回の確認結果を優先）：{service['must_include']}")
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
        lines.append(f"\n【参照必須URL】\n以下のURLの内容を記事中で必ず参照・引用・リンクしてください（今回取得した本文で確認できた事実のみ使用。URL指定だけで[confirmed]にしない）：\n{must_urls}")

    never_urls = job.get("never_reference_urls")
    if never_urls:
        lines.append(f"\n【参照・言及禁止URL/サイト】\n以下のURLまたはドメインは記事中で一切紹介・リンク・言及しないでください：\n{never_urls}")

    return "\n".join(lines)


def run(job_id: str, keyword: str, api_key: str | None = None, research_gaps: str = '') -> dict:
    """Generate article outline using Claude."""
    print("[outline] Generating outline...")

    serp = get_artifact(job_id, "serp")
    intent = get_artifact(job_id, "search_intent")
    fact = get_artifact(job_id, "fact_sheet")
    fact = {**fact, 'content_text': writing_evidence(fact['content_text'], source_evidence(get_artifact(job_id, 'fresh_sources')))}

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

    structure_prompts = ""
    structure_prompts += '''\n比較対象の件数は必要な情報を確認できた別々の対象から決める。
ユーザー指定件数は減らさない。自分で付けるN選の数字は実際の説明対象数と一致させる。
料金は同じ利用期間・必要機能・対象・税条件で比較し、プラン名だけで対応づけない。
優劣の結論は確認済み事実から導く。片方の指標が非公表なら大小を断定しない。
必要情報の未調査を「公式サイトで確認」の注釈で済ませない。'''
    if research_gaps:
        structure_prompts += '\n前回の不合格理由。追加調査結果を使い、この問題を解消する：\n' + research_gaps
    try:
        contract = json.loads(get_artifact(job_id, "content_contract")["content_text"])
        structure_prompts += contract_prompt(contract)
    except Exception as exc:
        raise ContentQualityError(f"content_contract artifact is required before outline: {exc}") from exc
    try:
        reference = json.loads(get_artifact(job_id, "reference_structure")["content_text"])
        structure_prompts += reference_prompt(reference)
    except Exception:
        pass

    # 企業設定・サービス・CTA を取得
    company_prompt = ""
    service_prompt = ""
    cta_prompt = ""
    extra_instructions = ""
    job = get_job(job_id)
    computed_target = None
    word_count_instruction = "- SERP上位10件の本文文字数を推定し、その平均値±10%を目標文字数として明示する\n- （推定できない場合は「4,000〜6,000字」とする）"
    try:
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
            # ユーザー指定優先。機械算出値で上書きしない。
            word_count_instruction = (
                f"- 目標文字数は「{word_count}」とする（SERP平均ではなくこの指定値を使うこと）\n"
                f"- この文字数に収まるようにH2・H3のセクション数を調整すること\n"
                f"- コンテンツ構造契約のprotected=trueの情報要件は残し、独立H2を固定せず重複章は統合する\n"
                f"- optional_sectionsや補足説明から調整する\n"
                f"- 全セクションを薄く書くより、優先度の高いセクションを充実させる構成を選ぶこと"
            )
            print(f"[outline] word_count_setting={word_count!r}")
        else:
            # ユーザー未設定時のみ、競合本文の異常値除外平均×1.2を機械算出して
            # 機械可読な目標文字数を確定し、jobに永続化（生成/レビューの真実源化）。
            computed = _calc_target_word_count(serp["content_text"])
            if computed:
                computed_str, hard_cap = computed
                job['word_count_setting'] = computed_str
                computed_target = computed_str
                word_count_instruction = (
                    f"- 目標文字数は「{computed_str}」とする（競合本文の異常値除外平均×1.2。これは推定ではなく確定値）\n"
                    f"- 全H2の推奨文字数合計がこの目標（上限{hard_cap:,}字）に収まるよう設計すること"
                )
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

    # Persistence is mandatory: later stages read this same budget from the job.
    # Do not swallow a DB failure and let them silently fall back to 5,000 chars.
    if computed_target is not None:
        update_job_word_count_setting(job_id, computed_target)
        print(f'[outline] computed word_count_setting={computed_target!r} (persisted)')

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
                ) + structure_prompts + chains_prompt + company_prompt + service_prompt + cta_prompt + extra_instructions,
            }
        ],
    )
    total_input = message.usage.input_tokens
    total_output = message.usage.output_tokens
    if getattr(message, "stop_reason", None) == "max_tokens":
        print('[outline] Response truncated; regenerating the complete outline with a larger limit')
        message = create_with_retry(
            client, model=MODEL, max_tokens=MAX_TOKENS * 2, system=SYSTEM_PROMPT,
            messages=[{'role': 'user', 'content': USER_TEMPLATE.format(
                keyword=keyword, intent_text=intent['content_text'], fact_text=fact['content_text'],
                serp_text=serp['content_text'], word_count_instruction=word_count_instruction,
            ) + structure_prompts + chains_prompt + company_prompt + service_prompt + cta_prompt + extra_instructions
                + '\n前回は出力上限で中断しました。全H2/H3と配分表まで省略せず、構成案全体を出力してください。'}],
        )
        total_input += message.usage.input_tokens
        total_output += message.usage.output_tokens
        if getattr(message, 'stop_reason', None) == 'max_tokens':
            raise ContentQualityError('構成案が出力上限で中断されました。未完成の構成から本文は生成できません。')
    if getattr(message, 'stop_reason', 'end_turn') != 'end_turn' or not message_text(message).strip():
        raise ContentQualityError('構成生成が正常終了していません。')
    outline_text = message_text(message)
    outline_text, volume_repaired = ensure_complete_volume_design(
        outline_text, job.get("word_count_setting")
    )
    if volume_repaired:
        print("[outline] Rebuilt incomplete section volume design")

    artifact = upsert_artifact(
        job_id=job_id,
        step="outline",
        content_type="text/markdown",
        content_text=outline_text,
        meta={
            "model": MODEL,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "volume_design_repaired": volume_repaired,
        },
    )
    print(f"[outline] Done → artifact id={artifact['id']}")
    return artifact
