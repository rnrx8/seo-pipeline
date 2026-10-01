"""Post-article CTA injection: inserts CTA blocks at predetermined positions in the article."""
import json
import re as _re
from .db import get_artifact, get_job, get_cta_by_id, upsert_artifact


def _normalize_h2(s: str) -> str:
    """Strip punctuation and whitespace for fuzzy H2 matching."""
    s = _re.sub(r'[？?！!：:。、・〜～「」【】（）()\s]', '', s)
    return s.lower()


def _h2_title_matches(actual: str, target: str) -> bool:
    """Return True if actual and target H2 titles refer to the same section."""
    a, t = _normalize_h2(actual), _normalize_h2(target)
    if not a or not t:
        return False
    return a == t


def _format_cta_block(cta: dict) -> str:
    """Format CTA as a Markdown blockquote block."""
    body = cta.get("body", "")
    btn = cta.get("button_text", "")
    url = cta.get("url", "")

    lines = ["---"]
    if body:
        lines.append(f"> {body}")
        lines.append(">")
    if btn and url:
        lines.append(f"> **[{btn}]({url})**")
    elif btn:
        lines.append(f"> **{btn}**")
    lines.append("---")
    return "\n".join(lines)


def _cta_already_present(section_content: str, cta: dict) -> bool:
    """Return True if a CTA-like block is already in this section content."""
    btn = cta.get("button_text", "")
    url = cta.get("url", "")
    if btn and url:
        return f'[{btn}]({url})' in section_content
    if btn:
        return f'> **{btn}**' in section_content
    return bool(cta.get('body') and f"> {cta['body']}" in section_content)


def _insert_cta_after_h2s(article: str, target_h2s: list[str], cta_block: str, cta: dict) -> tuple[str, int]:
    """Insert CTA block at the end of each specified H2 section.

    Returns (modified_article, number_of_insertions).
    """
    from .content_quality import ContentQualityError
    lines = article.split('\n')
    headings = [line[3:].strip() for line in lines if line.startswith('## ')]
    # Resolve each target once. Shared words are not evidence of section identity.
    targets = list(dict.fromkeys(target_h2s))
    for target in targets:
        matches = [h for h in headings if _h2_title_matches(h, target)]
        if len(matches) != 1:
            raise ContentQualityError(f'CTAの挿入先を一意に確認できません: {target}')
    result: list[str] = []
    inserted = 0
    i = 0

    while i < len(lines):
        line = lines[i]
        result.append(line)

        # Detect start of an H2 section
        if line.startswith('## '):
            h2_text = line[3:].strip()
            is_target = any(_h2_title_matches(h2_text, t) for t in target_h2s)

            if is_target:
                # Collect this section's content until the next H2 or H1
                section_lines: list[str] = []
                j = i + 1
                while j < len(lines) and not (lines[j].startswith('# ') or lines[j].startswith('## ')):
                    section_lines.append(lines[j])
                    j += 1

                section_content = '\n'.join(section_lines)
                result.extend(section_lines)

                if not _cta_already_present(section_content, cta):
                    # Trim trailing blank lines before inserting
                    while result and result[-1].strip() == '':
                        result.pop()
                    result.append('')
                    result.append(cta_block)
                    result.append('')
                    inserted += 1
                    print(f"[cta_inject] Inserted CTA after H2: {h2_text!r}")
                else:
                    print(f"[cta_inject] CTA already present in section: {h2_text!r} — skipping")

                i = j
                continue

        i += 1

    return '\n'.join(result), inserted


def cta_placement_issues(text: str, plan: dict | None, section_map: dict | None = None) -> list[dict]:
    """Re-check the owned CTA after all model edits; source links do not count."""
    if not plan:
        return []
    button, url = plan.get('button_text', ''), plan.get('url', '')
    if not button or not url:
        return []
    pattern = r'\[' + _re.escape(button) + r'\]\(' + _re.escape(url) + r'\)'
    count = len(_re.findall(pattern, text))
    expected = plan['count']
    if count != expected:
        return [{'key': 'cta_count_mismatch', 'actual': count, 'expected': expected,
                 'reason': '指定箇所以外のCTAを削除し、指定された配置数を保つ',
                 'button_text': button, 'url': url}]
    if plan.get('section_ids'):
        from .article_quality import article_sections
        entries = (section_map or {}).get('entries', [])
        sections = article_sections(text)
        if len(entries) != len(sections):
            return [{'key': 'cta_section_identity_missing'}]
        placements = []
        parent_id = None
        for section, entry in zip(sections, entries):
            if section['level'] == 2:
                parent_id = entry['id']
            placements.extend([parent_id] * len(_re.findall(pattern, section['body'])))
        if sorted(placements, key=str) != sorted(plan['section_ids']):
            return [{'key': 'cta_wrong_section', 'reason': 'CTAは指定された章に1回ずつ配置する',
                     'expected_sections': plan['section_ids'], 'actual_sections': placements}]
    return []


def resolve_cta_targets(targets: list[str], outline: str, binding: dict | None) -> list[str]:
    """Use verified outline identities when a writer corrected a heading."""
    if not binding:
        return targets
    from .article_quality import outline_sections
    expected = outline_sections(outline)
    resolved = []
    for target in targets:
        indices = [i for i, section in enumerate(expected)
                   if section['level'] == 2 and _h2_title_matches(section['title'], target)]
        matches = [e['title'] for e in binding.get('entries', [])
                   if e['level'] == 2 and e.get('outline_index') in indices]
        resolved.append(matches[0] if len(matches) == 1 else target)
    return list(dict.fromkeys(resolved))


def run(job_id: str, keyword: str, api_key: str | None = None) -> dict:
    """Insert CTA blocks at positions specified in the service_map artifact."""
    print("[cta_inject] Injecting CTAs into article...")

    article_artifact = get_artifact(job_id, "article")
    article_text = article_artifact["content_text"]

    # Load CTA content
    cta: dict | None = None
    try:
        job = get_job(job_id)
        cta_id = job.get("cta_id")
        if cta_id:
            cta = get_cta_by_id(cta_id)
    except Exception as e:
        print(f"[cta_inject] Warning: could not load job/CTA: {e}")

    if not cta:
        print("[cta_inject] No CTA configured — skipping")
        return article_artifact

    if not (cta.get("body") or cta.get("button_text") or cta.get("url")):
        print("[cta_inject] CTA is empty — skipping instead of inserting an empty separator")
        return article_artifact

    # Load service_map for CTA positions
    target_h2s: list[str] = []
    try:
        sm = get_artifact(job_id, "service_map")
        service_map = json.loads(sm["content_text"])
        target_h2s = service_map.get("cta_after_h2") or []
        print(f"[cta_inject] CTA target H2s from service_map: {target_h2s}")
    except Exception as e:
        print(f"[cta_inject] Warning: no service_map found ({e}), using fallback positions")

    # Fallback: find comparison and summary H2s heuristically
    if not target_h2s:
        comparison_keywords = ['比較', 'おすすめ', 'ランキング', 'まとめ']
        h2_pattern = _re.compile(r'^## (.+)$', _re.MULTILINE)
        all_h2s = [m.group(1).strip() for m in h2_pattern.finditer(article_text)]
        for kw in comparison_keywords:
            matches = [h for h in all_h2s if kw in h]
            if matches:
                target_h2s.append(matches[0])
                break
        if all_h2s and (not target_h2s or all_h2s[-1] not in target_h2s):
            target_h2s.append(all_h2s[-1])
        print(f"[cta_inject] Fallback CTA targets: {target_h2s}")

    if not target_h2s:
        print("[cta_inject] No target H2s found — skipping")
        return article_artifact

    binding = (article_artifact.get('meta') or {}).get('section_map')
    if binding:
        from .section_identity import valid_binding
        from .content_quality import ContentQualityError
        outline = get_artifact(job_id, 'outline')['content_text']
        if not valid_binding(article_text, outline, binding):
            raise ContentQualityError('CTA配置に使う章の対応が古くなっています。')
        target_h2s = resolve_cta_targets(target_h2s, outline, binding)
    cta_block = _format_cta_block(cta)
    modified, n_inserted = _insert_cta_after_h2s(article_text, target_h2s, cta_block, cta)

    from .content_quality import ContentQualityError
    plan = {'button_text': cta.get('button_text', ''), 'url': cta.get('url', ''),
            'count': len(set(target_h2s)), 'targets': list(dict.fromkeys(target_h2s))}
    binding = (article_artifact.get('meta') or {}).get('section_map')
    if binding:
        plan['section_ids'] = [e['id'] for e in binding['entries'] if e['level'] == 2
                               and any(_h2_title_matches(e['title'], t) for t in target_h2s)]
    issues = cta_placement_issues(modified, plan, binding)
    if issues:
        raise ContentQualityError(f'CTAの配置数が指定と一致しません: {issues}')

    # Update article artifact
    artifact = upsert_artifact(
        job_id=job_id,
        step="article",
        content_type="text/markdown",
        content_text=modified,
        meta={**article_artifact.get("meta", {}), "cta_injected": n_inserted, "cta_placement": plan, "content_audited": False},
    )
    print(f"[cta_inject] Done: {n_inserted} CTA(s) inserted → artifact id={artifact['id']}")
    return artifact
