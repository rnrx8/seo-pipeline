"""Analyze the article outline to determine service/CTA placement.

This step is deliberately read-only for the outline. Structural mutation belongs
to step_structure_guard so an LLM placement decision cannot append duplicate H2s.
"""
from .fresh_sources import WRITING_POLICY
from .content_quality import source_evidence, writing_evidence
import json
import re as _re
import anthropic
from .ai import create_with_retry, get_step_config
from .db import get_artifact, get_job, get_service_by_id, get_cta_by_id, upsert_artifact

MODEL, MAX_TOKENS = get_step_config("service_map")

_COMPARISON_KEYWORDS = ("比較", "おすすめ", "ランキング", "一覧")

SYSTEM_PROMPT = WRITING_POLICY + "\n" + """\
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
- 独立したサービスH2がなくても、比較H2内のサービスH3で十分に紹介できる場合は
  comparison_featured とし、専用H2を新設する前提で判断しない
- per_section_instructions: 比較セクション・サービス専用セクションなど、サービスへの言及が必要なH2のみ記載
  構成と提供された確認済み要約・直接取得本文の範囲に限定し、裏付けのない数値・機能・優劣を作らない。
- cta_after_h2: 以下の優先順で最大3箇所を選ぶ
  1. 比較セクションの末尾
  2. サービス専用紹介セクションの末尾（あれば）
  3. まとめの直前のH2末尾
  （文脈に合わない場合は2箇所でも可）
"""


def _has_dedicated_service_h2(outline_text: str, service_name: str) -> bool:
    """Return True if the outline already has a standalone H2 dedicated to this service.

    A dedicated section talks *about* the service (紹介・解説).
    A how-to section that *uses* the service as a tool (〇〇を使って...) does NOT count.
    """
    # Patterns that indicate the H2 is *about* the service (introduction / deep-dive)
    about_markers = (
        'の特徴', 'の解説', 'の紹介', 'について', 'とは', 'の詳細',
        'の評判', 'の料金', 'の使い方', 'の活用', 'がおすすめ', 'を徹底',
    )
    # Patterns that indicate the service is used *as a tool* inside a how-to section
    tool_markers = ('を使って', 'を使った', 'を活用して', 'を活用した', 'による')

    for m in _re.finditer(
        r'^#{2,4}\s+H2(?:[-−]?\d+)?\s*[.．：:｜|]\s*(.+)$', outline_text, _re.MULTILINE
    ):
        title = m.group(1).strip()
        if service_name not in title:
            continue
        # 「おすすめ」はサービス単体の推薦H2にも使われるため、比較章の
        # 判定語に含めない。比較・ランキング・一覧のみを除外する。
        if any(kw in title for kw in ("比較", "ランキング", "一覧")):
            continue  # comparison/ranking section → not a dedicated section
        if any(kw in title for kw in tool_markers):
            continue  # service is used as a tool, not introduced
        # Dedicated: service name is the primary subject
        if (
            any(kw in title for kw in about_markers)
            or _re.search(r"おすすめ(?:な|の|する)?理由", title)
            or title.startswith(service_name)
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

def _format_service_info(service: dict) -> str:
    lines = [f"サービス名：{service.get('name', '')}"]
    if service.get("url"):
        lines.append(f"URL：{service['url']}")
    sps = service.get("selling_points") or []
    if sps:
        lines.append("セールスポイント：" + "、".join(sps))
    if service.get("must_include"):
        lines.append(f"必須記載内容（事実・数値は今回の確認結果で更新し、未確認なら断定しない）：{service['must_include']}")
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(job_id: str, keyword: str, api_key: str | None = None) -> dict:
    """Analyze outline and determine service/CTA placement.

    Stores a service_map artifact with placement instructions. The outline is
    never patched here; step_structure_guard owns all structural repairs.
    """
    print("[service_map] Analyzing outline for service/CTA placement...")

    outline = get_artifact(job_id, "outline")
    outline_text = outline["content_text"]

    service: dict | None = None
    cta: dict | None = None
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

    # Resolve the natural destination from the validated outline. Prefer an
    # existing service-focused H2; otherwise use the existing comparison H2.
    h2_titles = [
        m.group(1).strip()
        for m in _re.finditer(
            r'^#{2,4}\s+H2(?:[-−]?\d+)?\s*[.．：:｜|]\s*(.+)$', outline_text, _re.MULTILINE
        )
    ]
    service_name = service.get("name", "") if service else ""
    service_h2s = [
        title for title in h2_titles
        if service_name
        and service_name in title
        and not any(term in title for term in ("比較", "ランキング", "一覧"))
        and bool(_re.search(
            r"特徴|強み|おすすめ(?:な|の|する)?理由|おすすめ|選ば|料金|始め方|使い方|活用|紹介|とは",
            title,
        ))
    ]
    comparison_h2s = [
        title for title in h2_titles
        if any(term in title for term in _COMPARISON_KEYWORDS)
    ]
    natural_primary_h2 = service_h2s[0] if service_h2s else (comparison_h2s[0] if comparison_h2s else "")

    # --- Ask Claude to determine placement instructions ---
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
                    outline_text=outline_text + "\n## 今回の確認済み事実\n" + writing_evidence(get_artifact(job_id, "fact_sheet")["content_text"], source_evidence(get_artifact(job_id, "fresh_sources"))),
                    service_info=service_info,
                    cta_info=cta_info,
                    article_purpose=article_purpose,
                ),
            }
        ],
    )

    raw = message.content[0].text.strip()
    if "```" in raw:
        m = _re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, _re.DOTALL)
        if m:
            raw = m.group(1)

    try:
        service_map = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[service_map] JSON parse error: {e}\nRaw: {raw[:200]}")
        service_map = {
            "service_section_type": "none",
            "primary_h2": "",
            "per_section_instructions": {},
            "cta_after_h2": [],
            "reasoning": "parse_error",
        }

    if service and natural_primary_h2:
        service_map["service_section_type"] = "dedicated" if service_h2s else "comparison_featured"
        service_map["primary_h2"] = natural_primary_h2
        per = service_map.setdefault("per_section_instructions", {})
        per.setdefault(
            natural_primary_h2,
            f"{service_name}の具体的な価値・他候補との違い・向いている人を説明する。"
            "料金や始め方は、この検索意図で利用判断に必要な場合だけ補足する。",
        )
        # Drop hallucinated headings from the model output. Every instruction
        # and CTA target must refer to an H2 that actually exists in the outline.
        service_map["per_section_instructions"] = {
            title: instruction
            for title, instruction in per.items()
            if title in h2_titles
        }
        cta_targets = [title for title in (service_map.get("cta_after_h2") or []) if title in h2_titles]
        if cta and natural_primary_h2 not in cta_targets:
            cta_targets.append(natural_primary_h2)
        service_map["cta_after_h2"] = cta_targets[:3]

    print(
        f"[service_map] type={service_map.get('service_section_type')}, "
        f"primary_h2={service_map.get('primary_h2')!r}, "
        f"cta_after={service_map.get('cta_after_h2')}"
    )

    artifact = upsert_artifact(
        job_id=job_id,
        step="service_map",
        content_type="application/json",
        content_text=json.dumps(service_map, ensure_ascii=False),
        meta={"model": MODEL, "input_tokens": message.usage.input_tokens, "output_tokens": message.usage.output_tokens},
    )
    print(f"[service_map] Done → artifact id={artifact['id']}")
    return artifact
