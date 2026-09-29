"""Do not start writing until the outline can be supported by available evidence."""
import json

import anthropic

from .content_quality import ContentQualityError, audit, confirmed_facts, requirements_for, source_evidence
from .db import get_artifact, get_job, upsert_artifact


def run(job_id: str, keyword: str, api_key: str | None = None) -> dict:
    from . import step_fact_sheet, step_content_contract, step_outline, step_structure_guard
    client = anthropic.Anthropic(api_key=api_key)
    upsert_artifact(job_id=job_id, step='research_validation', content_type='application/json',
                    content_text=json.dumps({'valid': False, 'status': 'running'}), meta={'valid': False})
    for attempt in range(2):
        job = get_job(job_id)
        outline = get_artifact(job_id, 'outline')['content_text']
        facts = confirmed_facts(get_artifact(job_id, 'fact_sheet')['content_text'])
        contract = json.loads(get_artifact(job_id, 'content_contract')['content_text'])
        sources = source_evidence(get_artifact(job_id, 'fresh_sources'))
        try:
            report = audit(client, stage='research', text=outline, facts=facts, outline=outline,
                           contract=contract, requirements=requirements_for(job, keyword), sources=sources)
        except ContentQualityError as exc:
            upsert_artifact(job_id=job_id, step='research_validation', content_type='application/json',
                            content_text=json.dumps({'valid': False, 'error': str(exc)}), meta={'valid': False})
            raise
        issues = step_structure_guard.validate_structure(outline, contract, outline=True)
        report['structural_issues'] = issues
        if issues:
            report['valid'] = False
            check = next(c for c in report['checks'] if c['key'] == 'coverage')
            check.update(status='fail', reason=check['reason'] + '\n構成の必須項目: ' + json.dumps(issues, ensure_ascii=False))
        report['attempt'] = attempt + 1
        artifact = upsert_artifact(job_id=job_id, step='research_validation', content_type='application/json',
                                  content_text=json.dumps(report, ensure_ascii=False), meta={'valid': report['valid']})
        upsert_artifact(job_id=job_id, step=f'research_validation_{attempt + 1}', content_type='application/json',
                        content_text=json.dumps(report, ensure_ascii=False), meta={'valid': report['valid']})
        if report['valid']:
            return artifact
        if attempt == 0:
            gaps = json.dumps([c for c in report['checks'] if c['status'] == 'fail'], ensure_ascii=False)
            print('[research_guard] Missing evidence or invalid comparison; researching and rebuilding once')
            # Re-run retrieval, not a prose-only rewrite that could promote guesses.
            step_fact_sheet.run(job_id, keyword, api_key=api_key, research_gaps=gaps)
            step_content_contract.run(job_id, keyword, api_key=api_key)
            step_outline.run(job_id, keyword, api_key=api_key, research_gaps=gaps)
            step_structure_guard.run_before_research(job_id, keyword, api_key=api_key)
    raise ContentQualityError('追加調査後も必要情報・比較条件が未充足です。research_validationを確認してください。')
