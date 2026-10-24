"""Independent audits of one immutable article, with per-role checkpoints."""
import json
from datetime import datetime, timezone
from .ai import create_with_retry, get_step_config
from .reader_presentation import RECOMMENDATION_REVIEW
from .content_edits import content_blocks
from .readability import READABILITY_POLICY, readability_issues, long_sentence_candidates, SENTENCE_REVIEW_LIMIT
from .quality_context import review_requirements, compact_enabled, review_facts, editorial_rules_for_role

ROLES = {
    'evidence': ('evidence_support', 'comparison_conditions', 'metric_scope'),
    'coverage': ('coverage', 'unfinished_content'),
    'language': ('prose_quality',),
    'consistency': ('conclusion_consistency', 'redundancy', 'unsupported_guarantees'),
    'organization': ('prose_quality',),
}
ROLE_INSTRUCTIONS = {
    'evidence': '原文との事実照合専任。具体的主張・比較条件・数値の対象/期間を原文と照合する。保証表現かどうかの文脈判断は整合性担当へ渡す。本文の表だけで根拠確認を済ませない。',
    'coverage': '重要論点と記事目的の達成専任。requirements、content_contract、調査計画と本文を照合し、各重要な疑問への実質的な回答を確認する。共通提示基準「検索語・結論・推奨理由」の検索語・推薦理由・検索意図への接続を担当し、構成自体の漏れも確認する。未調査を非公表として通さない。',
    'language': '文章の自然さ専任。全段落を前後の文脈と読んで主述・指示語・不自然な語の組合せ・文の途切れ・文体/表記を検査。共通提示基準「読者と同じ目線の自然な解説」も担当する。感情の章だけでなく手順・比較・注意点にも、読者が実際にすることや自分にとっての意味が普段の言葉で伝わるかを確認する。抽象的な行為名や書き手側の評価だけで伝わりにくい場合は、その箇所と失われた意味を具体的に指摘する。専門用語の存在だけで不合格にせず、正確で自然に伝わる文まで口語へ直さない。判定のreasonには実際の短い記述と、読者への説明として伝わる理由または足りない意味を示す。lead_block_idsはリードの所在であり、欠陥の候補ではない。その範囲を一続きに読み、誰が何を誰にするか、誰の状態を誰が知るかが実際の文から定まるか確認する。H1・後続本文・資料から意図を補って読めたことにしない。自然に一意に読める主語省略は許容し、人物の対応が変わる複数の読みが成立する場合は該当語句と各読みを示す。記事全体の重複や表示形式は他工程の担当。明示された共通文体を確認したうえで、同じ意味が自然に伝わる言い方の好みや感情の原文例を欠陥と扱わない。',
    'consistency': '記事全体の整合性専任。対策・手順の提案と結果の保証を本文の文脈で区別し、共通のunsupported_guarantees基準に従って表現の強さを判断する。冒頭・各章・比較表・結論の条件と説明の矛盾、未解決の参照、複数文の重複を全文で検査。使いたい機能の利用条件と、契約・設定・手順を勧める時点も照合し、先に必要な機能がある読者へ一律の後回しを勧めていないか確認する。共通提示基準「検索語・結論・推奨理由」に定める章間の役割分担と反復を担当する。局所の言い回しと表/リスト形式は他工程の担当。必要条件の再掲や短い要約は重複欠陥ではない。',
    'organization': '情報整理と読みやすさ専任。共通提示基準「章の設計と引継ぎ」に従い、見出しの回答範囲・独立章の必要性・複数論点の分け方も評価する。section_plan（旧形式ではoutline）の問い・予定回答と実際の本文を照合し、設計にある説明を本文が書き切れていない場合はarticle、回答範囲・順序・階層そのものの修正が必要な場合はoutlineへ指摘する。文字数不足だけで構成の不備と決めず、既存の設計範囲で必要な説明を補えるか確認する。構成に存在することを適切さの根拠にせず、構成変更が必要なら本文の言い換えと区別して指摘する。個別の商品・サービス紹介パートにそれぞれ主要情報の表があるか確認（全体比較表や箇条書きだけでは代替不可、単なる言及・FAQは対象外）。citation_styleに反する出典表示や、内部の調査判断を説明する不要な注釈を確認。表後の定型注意点リストは、必要条件を該当セル・説明に統合して重複を減らす。利用判断に必要な例外や設定された出典表示は残す。比較・並列・手順に適した形式、長文を詰め込んだ表/リスト、共通提示基準「検索語・結論・推奨理由」に定める見出し直下の情報順序、表/リストの導入、共感表現の位置を確認。誤字や全体の結論矛盾は他工程の担当。画面の実表示を確認したと主張しない。' + READABILITY_POLICY,
}


from .heading_scope import POLICY as HEADING_SCOPE_POLICY
ROLE_INSTRUCTIONS['organization'] += '\n' + HEADING_SCOPE_POLICY


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


def merge_block_findings(checks):
    """Preserve every distinct reason when reviewers flag the same paragraph."""
    reasons = {}
    for check in checks:
        for finding in check['affected_blocks']:
            bucket = reasons.setdefault(finding['id'], [])
            if finding['reason'] not in bucket:
                bucket.append(finding['reason'])
    return [{'id': block_id, 'reason': '\n'.join(values)}
            for block_id, values in reasons.items()]


def audit_article(client, *, text, facts, outline, contract, requirements, sources, checkpoint=None):
    from .content_quality import (AUDIT_SYSTEM, EDITORIAL_SYSTEM, CHECKS, POLICY_VERSION,
                                  snapshot, response_text, audit_output_config, create_with_retry)
    model, budget = get_step_config('content_audit')
    fingerprint = snapshot(text,facts,outline,contract,requirements,sources)
    from .quality_context import review_reference_date
    reference_date = review_reference_date("article", fingerprint)
    phases = {}
    detailed = bool(requirements.get('article_design'))
    from . import editorial_findings as findings
    for role, keys in ROLES.items():
        # Source-heavy context is restricted to the roles that actually need it.
        payload = {'current_date':reference_date, 'article_blocks':content_blocks(text), 'requirements':review_requirements(requirements,role)}
        if 'editorial_rules' in payload['requirements']:
            payload['requirements']['editorial_rules'] = editorial_rules_for_role(payload['requirements']['editorial_rules'], role)
        if role == 'language':
            payload['long_sentence_candidates'] = long_sentence_candidates(text)
            from .lead_review import article_block_ids
            payload['lead_block_ids'] = article_block_ids(text)
        heading_groups = []
        if role == 'organization':
            sections = requirements.get('article_design', {}).get('sections', [])
            if sections:
                fields = ('id', 'parent_id', 'level', 'title', 'question', 'conclusion', 'answer_elements')
                payload['section_plan'] = [{k: s[k] for k in fields} for s in sections]
                from .heading_scope import groups
                heading_groups = groups(text, outline, requirements['article_design'])
                if heading_groups:
                    payload['heading_scope_groups'] = heading_groups
            else:
                payload['outline'] = outline
        if role in ('evidence','coverage'):
            payload.update(confirmed_facts=review_facts(facts,requirements), outline=outline, content_contract=contract)
        if role == 'evidence': payload['source_documents'] = sources
        system = (AUDIT_SYSTEM if role in ('evidence','coverage') else EDITORIAL_SYSTEM)
        if role == 'language':
            system += (f'\nlong_sentence_candidatesは既存の{SENTENCE_REVIEW_LIMIT}字超基準による実測候補であり、不合格判定ではない。'
                       '文脈と学習済み文体ルールを照合して判断する。候補以外の全段落も主述・修飾・指示語を確認する。')
        system += ('\n【今回の担当範囲：上記の全項目出力指定より優先】\n' + ROLE_INSTRUCTIONS[role]
                   + '\n今回返すchecksは次のキーだけ、各1件：' + ', '.join(keys)
                   + '\nstatusはpass/failのみ。各checkにaffected_blocksを必ず付ける。'
                   'failは問題箇所のidとreasonを全て記す。欠落の指摘は補うべき既存段落を指定。passは空配列。'
                   '指摘は修正可能な具体的欠陥に限り、全文を書き直さずJSONだけ返す。')
        print('[quality] Checking ' + role, flush=True)
        if role=='coverage' and compact_enabled():
            from .review_scope import ROUTING_POLICY
            system+=ROUTING_POLICY+'\n今回は網羅性担当。requires_source_checkはfalse。補う情報のresearch_idsを指定する。'
        if role == 'coverage':
            system += RECOMMENDATION_REVIEW
            system += ('\nrequirementsのintent_value_context、なければintent_analysisを読み、'
                       '共通提示基準「検索語・結論・推奨理由」に照らして本文の具体的な該当箇所を照合する。')
        output_config=audit_output_config(keys, locations=True,source_routing=role=='coverage' and compact_enabled())
        design=payload['requirements'].get('article_design') if role=='coverage' else None
        if design:
            from .article_design import correspondence,review_config,REVIEW_POLICY
            from .reader_decisions import REVIEW_POLICY as DECISION_REVIEW_POLICY
            payload['reader_correspondence']=correspondence(text,outline,design)
            system+='\n'+DECISION_REVIEW_POLICY+'\n'+REVIEW_POLICY
            output_config=review_config(output_config,design)
        if detailed and role != 'evidence':
            system += '\n' + findings.POLICY
            output_config = findings.output_config(output_config, keys)
        if heading_groups:
            from .heading_scope import output_config as scope_config
            output_config = scope_config(output_config, heading_groups)
        request = dict(model=model, max_tokens=budget, system=system,
                       output_config=output_config,
                       messages=[{'role':'user','content':json.dumps(payload,ensure_ascii=False)}])
        from .tiered_research import structured_review_output_limit
        request['max_tokens']=structured_review_output_limit(request)
        from .ai import tiered_review_enabled
        trace=[]
        phase_model=model
        if role=='evidence' and compact_enabled():
            from .tiered_evidence import evidence_audit
            from .review_scope import ROUTING_POLICY, validate_routes, pages_from, source_resolutions, source_excerpts
            from .tiered_research import checked_request
            from .quality_budget import JOB
            from types import SimpleNamespace
            from .content_quality import digest
            phase_model='gpt-6-luna'
            brief={k:v for k,v in payload.items() if k not in ('source_documents','outline')}
            brief['source_revision']=digest(sources)
            brief['requirements']['source_resolutions']=source_resolutions('article',content_blocks(text),sources,brief['requirements'])
            brief['source_excerpts']=source_excerpts('article',sources)
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
        editorial = findings.checked_findings(json.loads(raw), checks, text) if detailed and role != 'evidence' else None
        if design:
            from .article_design import checked_result
            decision_response=json.loads(raw)
            checks=checked_result(checks,decision_response,design,text,outline=outline)
            if editorial is not None:
                editorial += findings.decision_findings(decision_response, design)
                from .article_progression import checked as checked_progression,findings as progression_findings
                editorial += progression_findings(checked_progression(
                    decision_response,design,payload['reader_correspondence'],text))
        if heading_groups:
            from .heading_scope import checked as checked_scopes, merge as merge_scopes
            scope_findings = checked_scopes(json.loads(raw), heading_groups, text)
            checks = merge_scopes(checks, scope_findings)
            editorial += scope_findings
        phase = {'role':role,'checks':checks,'snapshot':fingerprint,'model':phase_model if not trace else trace[-1]['model'],
                 'input_tokens':usage.input_tokens,'output_tokens':usage.output_tokens}
        if design:
            phase['decision_reviews']=decision_response['decision_reviews']
            if 'reader_progression_reviews' in decision_response:
                phase['reader_progression_reviews']=decision_response['reader_progression_reviews']
            if 'lead_direction' in design:phase['lead_review']=decision_response['lead_review']
            phase['reader_correspondence']=payload['reader_correspondence']
        if editorial is not None:
            phase.update(editorial_findings=editorial, findings_version=findings.VERSION)
        if heading_groups:
            phase['heading_scope_reviews'] = json.loads(raw)['heading_scope_reviews']
        if trace:phase['evidence_routing']=trace
        phases[role] = phase
        if checkpoint: checkpoint(role, phase)
    combined=[]
    for key in CHECKS:
        results=[c for phase in phases.values() for c in phase['checks'] if c['key']==key]
        failed=[c for c in results if c['status']=='fail']
        combined.append({'key':key,'status':'fail' if failed else 'pass',
                         'reason':'\n'.join(c['reason'] for c in failed or results),
                         'affected_blocks':merge_block_findings(failed),
                         'research_ids':sorted({i for c in failed for i in c.get('research_ids',[])}),
                         'source_urls':sorted({u for c in failed for u in c.get('source_urls',[])}),
                         **({'correction_evidence':[r for c in failed for r in c.get('correction_evidence',[])]}
                            if any('correction_evidence' in c for c in failed) else {})})
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
            'editorial_findings':[{**f, 'role':role} for role, p in phases.items() for f in p.get('editorial_findings',[])],
            'phases':phases,'readability_issues':mechanical,'stage':'article','snapshot':fingerprint,
            'policy_version':POLICY_VERSION,'model':model,
            'input_tokens':sum(p['input_tokens'] for p in phases.values()),
            'output_tokens':sum(p['output_tokens'] for p in phases.values())}
