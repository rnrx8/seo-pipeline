"""Analyze the article outline to determine service/CTA placement.
For CV-purpose jobs, patches the outline artifact to ensure a dedicated service H2 exists.
"""
import json
import re as _re
import anthropic
from .ai import create_with_retry, get_step_config
from .db import get_artifact, get_job, get_service_by_id, get_cta_by_id, upsert_artifact

MODEL, MAX_TOKENS = get_step_config("service_map")

_CV_KEYWORDS = ("cv", "CV", "コンバージョン", "自社")
_COMPARISON_KEYWORDS = ("比較", "おすすめ", "ランキング", "一覧")
_LATE_KEYWORDS = ("向き不向き", "注意点", "後悔", "失敗", "まとめ", "FAQ", "セクション別ボリューム")

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
  2. サービス専用紹介セクションの末尾（あれば）
  3. まとめの直前のH2末尾
  （文脈に合わない場合は2箇所でも可）
"""


# ---------------------------------------------------------------------------
# Outline patching helpers
# ---------------------------------------------------------------------------

def _is_cv_purpose(article_purpose: str) -> bool:
    return bool(article_purpose) and any(kw in article_purpose for kw in _CV_KEYWORDS)


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

    for m in _re.finditer(r'^###\s+H2[-−]?\d*[：:]\s*(.+)$', outline_text, _re.MULTILINE):
        title = m.group(1).strip()
        if service_name not in title:
            continue
        if any(kw in title for kw in _COMPARISON_KEYWORDS):
            continue  # comparison/ranking section → not a dedicated section
        if any(kw in title for kw in tool_markers):
            continue  # service is used as a tool, not introduced
        # Dedicated: service name is the primary subject
        if any(kw in title for kw in about_markers) or title.startswith(service_name):
            return True
    return False


def _count_h2_sections(outline_text: str) -> int:
    return len(_re.findall(r'^###\s+H2[-−]?\d+[：:]', outline_text, _re.MULTILINE))


def _find_insertion_point(outline_text: str) -> int:
    """Find the character position to insert the new service H2 section.

    Strategy:
      1. After the comparison H2 section (most natural flow: compare → deep-dive)
      2. Before the first 向き不向き/注意点 section (if no comparison found)
      3. Before FAQ/ボリューム設計 (last resort)
    """
    # Collect all H2 section start positions
    h2_starts: list[tuple[int, str]] = []
    for m in _re.finditer(r'^###\s+H2[-−]?\d+[：:](.+)$', outline_text, _re.MULTILINE):
        h2_starts.append((m.start(), m.group(1).strip()))

    # 1. Find comparison H2 and return the start of the NEXT section
    for i, (pos, title) in enumerate(h2_starts):
        if any(kw in title for kw in _COMPARISON_KEYWORDS):
            if i + 1 < len(h2_starts):
                return h2_starts[i + 1][0]
            break  # comparison is last H2 → fall through to fallback

    # 2. Insert before first "late" H2 (向き不向き etc.)
    for pos, title in h2_starts:
        if any(kw in title for kw in _LATE_KEYWORDS):
            return pos

    # 3. Before FAQ / ボリューム設計 table
    fallback = _re.search(r'^### (FAQ|セクション別)', outline_text, _re.MULTILINE)
    return fallback.start() if fallback else len(outline_text)


def _build_service_h2_block(service: dict, h2_num: int) -> str:
    """Build the outline text for a dedicated service H2 section.

    Uses neutral language that works for both comparison articles and how-to articles.
    """
    name = service.get("name", "自社サービス")
    sps = service.get("selling_points") or []
    must_include = service.get("must_include", "")

    sp_h3s = "\n".join(f"- {name}の強み：{sp}" for sp in sps[:3]) if sps else f"- {name}の主な特徴・強み"
    must_note = f"\n  ※必ず含める内容：{must_include}" if must_include else ""

    return (
        f"### H2-{h2_num}：{name}の特徴・活用方法・始め方\n\n"
        f"**H2直下方針：**\n"
        f"- ①結論：{name}はこの記事のテーマに関して最もおすすめできるサービスである。\n"
        f"- ②配下H3の概要：サービスの概要・強み・向いている人・利用開始方法をH3で詳述する。\n"
        f"- ③不安への補完：「自分に合っているか」という読者の疑問に答え、具体的なメリットと対象者像を明示する。{must_note}\n\n"
        f"**H3：**\n"
        f"- {name}とは？サービス概要と主な特徴\n"
        f"{sp_h3s}\n"
        f"- {name}がおすすめな人・向いている人\n"
        f"- {name}の始め方・料金・利用の流れ\n\n"
        f"---\n\n"
    )


def _patch_volume_table(outline_text: str, h2_title: str) -> str:
    """Append a row for the service H2 to the volume design table."""
    # Match the table header and all existing data rows
    table_m = _re.search(
        r'(\| *H2タイトル[^\n]*\n\|[-| ]+\n(?:\|[^\n]+\n)*)',
        outline_text, _re.MULTILINE
    )
    if not table_m:
        return outline_text
    new_row = f"| {h2_title} | 5 | 2,000字 | CV直結・サービス専用紹介セクション（自動挿入） |\n"
    insert_at = table_m.end()
    return outline_text[:insert_at] + new_row + outline_text[insert_at:]


def _patch_outline_for_cv(
    job_id: str,
    outline_text: str,
    service: dict,
) -> tuple[str, str]:
    """Insert a dedicated service H2 into the outline if missing.

    Returns (patched_outline_text, inserted_h2_title).
    Saves the updated outline artifact immediately.
    """
    service_name = service.get("name", "自社サービス")
    h2_count = _count_h2_sections(outline_text)
    new_h2_num = h2_count + 1
    insert_pos = _find_insertion_point(outline_text)

    h2_block = _build_service_h2_block(service, new_h2_num)
    patched = outline_text[:insert_pos] + h2_block + outline_text[insert_pos:]

    # Build the title we'll reference in service_map (must match _build_service_h2_block)
    h2_title = f"{service_name}の特徴・活用方法・始め方"

    patched = _patch_volume_table(patched, h2_title)

    upsert_artifact(
        job_id=job_id,
        step="outline",
        content_type="text/markdown",
        content_text=patched,
        meta={"service_h2_patched": True, "service_name": service_name},
    )
    print(f"[service_map] Outline patched: H2-{new_h2_num} '{h2_title}' inserted at char {insert_pos}")
    return patched, h2_title


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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(job_id: str, keyword: str, api_key: str | None = None) -> dict:
    """Analyze outline and determine service/CTA placement.

    For CV-purpose jobs, patches the outline artifact to ensure a dedicated
    service H2 exists before step_article runs.
    Stores a service_map artifact with placement instructions.
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

    # --- CV purpose: ensure a dedicated service H2 exists in the outline ---
    patched_h2_title: str | None = None
    if service and _is_cv_purpose(article_purpose):
        service_name = service.get("name", "")
        if not _has_dedicated_service_h2(outline_text, service_name):
            print(f"[service_map] CV purpose detected but no dedicated H2 for '{service_name}' — patching outline")
            outline_text, patched_h2_title = _patch_outline_for_cv(job_id, outline_text, service)
        else:
            print(f"[service_map] CV purpose detected, dedicated H2 already present for '{service_name}'")

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
                    outline_text=outline_text,
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

    # Ensure service_map reflects the patched outline if we added a dedicated H2
    if patched_h2_title:
        service_map["service_section_type"] = "dedicated"
        if not service_map.get("primary_h2"):
            service_map["primary_h2"] = patched_h2_title
        per = service_map.setdefault("per_section_instructions", {})
        if patched_h2_title not in per:
            name = service.get("name", "このサービス") if service else "このサービス"
            per[patched_h2_title] = (
                f"{name}の特徴・強み・向いている人・料金をH3で詳しく解説すること。"
                f"各H3は200字以上、セクション全体で1,500〜2,500字を目安にする。"
            )
        # Ensure CTA is also placed after the dedicated H2
        cta_list = service_map.setdefault("cta_after_h2", [])
        if patched_h2_title not in cta_list:
            cta_list.append(patched_h2_title)

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
