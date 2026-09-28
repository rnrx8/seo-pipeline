"""Validate and repair protected structural requirements before article writing."""
from __future__ import annotations

import json
import re
from typing import Any

from .db import get_artifact, get_job, upsert_artifact
from .step_outline import ensure_complete_volume_design


_META_HEADINGS = {
    "記事構成案", "目標文字数", "タイトル案", "リード文の設計方針", "H1 / H2 / H3 構成",
    "セクション別ボリューム設計", "補足", "補足メモ",
}


def _strip_outline_prefix(title: str) -> str:
    return re.sub(r"^H2(?:[-−]?\d+)?\s*[.．:：｜|]\s*", "", title, flags=re.IGNORECASE).strip()


def extract_h2_titles(text: str, *, outline: bool) -> list[str]:
    titles: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^(#{2,4})\s+(.+?)\s*$", line)
        if not match:
            continue
        hashes, raw = match.groups()
        if outline:
            if not re.match(r"^H2(?:[-−]?\d+)?\s*[.．:：｜|]", raw, flags=re.IGNORECASE):
                continue
            title = _strip_outline_prefix(raw)
        else:
            if hashes != "##":
                continue
            title = raw.strip()
        if title and not any(title.startswith(meta) for meta in _META_HEADINGS):
            titles.append(title)
    return titles


def _extract_h2_sections(text: str, *, outline: bool) -> list[dict[str, Any]]:
    """Return article/outline H2 blocks with exact character ranges."""
    if outline:
        pattern = re.compile(
            r"^#{2,4}\s+H2(?:[-−]?\d+)?\s*[.．:：｜|]\s*(.+?)\s*$",
            re.MULTILINE | re.IGNORECASE,
        )
    else:
        pattern = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    sections: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        if outline:
            meta = re.search(
                r"^#{2,4}\s+(?:セクション別ボリューム設計|補足|補足メモ)\s*$",
                text[match.end():end],
                re.MULTILINE,
            )
            if meta:
                end = match.end() + meta.start()
        sections.append({
            "title": match.group(1).strip(),
            "start": match.start(),
            "heading_end": match.end(),
            "end": end,
            "body": text[match.end():end],
        })
    return sections


_SERVICE_ABOUT_PATTERN = re.compile(
    r"特徴|強み|おすすめ(?:な|の|する)?理由|おすすめ|選ば|料金|費用|無料|"
    r"始め方|登録|使い方|活用|向いて|評判|安全|紹介|徹底|とは|メリット"
)
_COMPARISON_MARKERS = ("比較", "ランキング", "一覧", "おすすめアプリ", "おすすめサービス")


def _service_focused_h2s(text: str, name: str, *, outline: bool) -> list[dict[str, Any]]:
    if not name:
        return []
    result = []
    for section in _extract_h2_sections(text, outline=outline):
        title = section["title"]
        if name not in title:
            continue
        if any(marker in title for marker in _COMPARISON_MARKERS):
            continue
        if _SERVICE_ABOUT_PATTERN.search(title) or title.startswith(name):
            result.append(section)
    return result


def _service_coverage(text: str, name: str, *, outline: bool) -> dict[str, Any]:
    """Measure meaningful service coverage without requiring a standalone H2."""
    focused_h2s = _service_focused_h2s(text, name, outline=outline)
    h3_pattern = re.compile(
        r"^#{3,5}\s+(?:H3(?:[-−]?\d+)?\s*[.．:：｜|]\s*)?(.+?)\s*$",
        re.MULTILINE | re.IGNORECASE,
    )
    service_h3s: list[dict[str, str]] = []
    contextual_sections: list[str] = []
    for section in _extract_h2_sections(text, outline=outline):
        section_text = section["body"]
        for match in h3_pattern.finditer(section_text):
            title = match.group(1).strip()
            if name in title and _SERVICE_ABOUT_PATTERN.search(title):
                service_h3s.append({"title": title, "parent_h2": section["title"]})

        prose_text = "\n".join(
            line for line in section_text.splitlines()
            if not line.lstrip().startswith("|")
        )
        if name not in prose_text:
            continue
        signal_groups = (
            ("特徴", "強み", "理由", "メリット", "選ば", "安全", "機能"),
            ("向いて", "おすすめな人", "相性", "目的", "選び"),
            ("料金", "費用", "無料", "始め方", "登録", "利用方法", "使い方"),
        )
        if sum(any(term in prose_text for term in group) for group in signal_groups) >= 2:
            contextual_sections.append(section["title"])

    return {
        "sufficient": bool(focused_h2s or service_h3s or contextual_sections),
        "service_h2s": [section["title"] for section in focused_h2s],
        "service_h3s": service_h3s,
        "contextual_h2s": contextual_sections,
    }


def _candidate_mentions(text: str, candidates: list[str]) -> list[str]:
    names = [name for name in candidates if name and name in text]
    # A comparison table is direct structural evidence even when an older or
    # malformed contract contains category labels instead of product names.
    in_named_comparison_table = False
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            in_named_comparison_table = False
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells:
            continue
        name = re.sub(r"[*_`\[\]]", "", cells[0]).strip()
        # Transposed comparison: | 項目 | Service A | Service B |.
        # Require a known service as an anchor so ordinary attribute tables do
        # not turn column labels such as price/features into product names.
        headers = [re.sub(r"[*_`\[\]]", "", cell).strip() for cell in cells[1:]]
        if name in ("項目", "比較項目") and any(h in candidates for h in headers):
            for header in headers:
                if (1 < len(header) <= 40 and header not in names
                        and header not in {"料金", "特徴", "機能", "安全性", "備考", "おすすめ", "比較結果", "安いのは"}):
                    names.append(header)
        if any(term in name for term in ("サービス名", "アプリ名")):
            in_named_comparison_table = True
            continue
        if not in_named_comparison_table:
            continue
        if not name or set(name) <= {"-", ":", " "}:
            continue
        if any(term in name for term in ("比較項目", "項目", "料金")):
            continue
        if 1 < len(name) <= 40 and name not in names:
            names.append(name)
    return names


def validate_structure(text: str, contract: dict[str, Any], *, outline: bool) -> list[dict[str, Any]]:
    h2s = extract_h2_titles(text, outline=outline)
    violations: list[dict[str, Any]] = []
    for section in contract.get("required_sections", []):
        key = section.get("key")
        if key == "named_service_comparison":
            comparison_h2 = any(any(term in h for term in ("比較", "おすすめ", "ランキング", "一覧")) for h in h2s)
            candidates = section.get("candidate_services") or []
            mentions = _candidate_mentions(text, candidates)
            minimum = int(section.get("minimum_named_items") or 2)
            if not comparison_h2 or len(mentions) < minimum:
                violations.append({
                    "key": key,
                    "reason": "具体的なサービス比較H2または比較対象数が不足",
                    "comparison_h2": comparison_h2,
                    "named_items": mentions,
                    "minimum_named_items": minimum,
                })
        elif key in ("featured_service_coverage", "featured_service_dedicated", "featured_service_integrated"):
            name = section.get("service_name") or contract.get("featured_service") or ""
            coverage = _service_coverage(text, name, outline=outline) if name else {"sufficient": False}
            if not coverage.get("sufficient"):
                violations.append({
                    "key": "featured_service_coverage",
                    "reason": f"{name or '指定サービス'}の価値・選定理由が既存章内で十分に扱われていない",
                    "service_name": name,
                    "preferred_placement": section.get("preferred_placement") or "within_relevant_section",
                })
            max_h2s = int(section.get("max_service_focused_h2s", 1))
            service_h2s = coverage.get("service_h2s") or []
            if len(service_h2s) > max_h2s:
                violations.append({
                    "key": "featured_service_duplicate_h2",
                    "reason": f"{name}を主題にしたH2が重複している",
                    "service_name": name,
                    "h2_titles": service_h2s,
                    "maximum": max_h2s,
                })
    return violations


def _comparison_block(keyword: str, section: dict[str, Any]) -> tuple[str, str, int]:
    candidates = (section.get("candidate_services") or [])[:6]
    title = f"{keyword}で使える具体的なサービスを比較"
    h3s = [
        "#### H3：料金・会員規模・安全機能の比較一覧表\n\n"
        "- セクション内容：候補を同じ比較軸で整理し、読者が目的に合うサービスを選べるようにする。\n"
        "- 表現形式：比較表\n"
        "- 掲載項目：サービス名、料金、会員規模、身バレ対策、安全機能、向いている人\n"
        "- 使用する根拠：ファクトシートの各サービス公式情報"
    ]
    for name in candidates:
        h3s.append(
            f"#### H3：{name}の特徴と向いている人\n\n"
            f"- セクション内容：{name}の確認済みの特徴を説明し、どのような人に適するかを示す。\n"
            "- 表現形式：文章\n"
            "- 掲載項目：なし\n"
            "- 使用する根拠：ファクトシート内の該当サービス情報"
        )
    block = (
        f"### H2：{title}\n\n"
        "**H2直下方針**：候補を具体名で比較し、条件別の選び分けを最初に結論として示す。\n\n"
        "- セクション内容：複数サービスを同じ軸で比較し、読者が一社を選べる判断材料を提供する。\n"
        "- 表現形式：比較表（候補の違いを同条件で比較するため）\n"
        "- 掲載項目：サービス名、料金、会員規模、安全性、主な特徴、向いている人\n"
        "- 使用する根拠：ファクトシート内の各サービス公式情報\n\n"
        + "\n\n".join(h3s) + "\n\n"
    )
    return title, block, 1200


def _comparison_details(section: dict[str, Any]) -> str:
    candidates = (section.get("candidate_services") or [])[:6]
    blocks = [
        "#### H3：具体的なサービスを同じ条件で比較\n\n"
        "- セクション内容：各サービスを同じ比較軸で整理し、読者が条件に合う候補を選べるようにする。\n"
        "- 表現形式：比較表\n"
        "- 掲載項目：サービス名、料金、会員規模、安全機能、向いている人\n"
        "- 使用する根拠：ファクトシート内の各サービス公式情報"
    ]
    for name in candidates:
        blocks.append(
            f"#### H3：{name}の特徴と向いている人\n\n"
            f"- セクション内容：{name}の確認済み情報を使って特徴と選定条件を示す。\n"
            "- 表現形式：文章\n- 掲載項目：なし\n"
            "- 使用する根拠：ファクトシート内の該当サービス情報"
        )
    return "\n\n".join(blocks) + "\n\n"


def _enrich_existing_comparison(outline_text: str, section: dict[str, Any]) -> str:
    """Add concrete comparison details inside the existing comparison H2."""
    h2_pattern = re.compile(r"^#{2,4}\s+H2(?:[-−]?\d+)?\s*[.．:：｜|]\s*(.+)$", re.MULTILINE)
    matches = list(h2_pattern.finditer(outline_text))
    for index, match in enumerate(matches):
        if not any(term in match.group(1) for term in ("比較", "おすすめ", "ランキング", "一覧")):
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(outline_text)
        return outline_text[:end].rstrip() + "\n\n" + _comparison_details(section) + outline_text[end:].lstrip()
    return outline_text


def _service_block(section: dict[str, Any]) -> tuple[str, str, int]:
    name = section.get("service_name") or "指定サービス"
    title = f"{name}の特徴・料金・始め方"
    block = (
        f"### H2：{title}\n\n"
        f"**H2直下方針**：{name}が有力候補になる理由を端的に示し、利用判断に必要な情報を整理する。\n\n"
        f"- セクション内容：{name}の特徴、向く人、料金、利用開始までの流れを説明する。\n"
        "- 表現形式：文章と箇条書き\n"
        "- 掲載項目：特徴、向いている人、料金、無料範囲、始め方\n"
        "- 使用する根拠：ファクトシートおよび登録済みサービス情報\n\n"
        f"#### H3：{name}の特徴と選ばれる理由\n\n"
        f"- セクション内容：{name}の確認済みの強みを具体的に説明する。\n"
        "- 表現形式：箇条書き\n- 掲載項目：主要機能、安全性、独自性\n"
        "- 使用する根拠：ファクトシート内の公式情報\n\n"
        f"#### H3：{name}がおすすめな人\n\n"
        "- セクション内容：利用目的と条件から向いている読者像を示す。\n"
        "- 表現形式：チェックリスト\n- 掲載項目：目的、予算、重視する機能\n"
        "- 使用する根拠：登録済みサービス情報\n\n"
        f"#### H3：{name}の料金と始め方\n\n"
        "- セクション内容：無料範囲、料金、登録から利用開始までの流れを整理する。\n"
        "- 表現形式：番号付きリスト\n- 掲載項目：無料範囲、料金、登録手順\n"
        "- 使用する根拠：ファクトシート内の公式情報\n\n"
    )
    return title, block, 1000


def _service_coverage_details(section: dict[str, Any]) -> str:
    name = section.get("service_name") or "指定サービス"
    return (
        f"#### H3：{name}がおすすめな理由と向いている人\n\n"
        f"- セクション内容：比較結果や記事テーマに沿って{name}の具体的な価値と選定理由を示し、"
        "どのような読者に向くかを説明する。料金や始め方は検索意図と登録情報に必要な場合だけ補足する。\n"
        "- 表現形式：文章または箇条書き\n"
        "- 掲載項目：主な特徴、他候補との違い、向いている人\n"
        "- 使用する根拠：ファクトシートおよび登録済みサービス情報\n\n"
    )


def _insert_in_outline_h2(outline_text: str, section: dict[str, Any], block: str) -> str:
    pos = section["end"]
    return outline_text[:pos].rstrip() + "\n\n" + block + outline_text[pos:].lstrip()


def _enrich_service_coverage(outline_text: str, section: dict[str, Any]) -> tuple[str, str | None]:
    """Integrate service coverage into an existing H2 whenever possible."""
    h2_sections = _extract_h2_sections(outline_text, outline=True)
    if not h2_sections:
        return outline_text, None

    comparison = next(
        (
            item for item in h2_sections
            if any(term in item["title"] for term in ("比較", "おすすめ", "ランキング", "一覧"))
        ),
        None,
    )
    if comparison:
        return _insert_in_outline_h2(outline_text, comparison, _service_coverage_details(section)), comparison["title"]

    late_terms = ("FAQ", "よくある質問", "まとめ", "注意", "トラブル", "セクション別")
    relevant_terms = ("選び", "使い", "方法", "基礎", "とは", "始め", "探し", "出会")
    candidates = [item for item in h2_sections if not any(term in item["title"] for term in late_terms)]
    target = next(
        (item for item in candidates if any(term in item["title"] for term in relevant_terms)),
        candidates[0] if candidates else None,
    )
    if not target:
        return outline_text, None
    return _insert_in_outline_h2(outline_text, target, _service_coverage_details(section)), target["title"]


def deduplicate_service_h2s(text: str, service_name: str, *, outline: bool) -> tuple[str, list[str]]:
    """Remove redundant service H2 blocks, preferring the naturally worded one."""
    sections = _service_focused_h2s(text, service_name, outline=outline)
    if len(sections) <= 1:
        return text, []

    def score(item: dict[str, Any]) -> tuple[int, int]:
        title = item["title"]
        generic = bool(re.search(r"特徴[・と].*(?:料金|活用方法).*(?:始め方|使い方)", title))
        natural_reason = bool(re.search(r"おすすめ(?:な|の|する)?理由|選ばれる理由", title))
        return ((4 if natural_reason else 0) - (5 if generic else 0), -int(item["start"]))

    keep = max(sections, key=score)
    removed = [item for item in sections if item is not keep]
    working = text
    for item in sorted(removed, key=lambda value: int(value["start"]), reverse=True):
        working = working[:item["start"]].rstrip() + "\n\n" + working[item["end"]:].lstrip()

    removed_titles = [item["title"] for item in removed]
    if outline:
        for title in removed_titles:
            row_pattern = re.compile(rf"^\|\s*{re.escape(title)}\s*\|[^\n]*\n?", re.MULTILINE)
            working = row_pattern.sub("", working)
    return working, removed_titles


def _insert_before_late_sections(outline_text: str, blocks: list[str]) -> str:
    pattern = re.compile(
        r"^(?:#{2,4})\s+(?:(?:H2(?:[-−]?\d+)?\s*[.．:：｜|]\s*)?)"
        r"(?:FAQ|よくある質問|まとめ|セクション別ボリューム設計)",
        re.MULTILINE,
    )
    match = pattern.search(outline_text)
    pos = match.start() if match else len(outline_text)
    prefix = outline_text[:pos].rstrip() + "\n\n"
    suffix = outline_text[pos:].lstrip()
    return prefix + "\n".join(blocks) + suffix


def _append_volume_rows(outline_text: str, rows: list[tuple[str, int]]) -> str:
    if not rows:
        return outline_text
    table = re.search(
        r"(\|\s*H2タイトル[^\n]*\n\|[-| :]+\n(?:\|[^\n]+\n)*)",
        outline_text,
        re.MULTILINE,
    )
    if not table:
        return outline_text
    addition = "".join(
        f"| {title} | 5 | {chars:,}字 | 検索意図・CVに直結する保護セクション |\n"
        for title, chars in rows
    )
    return outline_text[:table.end()] + addition + outline_text[table.end():]


def repair_outline(outline_text: str, contract: dict[str, Any], violations: list[dict[str, Any]]) -> tuple[str, list[str]]:
    required_by_key = {s.get("key"): s for s in contract.get("required_sections", [])}
    rows: list[tuple[str, int]] = []
    added: list[str] = []
    working = outline_text

    duplicate = next((v for v in violations if v.get("key") == "featured_service_duplicate_h2"), None)
    if duplicate:
        working, removed = deduplicate_service_h2s(
            working, duplicate.get("service_name") or "", outline=True
        )
        if removed:
            added.append("featured_service_duplicate_h2_removed")

    # Comparison repair runs first. Its generated H3s normally satisfy featured
    # service coverage as well, so a second service H2 must not be created.
    for violation in violations:
        key = violation.get("key")
        section = required_by_key.get(key, {})
        if key == "named_service_comparison" and violation.get("comparison_h2"):
            working = _enrich_existing_comparison(working, section)
            added.append("named_service_comparison_details")
            continue
        if key == "named_service_comparison":
            title, block, chars = _comparison_block(contract.get("keyword") or "対象テーマ", section)
            working = _insert_before_late_sections(working, [block])
            rows.append((title, chars))
            added.append(key)

    service_section = next(
        (
            section for section in contract.get("required_sections", [])
            if section.get("key") in (
                "featured_service_coverage", "featured_service_dedicated", "featured_service_integrated"
            )
        ),
        None,
    )
    if service_section:
        name = service_section.get("service_name") or contract.get("featured_service") or ""
        coverage = _service_coverage(working, name, outline=True) if name else {"sufficient": False}
        if not coverage.get("sufficient"):
            working, target = _enrich_service_coverage(working, service_section)
            if target:
                added.append("featured_service_coverage_integrated")
            else:
                title, block, chars = _service_block(service_section)
                working = _insert_before_late_sections(working, [block])
                rows.append((title, chars))
                added.append("featured_service_coverage_fallback_h2")

    return _append_volume_rows(working, rows), added


def run(job_id: str, keyword: str, api_key: str | None = None) -> dict:
    del keyword, api_key
    outline = get_artifact(job_id, "outline")
    contract = json.loads(get_artifact(job_id, "content_contract")["content_text"])
    violations = validate_structure(outline["content_text"], contract, outline=True)
    added: list[str] = []
    final_text = outline["content_text"]
    if violations:
        final_text, added = repair_outline(final_text, contract, violations)
    word_count_setting = get_job(job_id).get("word_count_setting")
    final_text, volume_repaired = ensure_complete_volume_design(final_text, word_count_setting)
    if violations or volume_repaired:
        remaining = validate_structure(final_text, contract, outline=True)
        if remaining:
            raise ValueError(f"Outline structure contract could not be satisfied: {remaining}")
        upsert_artifact(
            job_id=job_id,
            step="outline",
            content_type="text/markdown",
            content_text=final_text,
            meta={
                **(outline.get("meta") or {}),
                "structure_guard_added": added,
                "volume_design_repaired_after_guard": volume_repaired,
            },
        )
        print(f"[structure_guard] Added missing protected sections: {added}")
    else:
        print("[structure_guard] Outline satisfies the content contract")

    report = {
        "stage": "outline",
        "valid": True,
        "initial_violations": violations,
        "added_sections": added,
        "final_h2s": extract_h2_titles(final_text, outline=True),
    }
    return upsert_artifact(
        job_id=job_id,
        step="structure_validation",
        content_type="application/json",
        content_text=json.dumps(report, ensure_ascii=False),
        meta={"stage": "outline", "valid": True, "added_count": len(added)},
    )
