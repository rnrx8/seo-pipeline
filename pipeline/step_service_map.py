"""Analyze the article outline to determine service/CTA placement instructions."""
import json
import anthropic
from .ai import create_with_retry, get_step_config
from .db import get_artifact, get_job, get_service_by_id, get_cta_by_id, upsert_artifact

MODEL, MAX_TOKENS = get_step_config("service_map")

SYSTEM_PROMPT = """\
あなたはSEOコンテンツ設計の専門家です。
記事構成案を分析して、自社サービスの最適な紹介方法とCTA挿入位置を決定してください。
必ず指定のJSON形式のみで回答してください。余分なテキストは書かないこと。
"""

USER_TEMPLATE = """\
## 記事構成案
{outline_text}

## 自社サービス情報
{service_info}

## CTA情報
{cta_info}

## 記事目的
{article_purpose}

---

上記を分析して、以下のJSON形式のみで回答してください（余分な説明・マークダウン不要）：

{{
  "service_section_type": "dedicated" または "comparison_featured" または "comparison_only" または "none",
  "primary_h2": "サービスを主に扱うH2タイトル（構成案のH2タイトルをそのままコピー）",
  "per_section_instructions": {{
    "H2タイトル（構成案のタイトルをそのままコピー）": "そのH2でのサービス扱い方の具体的指示（1〜3文）"
  }},
  "cta_after_h2": ["CTAを挿入するH2タイトル1（構成案のタイトルをそのままコピー）", "H2タイトル2"],
  "reasoning": "判断の根拠（1文）"
}}

判断ルール：
- service_section_type:
  - dedicated: 構成案に自社サービス専用のH2セクションがある
  - comparison_featured: 比較セクションで自社サービスを1位・最強推薦として扱う
  - comparison_only: 比較の一社として触れる程度
  - none: サービス情報なし
- primary_h2: 構成案のH2タイトルをそのまま使う（書き換え禁止）
- per_section_instructions: 比較セクション・サービス専用セクションなど、サービスへの言及が必要なH2のみ記載
- cta_after_h2: 以下の優先順で最大3箇所を選ぶ
  1. 比較セクションの末尾
  2. サービス専用紹介セクションの末尾
  3. まとめの直前のH2末尾
  （文脈に合わない場合は2箇所でも可）
"""


def _format_service_info(service: dict) -> str:
    lines = [f"サービス名：{service.get('name', '')}"]
    if service.get("url"):
        lines.append(f"URL：{service['url']}")
    sps = service.get("selling_points") or []
    if sps:
        lines.append("セールスポイント：" + "、".join(sps))
    if service.get("must_include"):
        lines.append(f"必ず含める内容：{service['must_include']}")
    if service.get("must_exclude"):
        lines.append(f"記載禁止：{service['must_exclude']}")
    return "\n".join(lines)


def _format_cta_info(cta: dict) -> str:
    lines = [f"CTA名称：{cta.get('name', '')}"]
    if cta.get("body"):
        lines.append(f"本文：{cta['body'][:200]}")
    if cta.get("button_text") and cta.get("url"):
        lines.append(f"ボタン：{cta['button_text']} → {cta['url']}")
    return "\n".join(lines)


def run(job_id: str, keyword: str, api_key: str | None = None) -> dict:
    """Analyze outline and determine service/CTA placement. Stores service_map artifact."""
    print("[service_map] Analyzing outline for service/CTA placement...")

    outline = get_artifact(job_id, "outline")

    service_info = "（なし）"
    cta_info = "（なし）"
    article_purpose = "情報提供"

    try:
        job = get_job(job_id)
        article_purpose = job.get("article_purpose") or "情報提供"

        service_id = job.get("service_id")
        if service_id:
            service = get_service_by_id(service_id)
            if service:
                service_info = _format_service_info(service)
                print(f"[service_map] Service: {service.get('name')}")

        cta_id = job.get("cta_id")
        if cta_id:
            cta = get_cta_by_id(cta_id)
            if cta:
                cta_info = _format_cta_info(cta)
                print(f"[service_map] CTA: {cta.get('name')}")
    except Exception as e:
        print(f"[service_map] Warning: could not load job settings: {e}")

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
                    outline_text=outline["content_text"],
                    service_info=service_info,
                    cta_info=cta_info,
                    article_purpose=article_purpose,
                ),
            }
        ],
    )

    raw = message.content[0].text.strip()
    # Extract JSON even if wrapped in code block
    if "```" in raw:
        import re
        m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
        if m:
            raw = m.group(1)

    try:
        service_map = json.loads(raw)
        print(
            f"[service_map] type={service_map.get('service_section_type')}, "
            f"primary_h2={service_map.get('primary_h2')!r}, "
            f"cta_after={service_map.get('cta_after_h2')}"
        )
    except json.JSONDecodeError as e:
        print(f"[service_map] JSON parse error: {e}\nRaw: {raw[:200]}")
        # Fallback: empty map so downstream steps don't crash
        service_map = {
            "service_section_type": "none",
            "primary_h2": "",
            "per_section_instructions": {},
            "cta_after_h2": [],
            "reasoning": "parse_error",
        }

    artifact = upsert_artifact(
        job_id=job_id,
        step="service_map",
        content_type="application/json",
        content_text=json.dumps(service_map, ensure_ascii=False),
        meta={"model": MODEL, "input_tokens": message.usage.input_tokens, "output_tokens": message.usage.output_tokens},
    )
    print(f"[service_map] Done → artifact id={artifact['id']}")
    return artifact
