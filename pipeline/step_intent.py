import json
import re
import anthropic
from .ai import create_with_retry, get_step_config
from .db import get_artifact, upsert_artifact

MODEL, MAX_TOKENS = get_step_config("search_intent")

# ─── 起点ヒント（query_attrs）─────────────────────────────────────────────────
# SERPから軽量にクエリ属性を生成する。用途は2つ:
#   (a) 記事詳細UIの「クエリ属性分析」カード（QueryAttrsCard）の表示
#   (b) コール①セクション3／コール②の「起点ヒント」(searcher_stage / key_concerns)
# 生成系の本体（chains）はコール②で別途生成するため、ここは従来スキーマを維持する。
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

## セクション3：潜在ニーズの深掘り（多起点 × 根源欲求）

（起点ヒント: searcher_stage="{searcher_stage}" / key_concerns={key_concerns} を参考にする）

### 手順
1.【起点の分解】対策KWを単語に分解し、各単語に5W1H（いつ/どこで/だれが/なにを/なぜ/どうやって）を
  掛け合わせて起点を作る。加えて、与えられたPAA・再検索キーワードが示す実際の問いも起点に含める。
  起点は最低6本。各起点が approach（〜したい/なりたい）か avoidance（〜したくない/避けたい）かを明示し、
  approach・avoidance を最低1本ずつ必ず含める。

2.【深掘り】各起点を次の3つの問いで掘る:
  ・それはなぜか？
  ・それをするとどうなるか？
  ・反対の言葉に言い換えると？（例：転職⇄現職維持、おすすめ⇄失敗例）
  掘る途中で出た語（＝中間ワード）は省略せず全部書き出す。

3.【着地】マズローの5段階欲求（生理的/安全/社会的/承認/自己実現）のいずれかに到達したら完了。
  全起点が到達する必要はないが、最低3本はいずれかの根源欲求まで到達させ、到達層を各チェーンに明記する。

4.【具体化】各チェーンの中間ワードのうち抽象的なものを、数字・比較・情景に置き換える。
  （例：「お金が減る」→「修理費5万円＝一人暮らしの食費2ヶ月分」）
  この具体化した言葉が、記事のリード文・見出しに直接入る言葉になる。

5.【SERP照合】具体化した中間ワードが、与えられたPAA・再検索KW・競合見出しの実語彙と一致するか確認する。
  一致するものは「SERP接地済」とマークする（見出しに優先使用する根拠）。
  一致しないものは推論として扱い、地の文（リード・H2末尾）向きとする。

### 注意
- マズローのラベル（「安全欲求」等）を結論として書いて終わらせない。主役は具体化した中間ワード。
- 複数チェーンが同じ根源欲求に収束したら代表1本に畳む（冗長回避）。
- ジャンル固有の例は例として扱い、深掘りの構造（起点→3問→マズロー）はどのKWでも再現できる形を保つ。
- 不安・回避方向だけに偏らせない（approach起点を必ず残す）。

### 4. ターゲット読者像
- 属性（年齢・職業・状況）
- 抱えている課題・感情
- 記事を読んだあとに取ってほしい行動

### 5. 競合コンテンツの傾向と差別化ポイント
- 上位記事が共通して扱っているH2トピック（3〜5個）
- 上位記事に抜けている視点（ここを差別化H2にする）
"""

# ─── コール②：中間ワード抽出（chains）──────────────────────────────────────
# コール①セクション3の深掘り結果から、下流（outline=見出し / article=地の文）が
# 機械的に使える構造化JSONを抽出する。query_attrs とは別アーティファクト intent_chains に保存。
CHAINS_PROMPT = """\
あなたは①の潜在ニーズ深掘りから、下流の見出し生成・本文生成が機械的に使える形を抽出します。
以下のJSONのみを出力してください（前後の説明・コードフェンス禁止）。チェーンは代表的な4〜6本に絞ること。

起点ヒント（参考）: searcher_stage="{searcher_stage}" / key_concerns={key_concerns}

## ①の潜在ニーズ深掘り
{intent_text}

## 出力JSON
{{
  "chains": [
    {{
      "origin": "起点（例：だれが×なぜ）",
      "direction": "approach | avoidance",
      "trace": "中間ワードを→でつないだ深掘りの軌跡",
      "maslow": "生理的 | 安全 | 社会的 | 承認 | 自己実現 | 未到達",
      "concrete_phrase": "リード/H2末尾に入れる具体化ワード（数字・比較・情景）",
      "heading_vocab": "見出しに織り込める短い語彙（例：失敗しない選び方／あなたに合う）",
      "serp_grounded": true
    }}
  ]
}}
"""


def _generate_query_attrs(client: anthropic.Anthropic, job_id: str, keyword: str, serp_text: str) -> dict:
    """SERPからクエリ属性を生成して保存。起点ヒント(dict)を返す。失敗時は空dict。"""
    try:
        print("[search_intent] Generating query attributes (hint)...")
        attrs_msg = create_with_retry(
            client,
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            system="SEOの専門家として、検索クエリの属性をJSONで分析してください。",
            messages=[{
                "role": "user",
                "content": QUERY_ATTRS_PROMPT.format(keyword=keyword, serp_text=serp_text[:3000]),
            }],
        )
        attrs_text = attrs_msg.content[0].text.strip()
        match = re.search(r"\{[\s\S]*\}", attrs_text)
        if not match:
            return {}
        attrs_json = json.loads(match.group(0))
        upsert_artifact(
            job_id=job_id,
            step="query_attrs",
            content_type="application/json",
            content_text=json.dumps(attrs_json, ensure_ascii=False),
        )
        print("[search_intent] Query attributes saved.")
        return attrs_json
    except Exception as e:
        print(f"[search_intent] Warning: could not generate query attributes: {e}")
        return {}


def _salvage_chains_json(text: str) -> dict | None:
    """切り詰められたchains JSONから、完結しているchainオブジェクトだけを拾って復元する。
    末尾の不完全な要素は捨てる。1件も拾えなければ None。"""
    start = text.find("[")
    if start == -1:
        return None
    objs: list[str] = []
    depth = 0
    obj_start: int | None = None
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and obj_start is not None:
                objs.append(text[obj_start:i + 1])
                obj_start = None
    if not objs:
        return None
    try:
        return json.loads('{"chains": [' + ",".join(objs) + "]}")
    except json.JSONDecodeError:
        return None


def _generate_chains(
    client: anthropic.Anthropic,
    job_id: str,
    intent_text: str,
    searcher_stage: str,
    key_concerns: list,
) -> None:
    """コール①の深掘り結果から中間ワードchainsを抽出して intent_chains に保存。失敗してもパイプラインは止めない。"""
    try:
        print("[search_intent] Extracting intent chains...")
        chains_msg = create_with_retry(
            client,
            model="claude-haiku-4-5-20251001",
            max_tokens=3000,
            system="あなたはSEOの専門家として、潜在ニーズ深掘りから構造化された中間ワードを抽出します。",
            messages=[{
                "role": "user",
                "content": CHAINS_PROMPT.format(
                    intent_text=intent_text,
                    searcher_stage=searcher_stage or "不明",
                    key_concerns=key_concerns or [],
                ),
            }],
        )
        # 出力打ち切りの監視（将来チェーンが増えて再truncateしたら即気づけるように恒久ログ化）
        if chains_msg.stop_reason == "max_tokens":
            print("[search_intent] WARNING: chains response hit max_tokens (truncated). "
                  "Consider raising max_tokens in _generate_chains.")

        chains_text = chains_msg.content[0].text.strip()
        match = re.search(r"\{[\s\S]*\}", chains_text)
        if not match:
            print("[search_intent] Warning: no chains JSON found; skipping intent_chains.")
            return
        try:
            chains_json = json.loads(match.group(0))
        except json.JSONDecodeError as e:
            # truncate等で不完全JSON → 完結要素だけサルベージして継続
            salvaged = _salvage_chains_json(chains_text)
            if salvaged is None:
                print(f"[search_intent] Warning: chains JSON parse failed ({e}) and salvage failed; "
                      "skipping intent_chains.")
                return
            print(f"[search_intent] Note: chains JSON was truncated ({e}); salvaged "
                  f"{len(salvaged.get('chains', []))} complete chain(s).")
            chains_json = salvaged

        chains = chains_json.get("chains", []) if isinstance(chains_json, dict) else []

        # 偏り監視用: approach/avoidance と maslow の分布をmetaに残す
        direction_dist: dict[str, int] = {}
        maslow_dist: dict[str, int] = {}
        for c in chains:
            direction_dist[c.get("direction", "?")] = direction_dist.get(c.get("direction", "?"), 0) + 1
            maslow_dist[c.get("maslow", "?")] = maslow_dist.get(c.get("maslow", "?"), 0) + 1

        upsert_artifact(
            job_id=job_id,
            step="intent_chains",
            content_type="application/json",
            content_text=json.dumps(chains_json, ensure_ascii=False),
            meta={"chain_count": len(chains), "direction_dist": direction_dist, "maslow_dist": maslow_dist},
        )
        print(f"[search_intent] Intent chains saved ({len(chains)} chains, dir={direction_dist}, maslow={maslow_dist}).")
    except Exception as e:
        # 握りつぶし再発防止のため、例外の型・メッセージは恒久的にログへ残す（パイプラインは止めない）
        print(f"[search_intent] Warning: could not extract intent chains: {type(e).__name__}: {e}")


def run(job_id: str, keyword: str, api_key: str | None = None) -> dict:
    """Extract search intent from SERP results using Claude (multi-origin × Maslow)."""
    print("[search_intent] Analyzing search intent...")

    serp = get_artifact(job_id, "serp")
    serp_text = serp["content_text"]

    client = anthropic.Anthropic(api_key=api_key)

    # 起点ヒント（query_attrs）を先に生成 → コール①/②に渡す（step①②連結）
    attrs = _generate_query_attrs(client, job_id, keyword, serp_text)
    searcher_stage = attrs.get("searcher_stage", "不明")
    key_concerns = attrs.get("key_concerns", [])

    # コール①（Opus）: 多起点×マズローで潜在ニーズを深掘り
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
                    serp_text=serp_text,
                    searcher_stage=searcher_stage,
                    key_concerns=key_concerns,
                ),
            }
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

    # コール②（Haiku）: 深掘り結果から中間ワードchainsを抽出 → intent_chains
    _generate_chains(client, job_id, intent_text, searcher_stage, key_concerns)

    return artifact
