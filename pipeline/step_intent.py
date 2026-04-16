import json
import anthropic
from .ai import create_with_retry, get_step_config
from .db import get_artifact, upsert_artifact

MODEL, MAX_TOKENS = get_step_config("search_intent")

QUERY_ATTRS_PROMPT = """\
以下の検索結果データから、このキーワードのクエリ属性を分析してJSON形式のみで返してください。

キーワード: {keyword}

検索結果:
{serp_text}

以下のJSON形式のみを返してください（説明文不要）：
{{
  "gender_tendency": "男性多め|女性多め|混在（均等）|不明",
  "age_range": "推定年齢層（例：20〜30代中心、40代以上）",
  "content_types": ["まとめ・比較記事", "体験談・口コミ", "公式サイト・LP", "専門家コラム", "動画コンテンツ"],
  "searcher_stage": "情報収集|比較検討|購入・申込直前|複数混在",
  "key_concerns": ["読者が最も気にしていること1", "気にしていること2", "気にしていること3"],
  "competition_level": "高|中|低",
  "notes": "その他の特記事項（任意、なければnull）"
}}
"""

SYSTEM_PROMPT = """\
あなたはSEOの専門家です。検索結果から検索意図を深く分析してください。
表面的な「知りたいこと」だけでなく、読者の心理・不安・潜在ニーズまで掘り下げること。
"""

USER_TEMPLATE = """\
キーワード: {keyword}

## Google検索結果（上位10件）
{serp_text}

---

以下の形式で検索意図を分析してください。

## 検索意図の分析

### 1. クエリタイプ
- Primary（メイン意図）: Informational / Commercial / Transactional / Navigationalのいずれか1つ
- Secondary（隣接意図）: 最大2つ（例：比較・料金・口コミ・選び方）

### 2. 顕在ニーズ（検索者が明示的に知りたいこと）
箇条書きで5〜8項目

### 3. 潜在ニーズ（検索者が言語化していない不安・疑問・本音）

「なぜ」を5回繰り返した先にある動機を掘り下げること。
以下の視点を汎用的に適用して掘り下げる：

【欲求・動機の深層】
- その情報を得て「どうなりたいのか」の根本動機
- 他者との比較・相対的な焦りがあるか
  （例：同世代・周囲と比べて遅れている感覚、取り残される不安）
- 時間的な切迫感があるか
  （例：今動かないと手遅れになるという焦り）
- 経済的・生活上の具体的な不安が背景にあるか
  （例：収入・支出・将来設計に関わる切迫感）

【現状への閉塞感・不満】
- 現在の状況に対する「もったいない」「もっとできるはず」という感覚
- 今の環境・選択肢に限界を感じているか
- 変化を望んでいるが踏み出せない理由は何か

【感情的障壁（行動を妨げているもの）】
- 失敗・後悔への恐れ
- 情報の信頼性への不信（広告・バイアスへの警戒）
- 選択肢が多すぎて判断できない混乱
- 自分には該当しないかもしれないという自己不信

これらはKWのジャンル（転職・購買・学習・健康など）に関わらず
普遍的に存在する動機構造として分析すること。
ジャンル固有の例が出る場合はあくまで例として扱い、
分析軸自体はどのKWにも再現できる形で出力する。

抽出した潜在ニーズは記事本文に直接書く必要はないが、
リード文・H2末尾の感情補完文・見出しの言葉選びに
反映させること（後続ステップへの引き継ぎ情報として明記）。

### 4. ターゲット読者像
- 属性（年齢・職業・状況）
- 抱えている課題・感情
- 記事を読んだあとに取ってほしい行動

### 5. 競合コンテンツの傾向と差別化ポイント
- 上位記事が共通して扱っているH2トピック（3〜5個）
- 上位記事に抜けている視点（ここを差別化H2にする）
"""


def run(job_id: str, keyword: str) -> dict:
    """Extract search intent from SERP results using Claude."""
    print("[search_intent] Analyzing search intent...")

    serp = get_artifact(job_id, "serp")

    client = anthropic.Anthropic()
    message = create_with_retry(
        client,
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": USER_TEMPLATE.format(keyword=keyword, serp_text=serp["content_text"])}
        ],
    )
    intent_text = message.content[0].text

    artifact = upsert_artifact(
        job_id=job_id,
        step="search_intent",
        content_type="text/markdown",
        content_text=intent_text,
        meta={"model": MODEL, "input_tokens": message.usage.input_tokens, "output_tokens": message.usage.output_tokens},
    )
    print(f"[search_intent] Done → artifact id={artifact['id']}")

    # クエリ属性をHaikuで生成
    try:
        print("[search_intent] Generating query attributes...")
        attrs_msg = create_with_retry(
            client,
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            system="SEOの専門家として、検索クエリの属性をJSONで分析してください。",
            messages=[{
                "role": "user",
                "content": QUERY_ATTRS_PROMPT.format(keyword=keyword, serp_text=serp["content_text"][:3000]),
            }],
        )
        attrs_text = attrs_msg.content[0].text.strip()
        match = __import__("re").search(r"\{[\s\S]*\}", attrs_text)
        if match:
            attrs_json = json.loads(match.group(0))
            upsert_artifact(
                job_id=job_id,
                step="query_attrs",
                content_type="application/json",
                content_text=json.dumps(attrs_json, ensure_ascii=False),
            )
            print("[search_intent] Query attributes saved.")
    except Exception as e:
        print(f"[search_intent] Warning: could not generate query attributes: {e}")

    return artifact
