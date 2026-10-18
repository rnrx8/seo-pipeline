"""Cost-bounded partial revision, preserving unchanged article sections verbatim.

Reuses frozen evidence and deepened reader hypotheses. This is not a new
analysis/research/normal-generation E2E or a whole-article QA certificate.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace as NS

from reader_depth_followup import BACKEND, sha, write

TARGETS = ['s01', 's07', 's10', 's11']
NEW = 's18'
FEEDBACK = ('リードは元の275字の約3分の2、目安180字。主題と求める関係・ためらい・読む意味を簡潔につなぐ。'
    '手段比較と商材紹介は、心を許せる相手を求める理由と、守りたい日常・自分のペースを一段深く捉え、'
    '特徴の作用と読者が取れる具体策へつなぐ。危機を大きくするだけの深掘りはしない。'
    '既婚者専用マッチングと既婚者クラブを明確に推薦する方向は維持する。'
    '末尾は結論・理由・次の一歩を回収する短いまとめ。')


def split_sections(text, sections):
    matches = list(re.finditer(r'^(#{2,4}) (.+)$', text, re.M))
    if [(len(m[1]), m[2]) for m in matches] != [(s['level'], s['title']) for s in sections]:
        raise RuntimeError('Article headings differ from the frozen blueprint')
    result = {'lead': text[:matches[0].start()]}
    for i, (match, section) in enumerate(zip(matches, sections)):
        end = matches[i+1].start() if i+1 < len(matches) else len(text)
        result[section['id']] = text[match.start():end]
    return result


def main():
    sys.stdout.reconfigure(line_buffering=True)
    root, source, hypotheses = (Path(v).resolve() for v in sys.argv[1:4])
    stage = sys.argv[4]
    if stage not in ('outline', 'write', 'audit', 'repair', 'audit2'):
        raise RuntimeError('Unknown local evaluation stage')
    root.mkdir(parents=True, exist_ok=True)
    os.environ.update(ARTICLE_REVIEW_PROVIDER='tiered', ARTICLE_WRITING_FLOW='sonnet_opus',
        QUALITY_RESEARCH_ROUTING='focused', QUALITY_BUDGET_MODE='bounded',
        QUALITY_BUDGET_DIR=str(root/'ledger'), QUALITY_TOTAL_LIMIT_USD='7.5')
    sys.path.insert(0, str(BACKEND))
    from pipeline import db, ai, quality_budget as qb, editorial_blueprint as eb
    from pipeline import step_outline, step_article, content_quality as cq, focused_quality as fq
    from pipeline import article_design as ad, reader_needs as rn, tiered_research as tr
    def forbidden(*a, **kw):
        raise RuntimeError('Production DB access forbidden')
    db._base = db._headers = forbidden
    local = root/'local_artifacts'
    def get(job, step):
        path = local/(step+'.json')
        return json.loads(path.read_text()) if path.exists() else None
    def save(**kw):
        write(local/(kw['step']+'.json'), kw)
        return kw
    db.get_optional_artifact = tr.get_optional_artifact = get
    db.upsert_artifact = tr.upsert_artifact = save
    qb.LIMIT = 7.5
    qb.REVIEW_PHASE_LIMITS = {'outline':7.5, 'article':7.5}
    def artifact(name):
        return json.loads((source/'artifacts'/(name+'.json')).read_text())
    old = artifact('outline')
    blueprint = old['meta']['editorial_blueprint']
    context = old['meta']['blueprint_context']
    original = artifact('article')['content_text']
    pieces = split_sections(original, blueprint['sections'])
    needs_artifact = json.loads((hypotheses/'analysis_artifacts/reader_needs.json').read_text())
    needs = json.loads(needs_artifact['content_text'])
    # Analysis ids belong to another saved run. Preserve them with provenance;
    # never relabel i02/i04 into the original plan's i02 by string coincidence.
    alignment = {'i01':['i01'], 'i02':['i02','i04'], 'i03':['i02','i03']}
    frozen = {
        'scope':'Partial blueprint revision and shared-writer excerpts; merged preview with untouched sections. Scoped QA, not a full normal run.',
        'source':str(source), 'hypotheses_source':str(hypotheses), 'source_date':'2026-10-18',
        'cap_usd':7.5, 'planning_jpy_per_usd':200,
        'authorization':'はい。同じKWで進めたほうがいい？ その場合、部分検証で済むなら部分検証の方が費用的には助かるかも。',
        'targets':TARGETS, 'new_section':NEW, 'lead_target':'about 180 chars; no artificial mandatory lower bound',
        'source_article_sha256':sha(original), 'source_outline_sha256':sha(old),
        'hypotheses_sha256':sha(needs_artifact), 'stable_plan_to_new_hypotheses':alignment,
        'source_artifacts_sha256':{n:sha(artifact(n)) for n in ('fact_sheet','fresh_sources','research_matrix','research_plan')},
        'pipeline_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((BACKEND/'pipeline').glob('*.py'))},
        'production_changes':False, 'new_sources':False, 'full_generation':False,
    }
    if (root/'manifest.json').exists():
        if json.loads((root/'manifest.json').read_text()) != frozen:
            raise RuntimeError('Frozen inputs or policy changed')
    else:
        write(root/'manifest.json', frozen)
    calls = [c for p in (root/'ledger').glob('*.json') for c in json.loads(p.read_text())['calls']]
    if any(c['status']=='pending' for c in calls):
        raise RuntimeError('Pending model request; do not duplicate')
    from dotenv import dotenv_values
    values = dotenv_values('/Users/ryoka/seo-pipeline/.env')
    for key in ('OPENAI_API_KEY','ANTHROPIC_API_KEY'):
        if not values.get(key):
            raise RuntimeError('Existing provider credential unavailable')
        os.environ[key] = values[key]
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'], max_retries=0)
    normal = ai.create_with_retry
    def measured(*args, **kwargs):
        ident = sha(kwargs)
        request_path, response_path = root/'requests'/(ident+'.json'), root/'responses'/(ident+'.json')
        if response_path.exists():
            if json.loads(request_path.read_text()) != kwargs:
                raise RuntimeError('Saved request hash mismatch')
            value = json.loads(response_path.read_text())
            return NS(stop_reason=value['stop_reason'], model=value['model'], usage=NS(**value['usage']),
                content=[NS(type='text', text=value['text'])])
        write(request_path, kwargs)
        response = normal(*args, **kwargs)
        write(response_path, {'model':response.model, 'stop_reason':response.stop_reason,
            'usage':response.usage.model_dump() if hasattr(response.usage,'model_dump') else vars(response.usage),
            'text':ai.message_text(response)})
        return response
    ai.create_with_retry = tr.create_with_retry = measured
    job = 'reader-depth-partial'
    result = {'stage':stage, 'status':'running'}
    try:
        if stage == 'outline':
            request = dict(model=step_outline.MODEL, max_tokens=12000,
                system=step_outline.SYSTEM_PROMPT,
                output_config=eb.repair_config(allow_lead=True),
                messages=[{'role':'user','content':json.dumps({
                    'task':eb.POLICY+'\n既存編集設計の部分修正。対象以外の節・タイトル・省略判断・IDは維持。'+FEEDBACK,
                    'blueprint':blueprint, 'context':context,
                    'new_reader_hypotheses':needs['selected'], 'hypotheses_alignment':alignment,
                    'alignment_note':'左は元の計画の安定ID、右は追加分析の別名前空間のID。新たな正式計画の承認や根拠の追加ではなく、関連する心理仮説を編集設計へ反映する。',
                    'allowed_existing_targets':TARGETS, 'new_last_section_id':NEW,
                    'editing_scope':'既存4節とlead_directionを修正し、最後に短いまとめs18を追加。既存の見出し・階層・節の順序は維持。delete_ids=[]。section_orderは元の17節にs18を末尾追加した全順序。',
                    'chapter_scope':'H2は結論・短い理由・比較表を優先し、望む関係・迷い・特徴の作用・具体策の深い説明はs07/s11へ。s01の比較は手段の選択、s10は推薦概要、s11は特徴と利用価値。料金・手順・注意点の本文は再生成しない。まとめに新しい事実を持ち込まない。',
                    'lead_length':'元の275字の約3分の2、目安180字。定義・方法全列挙・詳細な本文予告を積まない。厳密な下限や心理段数は設けない。',
                }, ensure_ascii=False)}])
            with qb.scope(job,'outline'):
                response = measured(client, **request)
            edits = json.loads(ai.message_text(response))
            candidate = eb.apply_edits(blueprint, edits, context, structural=True, targets=TARGETS, allow_lead=True)
            if [s['id'] for s in candidate['sections']] != [s['id'] for s in blueprint['sections']] + [NEW]:
                raise RuntimeError('Only an appended closing section is permitted')
            for before in blueprint['sections']:
                after = next(s for s in candidate['sections'] if s['id']==before['id'])
                if before['id'] not in TARGETS and before != after:
                    raise RuntimeError('Untargeted blueprint section changed')
                if any(before[k] != after[k] for k in ('title','level','parent_id')):
                    raise RuntimeError('Existing headings changed')
            outline = {'content_text':eb.render(candidate,context), 'meta':eb.metadata(candidate,context)}
            eb.require_projection(outline,context)
            write(root/'outline.json',outline)
            design = eb.review_context(outline)
            payload = {'outline_blocks':cq.content_blocks(outline['content_text']), 'editorial_design':design,
                'blueprint':candidate, 'confirmed_context':context, 'new_reader_hypotheses':needs['selected'],
                'requirements':FEEDBACK, 'hypotheses_alignment':alignment}
            check = dict(model='gpt-6.1-sol', max_tokens=10000,
                system=cq.AUDIT_SYSTEM+'\n'+eb.REVIEW_POLICY+'\n構成全体の回答・順序と、今回の追加指摘の解消を確認する。本文未生成は欠陥ではない。checksはcoverageだけ返す。',
                output_config=eb.review_config(cq.audit_output_config(('coverage',),locations=True), design),
                messages=[{'role':'user','content':json.dumps(payload,ensure_ascii=False)}])
            with qb.scope(job,'quality'):
                value, usage = tr.checked_request(job,'partial_outline_review',check)
            checks = fq.parse_focus(json.dumps({'checks':value['checks']}),('coverage',),outline['content_text'])
            issues = eb.checked_review(value,design)
            report = {'valid':all(c['status']=='pass' for c in checks) and not issues,
                'scope':'Full blueprint semantic/order review only; no fresh evidence or normal readiness certificate',
                'checks':checks, 'blueprint_issues':issues, 'raw_verdict':value}
            write(root/'outline-review.json',report)
            result.update(status='completed' if report['valid'] else 'needs_revision', outline_review_valid=report['valid'])
        elif stage in ('write', 'repair'):
            review = json.loads((root/'outline-review.json').read_text())
            if not review['valid']:
                raise RuntimeError('Current blueprint has not passed its scoped review')
            outline = json.loads((root/'outline.json').read_text())
            candidate = eb.require_projection(outline,context)
            targeted = [s for s in candidate['sections'] if s['id'] in TARGETS+[NEW]]
            refs = {r for s in targeted for e in s['answer_elements'] for r in e['claim_ids']}
            claims = {k:context['accepted_claims'][k] for k in refs}
            writer_data = {
                    'scope':'正式編集設計を使う部分執筆。各キーには該当見出し直下の本文だけを書き、H1/H2/H3などの見出し行は出力しない。leadにはリードだけ。見出しと対象外本文はコードが固定のまま結合する。',
                    'feedback':FEEDBACK, 'formal_blueprint':candidate, 'target_sections':targeted,
                    'confirmed_claims':claims, 'source_catalog':context['sources'],
                    'new_reader_hypotheses':needs['selected'], 'original_full_article_for_context':original,
                    'length_note':'leadは180字前後を目安にし、必要な主題・望み・ためらい・読む意味を自然に残す。短いことだけを欠陥とせず、字数合わせの水増しはしない。まとめは150〜220字程度。',
                    'placement':'s01とs10のH2直下は結論・短い理由、s01は続いて6方法の比較表。詳しい共感・価値説明・対策はs07/s11のH3。s07/s11の主要情報表と条件は保持する。料金表・登録手順・既存CTAは対象外のため出力しない。',
                    'citation_style':'元記事の設定を維持。内部ID・確認過程・採用判断を記事に出さない。',
                }
            keys = ['lead']+TARGETS+[NEW]
            if stage == 'repair':
                audit = json.loads((root/'scoped-audit.json').read_text())
                notes = json.loads((root/'codex-initial-findings.json').read_text())
                previous = json.loads((root/'written-parts.json').read_text())
                keys = list(dict.fromkeys(n['target'] for n in notes['findings']))
                if audit['valid'] or not set(keys) <= set(previous):
                    raise RuntimeError('No matching consolidated repair scope')
                writer_data.update(
                    scope='一回の統合局所修正。指摘されたキーの本文だけをJSONで返す。見出しは出力しない。未対象のs01とs18、および既存13節は変えない。',
                    current_parts=previous, mandatory_model_findings=audit['editorial_findings'],
                    readability_issues=audit['readability_issues'], codex_findings=notes['findings'],
                    measured_previous_lead_chars=len(previous['lead']),
                    measurement_note='前回lead実測216字。モデルの約250字は誤計数。元の275字の約3分の2、今回は180字前後へ短縮。主要語・望みとためらい・読む意味を残し、方法名の列挙と予告の重複を減らす。',
                    preservation='既婚者専用マッチングと既婚者クラブを明確に勧める。機能と重要な利用条件を残す。s07には元記事の主要情報表を戻す。深い価値説明はH3で担い、s10は推薦結論・短い理由・必要な有料条件に絞る。',
                )
                write(root/'consolidated-repair-plan.json', {'target_keys':keys,
                    'previous_parts_sha256':sha(previous), 'audit_sha256':sha(audit),
                    'codex_findings_sha256':sha(notes), 'one_repair_only':True})
            request = dict(model=step_article.MODEL, max_tokens=9000, system=step_article.SYSTEM_PROMPT,
                output_config=eb.model_output_config(rn.obj({k:rn.STR for k in keys})),
                messages=[{'role':'user','content':json.dumps(writer_data,ensure_ascii=False)}])
            with qb.scope(job,'article'):
                response = measured(client,**request)
            texts = json.loads(ai.message_text(response))
            if set(texts) != set(keys) or any(not t.strip() or re.search(r'^#{1,6} ',t,re.M) for t in texts.values()):
                raise RuntimeError('Partial writer returned missing or unplanned headings')
            suffix = '-v2' if stage == 'repair' else ''
            if stage == 'repair':
                texts = {**previous, **texts}
            title = original.split('\n',1)[0]
            merged = title+'\n\n'+texts['lead'].strip()+'\n\n'
            excerpt = merged
            for s in candidate['sections']:
                sid = s['id']
                if sid in texts:
                    segment = '#'*s['level']+' '+s['title']+'\n\n'+texts[sid].strip()+'\n\n'
                    if sid == NEW and not merged.endswith('\n\n'):
                        # The frozen article has no final newline. Insert the
                        # new chapter delimiter without editing its last text.
                        merged += '\n\n'
                    merged += segment
                    excerpt += segment
                else:
                    merged += pieces[sid]
            located = split_sections(merged,candidate['sections'])
            last = blueprint['sections'][-1]['id']
            unchanged = {s['id']:located[s['id']]==pieces[s['id']]
                or (s['id']==last and located[s['id']]==pieces[s['id']]+'\n\n')
                for s in blueprint['sections'] if s['id'] not in TARGETS}
            if not all(unchanged.values()):
                raise RuntimeError('Untargeted article text changed')
            write(root/('written-parts'+suffix+'.json'),texts)
            (root/('merged-article'+suffix+'.md')).write_text(merged)
            (root/('changed-excerpts'+suffix+'.md')).write_text(excerpt)
            from pipeline.section_identity import bind_sections
            binding = bind_sections(merged,outline['content_text'])
            write(root/('binding'+suffix+'.json'),binding)
            result.update(status='completed', lead_chars=len(texts['lead']),
                untouched_sections=unchanged, merged_chars=len(merged), changed_excerpt_chars=len(excerpt),
                added_boundary_separator_before_closing='two newlines; existing final paragraph bytes retained',
                merged_body_sha256=hashlib.sha256(merged.encode()).hexdigest(),
                all_headings_match=True, full_article_regenerated=False)
        else:
            suffix = '-v2' if stage == 'audit2' else ''
            outline = json.loads((root/'outline.json').read_text())
            texts = json.loads((root/('written-parts'+suffix+'.json')).read_text())
            excerpt = (root/('changed-excerpts'+suffix+'.md')).read_text()
            decisions = [
                {'id':'p1','question':'短い導入と方法比較で、求める関係と大切な日常の両方から方法を選べるか',
                 'answer_requirements':['主題と対象が冒頭で分かり、望みとためらいが読む意味につながる','方法の違いから既婚者専用マッチングを明確に勧める理由が分かる','何を得たい・守りたいから迷うかを特徴の作用と具体策へつなぐ']},
                {'id':'p2','question':'既婚者クラブを勧める理由と、自分のペースで始める具体策が分かるか',
                 'answer_requirements':['機能分類だけでなく、望む関係やためらいにどう役立つか説明する','確認済みの対象・機能・条件を保ち、まず確かめることを示す','H2は推薦概要、深い説明はH3へ配置する']},
                {'id':'p3','question':'結末で主回答と推薦、読者の次の一歩を回収するか',
                 'answer_requirements':['既婚者専用マッチングと既婚者クラブの推薦を短く回収する','読者の望みに関係する理由と次の一歩を示す','新しい事実・不安・未検証の効果を足さない']},
            ]
            research_ids=sorted({q for s in outline['meta']['editorial_blueprint']['sections'] if s['id'] in TARGETS+[NEW] for q in s['research_ids']})
            for d in decisions:
                d.update(role='primary',scope='changed excerpts only',depends_on=[],research_ids=research_ids)
            design={'version':ad.VERSION,'blueprint_sha256':eb.fingerprint(outline['meta']['editorial_blueprint']),
                'lead_direction':outline['meta']['editorial_blueprint']['lead_direction'],
                # Correspondence indexes refer to the full formal outline.
                # Missing excerpt sections remain visibly unresolved; slicing
                # this list would shift all ids after s01 onto wrong indexes.
                'reader_decisions':decisions,'sections':outline['meta']['editorial_blueprint']['sections']}
            requirements={'keyword':'セカンドパートナー 探し方','article_purpose':'cv','custom_prompt':FEEDBACK,
                'measured_lead_chars':len(texts['lead']), 'original_lead_chars':275, 'lead_target_chars_approximate':180,
                'evaluation_scope':'非連続の変更部分だけを提示。中間の方法H3・選び方・料金表・登録手順・CTA・注意点本文は既存のまま別に存在し、今回の有料QAの対象外。抜粋にないことを欠陥とせず、提示した説明と配置の品質を検査する。全記事を合格と判定しない。',
                'full_heading_order':[s['title'] for s in outline['meta']['editorial_blueprint']['sections']],
                'article_design':design,'intent_value_context':needs['selected'],
                'research_plan':json.loads(artifact('research_plan')['content_text']),
                'research_decisions':json.loads(artifact('research_matrix')['content_text'])}
            def checkpoint(role,phase):
                write(root/('qa-phases'+suffix)/(role+'.json'),phase)
                print(role,[(c['key'],c['status']) for c in phase['checks']],flush=True)
            with qb.scope(job,'content_audit'):
                report=fq.audit_article(client,text=excerpt,facts=artifact('fact_sheet')['content_text'],
                    outline=outline['content_text'],contract={},requirements=requirements,
                    sources=cq.source_evidence(artifact('fresh_sources')),checkpoint=checkpoint)
            report.update(scope='Changed excerpts only; not a whole merged article certificate')
            write(root/('scoped-audit'+suffix+'.json'),report)
            result.update(status='completed' if report['valid'] else 'needs_revision',scoped_qa_valid=report['valid'],
                full_article_qa=False,readability_issues=report.get('readability_issues'))
    except Exception as exc:
        result.update(status='stopped',error_type=type(exc).__name__,error=str(exc))
        raise
    finally:
        calls=[c for p in (root/'ledger').glob('*.json') for c in json.loads(p.read_text())['calls']]
        result.update(new_cost_with_holds_usd=sum(c.get('cost_usd',c['reserved_usd']) for c in calls),
            runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            cap_usd=7.5,pending_calls=sum(c['status']=='pending' for c in calls),
            source_article_unchanged=sha(artifact('article')['content_text'])==frozen['source_article_sha256'],
            full_article_qa=False,production_changes=False)
        write(root/(stage+'-result.json'),result)
        print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
