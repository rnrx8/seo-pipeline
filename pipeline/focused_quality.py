"""Independent audits of one immutable article, with per-role checkpoints."""
import json
from datetime import datetime, timezone
from .ai import create_with_retry, get_step_config
from .content_edits import content_blocks
from .readability import READABILITY_POLICY, readability_issues
from .quality_context import review_requirements, compact_enabled, review_facts

ROLES = {
    'evidence': ('evidence_support', 'comparison_conditions', 'metric_scope', 'unsupported_guarantees'),
    'coverage': ('coverage', 'unfinished_content'),
    'language': ('prose_quality',),
    'consistency': ('conclusion_consistency', 'redundancy'),
    'organization': ('prose_quality',),
}
ROLE_INSTRUCTIONS = {
    'evidence': '原文との事実照合専任。具体的主張・比較条件・数値の対象/期間・安全保証を検査する。本文の表だけで根拠確認を済ませない。',
    'coverage': '重要論点と記事目的の達成専任。requirements、content_contract、調査計画と本文を照合し、各重要な疑問に実質的な回答があるか検査。CV目的の推奨商材がある場合、順位だけ・機能列挙だけ・他社と同列の結論だけで終わらず、検索意図に合う読者のメリットと第一候補にする理由があるか確認。自社の有利な比較軸は許可するが、利用判断に重要な制限を隠すのは不可。構成自体の漏れにも注意。未調査を非公表として通さない。',
    'language': '文章の自然さ専任。全段落を前後の文脈と読んで主述・指示語・不自然な語の組合せ・文の途切れ・文体/表記を検査。記事全体の重複や表示形式は他工程の担当。好みの言い換えや感情の原文例を欠陥と扱わない。',
    'consistency': '記事全体の整合性専任。冒頭・各章・比較表・結論の条件と説明の矛盾、未解決の参照、複数文の重複を全文で検査。局所の言い回しと表/リスト形式は他工程の担当。必要条件の再掲や短い要約は重複欠陥ではない。',
    'organization': '情報整理と読みやすさ専任。個別の商品・サービス紹介パートにそれぞれ主要情報の表があるか確認（全体比較表や箇条書きだけでは代替不可、単なる言及・FAQは対象外）。citation_styleに反する出典表示や、内部の調査判断を説明する不要な注釈を確認。比較・並列・手順に適した形式、長文を詰め込んだ表/リスト、見出し直下の回答の順序、表/リストの導入、共感表現の位置を確認。誤字や全体の結論矛盾は他工程の担当。画面の実表示を確認したと主張しない。' + READABILITY_POLICY,
}


def parse_focus(raw, keys, text):
    from .content_quality import ContentQualityError
    try:
        checks = json.loads(raw)['checks']
        if len(checks) != len(keys) or {c['key'] for c in checks} != set(keys): raise ValueError('missing checks')
        valid_ids = {b['id'] for b in content_blocks(text)}
        for c in checks:
            if c['status'] not in ('pass','fail') or not isinstance(c['reason'],str) or not c['reason'].strip(): raise ValueError('verdict')
            locs = c['affected_blocks']
            if not isinstance(locs,list) or (c['status']=='fail' and not locs) or (c['status']=='pass' and locs): raise ValueError('locations')
            if any(l.get('id') not in valid_ids or not isinstance(l.get('reason'),str) or not l['reason'].strip() for l in locs): raise ValueError('location')
        return checks
    except (ValueError,KeyError,TypeError,AttributeError) as exc:
        raise ContentQualityError('分割品質検査の判定・対象段落が不正です。') from exc


def audit_article(client, *, text, facts, outline, contract, requirements, sources, checkpoint=None):
    from .content_quality import (AUDIT_SYSTEM, EDITORIAL_SYSTEM, CHECKS, POLICY_VERSION,
                                  snapshot, response_text, audit_output_config, create_with_retry)
    model, budget = get_step_config('content_audit')
    fingerprint = snapshot(text,facts,outline,contract,requirements,sources)
    from .quality_context import review_reference_date
    reference_date = review_reference_date("article", fingerprint)
    phases = {}
    for role, keys in ROLES.items():
        # Source-heavy context is restricted to the roles that actually need it.
        payload = {'current_date':reference_date, 'article_blocks':content_blocks(text), 'requirements':review_requirements(requirements,role)}
        if role in ('evidence','coverage'):
            payload.update(confirmed_facts=review_facts(facts,requirements), outline=outline, content_contract=contract)
        if role == 'evidence': payload['source_documents'] = sources
        system = (AUDIT_SYSTEM if role in ('evidence','coverage') else EDITORIAL_SYSTEM)
        system += ('\n【今回の担当範囲：上記の全項目出力指定より優先】\n' + ROLE_INSTRUCTIONS[role]
                   + '\n今回返すchecksは次のキーだけ、各1件：' + ', '.join(keys)
                   + '\nstatusはpass/failのみ。各checkにaffected_blocksを必ず付ける。'
                   'failは問題箇所のidとreasonを全て記す。欠落の指摘は補うべき既存段落を指定。passは空配列。'
                   '指摘は修正可能な具体的欠陥に限り、全文を書き直さずJSONだけ返す。')
        print('[quality] Checking ' + role, flush=True)
        if role=='coverage' and compact_enabled():
            from .review_scope import ROUTING_POLICY
            system+=ROUTING_POLICY+'\n今回は網羅性担当。requires_source_checkはfalse。補う情報のresearch_idsを指定する。'
        request = dict(model=model, max_tokens=budget, system=system,
                       output_config=audit_output_config(keys, locations=True,source_routing=role=='coverage' and compact_enabled()),
                       messages=[{'role':'user','content':json.dumps(payload,ensure_ascii=False)}])
        from .ai import tiered_review_enabled
        trace=[]
        phase_model=model
        if role=='evidence' and compact_enabled():
            from .tiered_evidence import evidence_audit
            from .review_scope import ROUTING_POLICY, validate_routes, pages_from, source_resolutions
            from .tiered_research import checked_request
            from .quality_budget import JOB
            from types import SimpleNamespace
            from .content_quality import digest
            phase_model='gpt-6-luna'
            brief={k:v for k,v in payload.items() if k not in ('source_documents','outline')}
            brief['source_revision']=digest(sources)
            brief['requirements']['source_resolutions']=source_resolutions('article',content_blocks(text),sources,brief['requirements'])
            request.update(model='gpt-6-luna',system=system+ROUTING_POLICY,
                output_config=audit_output_config(keys,locations=True,source_routing=True),
                messages=[{'role':'user','content':json.dumps(brief,ensure_ascii=False)}])
            value,usage=checked_request(JOB.get(),'tiered_article_evidence_scope',request)
            raw=json.dumps(value,ensure_ascii=False)
            checks=parse_focus(raw,keys,text)
            validate_routes(checks,content_blocks(text),payload['requirements'],pages_from(sources),keys)
            if any(c['requires_source_check'] for c in checks):
                confirmed,extra,trace=evidence_audit('article',{**payload,'candidate_checks':checks},keys,text)
                replacements={c['key']:c for c in confirmed}
                checks=[replacements[c['key']] if c['requires_source_check'] else c for c in checks]
                usage=SimpleNamespace(input_tokens=usage.input_tokens+extra.input_tokens,output_tokens=usage.output_tokens+extra.output_tokens)
                raw=json.dumps({'checks':checks},ensure_ascii=False)
        elif tiered_review_enabled():
            from .quality_budget import JOB
            from .tiered_research import checked_request
            value, usage = checked_request(JOB.get(), 'tiered_article_'+role, request)
            raw = json.dumps(value,ensure_ascii=False)
        else:
            msg = create_with_retry(client, **request)
            raw, usage = response_text(msg), msg.usage
        if not trace:checks = parse_focus(raw, keys, text)
        if role=='coverage' and compact_enabled():
            from .review_scope import validate_routes, pages_from
            validate_routes(checks,content_blocks(text),payload['requirements'],pages_from(sources),())
        phase = {'role':role,'checks':checks,'snapshot':fingerprint,'model':phase_model if not trace else trace[-1]['model'],
                 'input_tokens':usage.input_tokens,'output_tokens':usage.output_tokens}
        if trace:phase['evidence_routing']=trace
        phases[role] = phase
        if checkpoint: checkpoint(role, phase)
    combined=[]
    for key in CHECKS:
        results=[c for phase in phases.values() for c in phase['checks'] if c['key']==key]
        failed=[c for c in results if c['status']=='fail']
        combined.append({'key':key,'status':'fail' if failed else 'pass',
                         'reason':'\n'.join(c['reason'] for c in failed or results),
                         'affected_blocks':list({v['id']:v for c in failed for v in c['affected_blocks']}.values()),
                         'research_ids':sorted({i for c in failed for i in c.get('research_ids',[])}),
                         'source_urls':sorted({u for c in failed for u in c.get('source_urls',[])})})
    from .price_comparison import comparison_evidence
    from .claim_scope import scope_issues
    from .content_quality import explicit_risk_guarantees
    prices, price_issues = comparison_evidence(text, contract)
    for key, findings in [('comparison_conditions', price_issues + scope_issues(text, facts)),
                          ('unsupported_guarantees', [{'claim':c} for c in explicit_risk_guarantees(text)])]:
        if findings:
            check=next(c for c in combined if c['key']==key)
            check.update(status='fail', reason=check['reason']+'\n'+json.dumps(findings,ensure_ascii=False))
            check['affected_blocks'] += [{'id':b['id'],'reason':str(f)} for f in findings for b in content_blocks(text)
                                         if f.get('claim') and f['claim'] in b['text']]
    mechanical=readability_issues(text)
    return {'checks':combined,'valid':all(c['status']=='pass' for c in combined) and not mechanical,
            'evidence_pending':any(p.get('evidence_routing') and p['evidence_routing'][-1]['review']['needed'] for p in phases.values()),
            'phases':phases,'readability_issues':mechanical,'stage':'article','snapshot':fingerprint,
            'policy_version':POLICY_VERSION,'model':model,
            'input_tokens':sum(p['input_tokens'] for p in phases.values()),
            'output_tokens':sum(p['output_tokens'] for p in phases.values())}
