"""Isolated normal-stage evaluation; fixture storage only, never production DB.

Input preparation and authorization are external. A run is single-use, keeps all
receipts, and uses the normal model adapters, quality gates and budget limits.
"""
import copy
import json
import os
import sys
import traceback
from pathlib import Path


def main(root, resume_unstarted_audit=False):
    root = Path(root).resolve()
    manifest = json.loads((root / 'manifest.json').read_text())
    assert manifest['authorized_cap_jpy'] == 500
    os.environ.update(ARTICLE_WRITING_FLOW='sonnet_opus', ARTICLE_REVIEW_PROVIDER='tiered',
                      QUALITY_RESEARCH_ROUTING='focused', QUALITY_TOTAL_LIMIT_USD='2.5',
                      QUALITY_BUDGET_DIR=str(root / 'ledger'))
    os.environ.pop('QUALITY_COMPLETION_EVAL', None)
    settings = json.loads((root / 'settings.json').read_text())
    job = {**settings['job'], 'id': manifest['test_job_id']}
    artifacts = root / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    from pipeline import db

    def get(job_id, step):
        assert job_id == job['id']
        try:
            return json.loads((artifacts / (step + '.json')).read_text())
        except FileNotFoundError:
            # Match the production DB adapter's missing-row contract.
            raise ValueError(f'Artifact not found: {step}') from None

    def optional(job_id, step):
        assert job_id == job['id']
        if not (artifacts / (step + '.json')).exists():
            return None
        return get(job_id, step)

    def save(**value):
        assert value['job_id'] == job['id']
        value = {'id': 'isolated-' + value['step'], 'meta': {}, **value}
        dest = artifacts / (value['step'] + '.json')
        temp = dest.with_suffix('.tmp')
        temp.write_text(json.dumps(value, ensure_ascii=False))
        temp.replace(dest)
        return copy.deepcopy(value)

    def forbidden(*args, **kwargs):
        raise AssertionError('Production database access is forbidden in this evaluation')

    db._base = db._headers = forbidden
    db.get_artifact, db.get_optional_artifact, db.upsert_artifact = get, optional, save
    db.get_job = lambda job_id: copy.deepcopy(job) if job_id == job['id'] else forbidden()
    db.get_service_by_id = lambda _: copy.deepcopy(settings['service'])
    db.get_cta_by_id = lambda _: copy.deepcopy(settings['cta'])
    db.get_company_settings = lambda *args: copy.deepcopy(settings['companies'])
    db.get_learned_style_rules = lambda _: copy.deepcopy(settings['learned_rules'])
    from pipeline import ai
    normal_create = ai.create_with_retry

    def measured_create(client, *args, **kwargs):
        if kwargs.get('model', '').startswith('claude-'):
            import anthropic
            counter = client or anthropic.Anthropic(max_retries=0)
            fields = {k: kwargs[k] for k in ('model', 'system', 'messages', 'tools') if k in kwargs}
            tokens = counter.with_options(max_retries=0).messages.count_tokens(**fields, timeout=30).input_tokens
            with (root / 'input-counts.jsonl').open('a') as file:
                file.write(json.dumps({'model': kwargs['model'], 'input_tokens': tokens,
                                       'requested_max_tokens': kwargs['max_tokens']}) + '\n')
            print('INPUT_COUNT', kwargs['model'], tokens, flush=True)
        return normal_create(client, *args, **kwargs)

    ai.create_with_retry = measured_create
    from pipeline.step_plan import build_step_plan
    from pipeline.quality_budget import scope, ledger
    from pipeline.research_requirements import require_matrix
    from pipeline.outline_policy import require_current_outline
    from pipeline.ai import validate_model_credentials
    status = {'status': 'preflight', 'test_job_id': job['id'], 'completed_stages': []}
    if resume_unstarted_audit:
        previous = json.loads((root / 'status.json').read_text())
        assert previous['status'] == 'stopped' and previous['error_type'] == 'FileNotFoundError'
        assert previous['stage'] == 'content_audit'
        assert previous['completed_stages'] == ['research_validation', 'article', 'cta_inject']
        assert not (artifacts / 'content_audit.json').exists()
        status['completed_stages'] = previous['completed_stages']
        (root / 'storage-error-status.json').write_text(json.dumps(previous))

    def report():
        (root / 'status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2))

    marker = 'resume-unstarted-audit.json' if resume_unstarted_audit else 'started.json'
    with (root / marker).open('x') as file:
        json.dump({'pid': os.getpid(), 'job_id': job['id']}, file)
    try:
        validate_model_credentials(job)
        require_matrix(job['id'])
        require_current_outline(get(job['id'], 'outline'))
        with scope(job['id']):
            with ledger() as value:
                if resume_unstarted_audit:
                    assert value['total_limit_usd'] == 2.5
                    assert all(c['status'] == 'accounted' for c in value['calls'])
                    assert all(c['stage'] in ('research_validation', 'article') for c in value['calls'])
                else:
                    assert not value['calls']
                    value['total_limit_usd'] = 2.5
                    value['authorization'] = manifest['authorization']
        # Re-check edited frozen outline under the current policy. No research,
        # outline generation or service-map regeneration is part of this run.
        stages = build_step_plan(job)
        selected = [(key, fn) for key, fn in stages if key == 'research_validation']
        start = next(i for i, (key, _) in enumerate(stages) if key == 'article')
        selected += stages[start:]
        assert [key for key, _ in selected] == ['research_validation', 'article', 'cta_inject',
                                                'content_audit', 'final_structure_validation']
        if resume_unstarted_audit:
            selected = selected[3:]
        for key, fn in selected:
            status.update(status='running', stage=key)
            report()
            print('STAGE', key, flush=True)
            fn(job['id'], job['main_keyword'])
            status['completed_stages'].append(key)
            report()
        status.update(status='completed', stage=None)
    except Exception as exc:
        status.update(status='stopped', error_type=type(exc).__name__, error=str(exc))
        traceback.print_exc()
    finally:
        with scope(job['id']):
            with ledger() as value:
                status['cost_usd'] = sum(c.get('cost_usd', c['reserved_usd']) for c in value['calls'])
                status['cost_jpy_at_200'] = status['cost_usd'] * 200
                status['paid_calls'] = len(value['calls'])
                status['unsettled_calls'] = sum(c['status'] != 'accounted' for c in value['calls'])
        report()
        print(json.dumps(status, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main(sys.argv[1], resume_unstarted_audit='--resume-unstarted-audit' in sys.argv[2:])
