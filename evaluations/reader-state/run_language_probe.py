"""One authorized language-role probe; immutable original article, no repairs."""
import copy
import json
import os
import sys
from pathlib import Path


def main(root):
    root = Path(root).resolve()
    source = root.parent / 'hybrid_full_20261007'
    manifest = json.loads((root / 'authorization.json').read_text())
    assert manifest['cap_jpy'] == 30 and manifest['user_message'] == 'はい'
    assert manifest['source_job_id'] == '7b3faf7b-0675-44a3-b023-d94e2381db78'
    with (root / 'started.json').open('x') as f:
        json.dump({'pid': os.getpid()}, f)
    os.environ.update(ARTICLE_REVIEW_PROVIDER='tiered', QUALITY_RESEARCH_ROUTING='focused',
                      QUALITY_TOTAL_LIMIT_USD='.15', QUALITY_BUDGET_DIR=str(root / 'ledger'))
    os.environ.pop('QUALITY_COMPLETION_EVAL', None)
    settings = json.loads((source / 'settings.json').read_text())
    job = {**settings['job'], 'id': manifest['probe_id']}
    out = root / 'artifacts'
    out.mkdir(exist_ok=True)
    from pipeline import db
    def forbidden(*args, **kwargs):
        raise AssertionError('Production DB forbidden')
    def get(job_id, step):
        assert job_id == job['id']
        path = out / (step + '.json')
        if not path.exists(): path = source / 'artifacts' / (step + '.json')
        if not path.exists(): raise ValueError('Artifact missing: ' + step)
        return json.loads(path.read_text())
    def optional(job_id, step):
        # Probe requests/checkpoints must not inherit older API results.
        if step.startswith(('tiered_', 'quality_')):
            path = out / (step + '.json')
            return json.loads(path.read_text()) if path.exists() else None
        try: return get(job_id, step)
        except ValueError: return None
    def save(**value):
        assert value['job_id'] == job['id']
        value = {'meta': {}, **value}
        (out / (value['step'] + '.json')).write_text(json.dumps(value, ensure_ascii=False))
        return copy.deepcopy(value)
    db._base = db._headers = forbidden
    db.get_artifact, db.get_optional_artifact, db.upsert_artifact = get, optional, save
    db.get_learned_style_rules = lambda _: copy.deepcopy(settings['learned_rules'])
    from pipeline import quality_budget as budget
    from pipeline import focused_quality as focused
    from pipeline.content_quality import final_review_requirements
    from pipeline import tiered_research
    budget.LIMIT = .15
    budget.REVIEW_PHASE_LIMITS = {'outline': .15, 'article': .15}
    normal = tiered_research.checked_request
    sent = 0
    def once(job_id, step, request, **kwargs):
        nonlocal sent
        assert sent == 0 and step == 'tiered_article_language'
        sent += 1
        (root / 'request.json').write_text(json.dumps(request, ensure_ascii=False))
        return normal(job_id, step, request, **kwargs)
    tiered_research.checked_request = once
    # Use the normal request builder/parser, stopping before any combined verdict.
    focused.ROLES = {'language': focused.ROLES['language']}
    class Done(Exception): pass
    def checkpoint(role, phase):
        assert role == 'language'
        (root / 'result.json').write_text(json.dumps(phase, ensure_ascii=False, indent=2))
        raise Done()
    state = {'status': 'running', 'whole_article_accepted': False}
    (root / 'status.json').write_text(json.dumps(state))
    try:
        with budget.scope(job['id'], 'content_audit'):
            with budget.ledger() as value:
                assert value['calls'] == []
                value.update(total_limit_usd=.15, authorization=manifest)
            focused.audit_article(None, text=get(job['id'], 'article_draft')['content_text'],
                facts='', outline='', contract={}, sources='',
                requirements=final_review_requirements(job, 'マッチングアプリ 40代 既婚者'),
                checkpoint=checkpoint)
    except Done:
        state['status'] = 'complete'
    except Exception as exc:
        state.update(status='stopped', error_type=type(exc).__name__, error=str(exc))
    finally:
        with budget.scope(job['id']), budget.ledger() as value:
            state.update(requests=len(value['calls']),
                cost_plus_reserved_jpy=sum(c.get('cost_usd',c['reserved_usd'])*200 for c in value['calls']),
                unsettled=sum(c['status']!='accounted' for c in value['calls']))
        (root / 'status.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == '__main__': main(sys.argv[1])
