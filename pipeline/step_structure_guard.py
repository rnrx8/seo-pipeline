"""Validate and repair protected structural requirements before article writing."""
from __future__ import annotations

import json
import re
from typing import Any

from .db import get_artifact, get_job, get_service_by_id, upsert_artifact
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


def _candidate_mentions(text: str, candidates: list[str]) -> list[str]:
    if candidates:
        return [name for name in candidates if name and name in text]
    # Fallback when the fact sheet did not expose product names as headings:
    # count plausible first-column labels in Markdown comparison tables.
    names: list[str] = []
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells:
            continue
        name = re.sub(r"[*_`\[\]]", "", cells[0]).strip()
        if not name or set(name) <= {"-", ":", " "}:
            continue
        if any(term in name for term in ("サービス名", "アプリ名", "比較項目", "項目", "料金")):
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
        elif key == "featured_service_dedicated":
            name = section.get("service_name") or contract.get("featured_service") or ""
            about_terms = ("特徴", "料金", "始め方", "使い方", "おすすめな理由", "紹介", "徹底解説", "活用")
            found = bool(name) and any(name in h and any(term in h for term in about_terms) for h in h2s)
            if not found:
                violations.append({"key": key, "reason": f"{name or '指定サービス'}の専用H2がない"})
        elif key == "featured_service_integrated":
            name = section.get("service_name") or contract.get("featured_service") or ""
            if not name or name not in text:
                violations.append({"key": key, "reason": f"{name or '指定サービス'}が関連章に統合されていない"})
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
    blocks: list[str] = []
    rows: list[tuple[str, int]] = []
    added: list[str] = []
    working = outline_text
    for violation in violations:
        key = violation.get("key")
        section = required_by_key.get(key, {})
        if key == "named_service_comparison" and violation.get("comparison_h2"):
            working = _enrich_existing_comparison(working, section)
            added.append("named_service_comparison_details")
            continue
        if key == "named_service_comparison":
            title, block, chars = _comparison_block(contract.get("keyword") or "対象テーマ", section)
        elif key == "featured_service_dedicated":
            title, block, chars = _service_block(section)
        else:
            continue
        blocks.append(block)
        rows.append((title, chars))
        added.append(key)
    if not blocks:
        return working, added
    patched = _insert_before_late_sections(working, blocks)
    return _append_volume_rows(patched, rows), added


def run(job_id: str, keyword: str, api_key: str | None = None) -> dict:
    del keyword, api_key
    outline = get_artifact(job_id, "outline")
    contract = json.loads(get_artifact(job_id, "content_contract")["content_text"])
    violations = validate_structure(outline["content_text"], contract, outline=True)
    added: list[str] = []
    final_text = outline["content_text"]
    if violations:
        final_text, added = repair_outline(final_text, contract, violations)
        try:
            word_count_setting = get_job(job_id).get("word_count_setting")
        except Exception:
            word_count_setting = None
        final_text, volume_repaired = ensure_complete_volume_design(final_text, word_count_setting)
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
