"""One scoped length correction; preserve prior costs and unchanged proposals."""
import hashlib
import json
import os
from pathlib import Path
import sys

from reader_depth_followup import BACKEND, sha, write


def main():
    source = Path(sys.argv[1]).resolve()
    root = Path(sys.argv[2]).resolve()
    prior = json.loads((source / 'result.json').read_text())
    frozen = json.loads((source / 'manifest.json').read_text())
    if prior['status'] != 'completed' or prior['pending_calls'] or prior['lead_target_met']:
        raise RuntimeError('Only the completed, length-mismatched proposal can be corrected once')
    if any(hashlib.sha256((BACKEND / 'pipeline' / n).read_bytes()).hexdigest() != h
           for n, h in frozen['pipeline_sha256'].items()):
        raise RuntimeError('Pipeline policy changed since the scoped proof')
    cap = prior['combined_cap_usd'] - prior['combined_cost_with_holds_usd']
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'started.json').open('x') as f:
        json.dump({'source_result_sha256': sha(prior), 'cap_usd': cap}, f)
    os.environ.update(ARTICLE_REVIEW_PROVIDER='tiered', ARTICLE_WRITING_FLOW='sonnet_opus',
        QUALITY_RESEARCH_ROUTING='focused', QUALITY_BUDGET_MODE='bounded',
        QUALITY_BUDGET_DIR=str(root / 'ledger'), QUALITY_TOTAL_LIMIT_USD=str(cap))
    sys.path.insert(0, str(BACKEND))
    from pipeline import db, ai, quality_budget as qb, reader_needs as rn
    from pipeline import focused_quality as fq, article_design as ad, editorial_findings as ef
    from pipeline import tiered_research
    from stage6_replay import capture
    def forbidden(*a, **kw):
        raise RuntimeError('Production DB forbidden')
    db._base = db._headers = forbidden
    db.get_optional_artifact = tiered_research.get_optional_artifact = lambda *a: None
    tiered_research.upsert_artifact = lambda **kw: kw
    qb.LIMIT = cap
    qb.REVIEW_PHASE_LIMITS = {'outline': cap, 'article': cap}
    from dotenv import dotenv_values
    values = dotenv_values('/Users/ryoka/seo-pipeline/.env')
    for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY'):
        if not values.get(key):
            raise RuntimeError('Existing provider credential unavailable')
        os.environ[key] = values[key]
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'], max_retries=0)
    normal = ai.create_with_retry
    count = [0]
    def measured(*a, **kw):
        count[0] += 1
        name = f'{count[0]:04d}.json'
        write(root / 'requests' / name, kw)
        response = normal(*a, **kw)
        write(root / 'responses' / name, {'text': ai.message_text(response),
            'model': response.model, 'usage': response.usage.model_dump()
            if hasattr(response.usage, 'model_dump') else vars(response.usage)})
        return response
    ai.create_with_retry = tiered_research.create_with_retry = measured
    request = json.loads((source / 'requests/0002.json').read_text())
    data = json.loads(request['messages'][0]['content'])
    proposal = json.loads((source / 'proposal.json').read_text())
    data.update(scope='リードだけを1案生成。元の130字を、主題・望む関係・ためらい・方法を選ぶ意味・読む理由を保って170〜195字へ調整する。定義や全方法の列挙、強い危機感は足さない。望む関係の意味や、迷いが生じる理由を自然につなぐ。',
        previous_lead=proposal['lead'], previous_lead_chars=len(proposal['lead']),
        lead_target_chars=180, lead_min_chars=160, lead_max_chars=200)
    request.update(max_tokens=1024, messages=[{'role':'user', 'content':json.dumps(data, ensure_ascii=False)}],
        output_config={'format':{'type':'json_schema', 'schema':rn.obj({'lead':rn.STR})}})
    write(root / 'manifest.json', {'source':str(source), 'prior_cost_with_holds_usd':prior['combined_cost_with_holds_usd'],
        'combined_cap_usd':17.5, 'this_cap_usd':cap, 'pipeline_sha256':frozen['pipeline_sha256'],
        'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'source_proposal_sha256':sha(proposal), 'lead_min_chars':160, 'lead_max_chars':200,
        'full_article_regeneration':False, 'production_changes':False})
    result = {'status':'running'}
    try:
        with qb.scope('reader-depth-lead-finish', 'article'):
            response = measured(client, **request)
        proposal['lead'] = json.loads(ai.message_text(response))['lead']
        write(root / 'proposal.json', proposal)
        text = '# セカンドパートナーの探し方\n\n' + proposal['lead']
        need = {'id':'d1', 'role':'primary', 'scope':'リードのみ', 'depends_on':[], 'research_ids':['q1'],
            'question':'短い導入で対象と望み・ためらい、読む意味が伝わるか',
            'answer_requirements':['主題と対象を冒頭で示す', '160〜200字で不要な定義や列挙を省く',
                '求めるものとためらいが、方法を選ぶ意味へつながる']}
        check, design = capture(text, need, 'coverage', facts=json.dumps(data['confirmed_facts'], ensure_ascii=False),
            extra_requirements={'evaluation_scope':'リードだけを検査。本文・まとめの省略は欠陥ではない。',
                'custom_prompt':'約180字。主題と求める関係・ためらい・読む意味を保つ。'})
        with qb.scope('reader-depth-lead-finish', 'quality'):
            value, usage = tiered_research.checked_request('reader-depth-lead-finish', 'lead_finish_coverage', check)
        checks = fq.parse_focus(json.dumps({'checks':value['checks']}), fq.ROLES['coverage'], text)
        findings = ef.checked_findings(value, checks, text)
        checks = ad.checked_result(checks, value, design, text)
        findings += ef.decision_findings(value, design)
        result.update(status='completed', lead_chars=len(proposal['lead']),
            lead_target_met=160 <= len(proposal['lead']) <= 200,
            coverage_pass=all(c['status'] != 'fail' for c in checks), checks=checks,
            editorial_findings=findings, lead_review=value.get('lead_review'),
            other_excerpts_unchanged=all(proposal[k] == json.loads((source/'proposal.json').read_text())[k]
                for k in proposal if k != 'lead'))
    except Exception as exc:
        result.update(status='stopped', error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        calls = [c for p in (root/'ledger').glob('*.json') for c in json.loads(p.read_text())['calls']]
        cost = sum(c.get('cost_usd', c['reserved_usd']) for c in calls)
        result.update(new_cost_with_holds_usd=cost,
            combined_cost_with_holds_usd=prior['combined_cost_with_holds_usd'] + cost,
            combined_cap_usd=17.5, pending_calls=sum(c['status'] == 'pending' for c in calls),
            full_article_regenerated=False)
        write(root/'result.json', result)


if __name__ == '__main__':
    main()
