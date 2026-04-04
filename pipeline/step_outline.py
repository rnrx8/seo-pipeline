import anthropic
from .ai import create_with_retry, get_step_config
from .db import get_artifact, upsert_artifact

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
- SERP上位10件の本文文字数を推定し、その平均値±10%を目標文字数として明示する
- （推定できない場合は「4,000〜6,000字」とする）

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
- H2タイトル（疑問詞を積極使用：〜とは？いくら？なぜ？向いてる人は？など）
- H2直下方針：①章の結論（1文断言）→②配下H3の概要箇条書き→③読者の不安に寄り添う補完1文
- 配下のH3リスト

※以下のH2を必ず含めること：
- 向き不向き・適性に関するH2（潜在ニーズ対応）
- 後悔しない・失敗しないための注意点H2（感情的障壁の解消）
- 差別化H2（競合が扱っていない視点、step5の差別化ポイントから1つ以上）
"""


def run(job_id: str, keyword: str) -> dict:
    """Generate article outline using Claude."""
    print("[outline] Generating outline...")

    serp = get_artifact(job_id, "serp")
    intent = get_artifact(job_id, "search_intent")
    fact = get_artifact(job_id, "fact_sheet")

    client = anthropic.Anthropic()
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
                ),
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
