"""One editable blueprint; Markdown and trace links are derived, never co-generated."""
from __future__ import annotations

import copy
import hashlib
import json
import re

VERSION = 'editorial-blueprint-v2-reader-progression'
POLICY = '''【読者の理解・判断を設計する構成】
editorial_blueprintを唯一の構成としてJSONで返す。Markdownはコードで描画するため生成しない。
sectionsは記事順の平坦な配列。idはs01等の一意なID、parent_idは直前の親のID（H2は空文字）。
各節のquestion/conclusion/answer_elementsに問い・結論・説明事項を記し、reader_progressionに章の前後の変化を設計する。完成本文や料金表の全セルは書かない。重要節では必要な区別・理由・手順・選ぶ条件を具体化する。「詳しく説明」のみは不足。親節は章の結論と子節の役割、子節は具体的な説明を担い、全欄へ同じ説明を複製しない。
need_idsは読者分析、decision_idsは読者の判断、research_idsは調査質問、answer_elements.claim_idsはaccepted_claimsのIDを参照する。need_idsには部分的に扱う関連ニーズも含められ、そのすべての判断をこの節で完了させる必要はない。decision_idsに対応する分析IDは保持する。計画のresearch_idsは調査の案内であり、他の質問の採用済み主張を関連する判断へ再利用してよい。関連性と回答の十分さは独立確認で評価する。IDの割当ては充足の証明ではない。主張の実条件は参照IDから描画されるため条件・数値を別欄に再入力しない。確認済みの主張を都合よく一般化・組み替えない。一般的な助言・架空の場面例は事実と区別し、効果・料金・専門判断を創作しない。
H2はreader_progressionでbefore（理解済みのこと・未解決の問い・必要なら心理仮説）、after（この章で解消する疑問・理解・判断・行動・狙う感情の変化）、next_questions（残る/新たに生まれる/意図して意識してもらう問い）、marketing（記事目的への役割・変化を生む説明・次の行動）を設計する。この章が担う一歩でよく、記事全体の課題を毎章で完結させない。afterの理解/判断/行動は必要なものだけ、emotionは狙う変化がある場合だけ記入する。beforeのemotionは読者全員の実態ではなく設計上の仮説。
H3/H4のreader_progressionは独自の変化を担うときだけ差分を記し、親と重複する場合はnull。親の開始状態・読後状態を子節すべてへ複製しない。次の問いがなければnext_questions=[]で完結する。same_sectionはこの節で解消（target_section_idは自分）、next_sectionは後続の節IDへ引継ぎ、related/out_of_scopeはtarget_section_idを空文字にして扱う理由を記す。単なる関連記事を読者の次の疑問と偽らず、前章の読後状態から次章の開始状態がつながるようにする。
マーケティング目的に沿って認識・感情・行動の変化を意図してよい。潜在課題/願望への気づき、懸念の重要性の認識、新しい選択肢、利用後の変化のイメージ、期待・共感・安心・納得、比較・相談・登録への誘導、問いかけや価値の予告を設計できる。marketingは必要な章だけ指定し、不要ならnull。次の行動は認知・理解が目的なら空文字でもよい。全章にCTAや同じ感情を要求しない。脅迫、虚偽の恐怖/効果/緊急性、判断に重要な条件の隠蔽は行わない。商品の適合条件と確認済み事実を保ち、心理は仮説、感情変化は狙いであって実測効果や全員の感情として断定しない。合理的な助言や価値説明に効果率・同一文章の引用を一律要求しない。
具体例exampleは役立つ節だけ記入し、不要なら空文字。説明用の仮の場面/操作例とし、実体験や調査結果に偽装しない。
主回答の概要から必要な詳細まで連続して説明し、その後に商品選択や補足へ進む。必要な前提は先に置ける。方法選びが問いなら各方法の違いと向く状況に答え、商品名一覧をその代わりにしない。単一手順に不要な比較章を追加しない。depends_onは回答順序に反映し、短い概要だけで下位の推薦へ進まない。
採否と説明を確定してからH2全体のbudgetを見積もる（子節込み）。子節のbudgetはchars=0/importance=0/reason空文字。競合文字数は参考であり合計を合わせない。明示的な文字数指定では重複と補足を先に減らし主回答を薄めない。文字数だけを理由に必須論点を削らない。
omissionsには省く内容と読者判断を損なわない理由を記録する。同じ質問の採用済み部分を使い、未確認の細部だけ省く場合も、その質問IDと省く範囲を記録してよい。essential/requiredの主要回答全体は省略不可。supportingは全件の列挙・採用を求めない。FAQは既出回答を繰り返さず必要な場合だけ。
リード方針は今回の読者設計と既存の執筆基準を引き継ぎ、完成リードは作らない。CTAは読者設計のcta_roleを守り、商品が役立つ状況と章の読後判断に合う位置だけをctaで指定する。omit/deferならCTAを設計しない。内部ID・分析ラベル・掲載判断の管理文を完成記事へ出さない。
'''
REVIEW_POLICY = '''【編集設計の個別確認】
blueprint_reviewsを読者判断ごと・answer_requirementsの0始まり番号ごとに判定する。割当てID・表・見出し・出典があるだけでpassにしない。conclusion/answer_elements/reader_progressionと実際の主張・条件を照合し、読者が比較・理解・実行するための説明が設計されているかを確認する。
statusはpass/answer_missing/evidence_missing。説明や具体性の欠落はanswer_missing、必要な事実が採用資料にない場合だけevidence_missing。supportingの合理的省略はomittedを許可する。主要判断の不足は補足や商品紹介の充実で相殺しない。
section_idsは実際に回答する節、欠落時は直すべき最寄りの節を示す。orderは主回答の詳細を終える前の推薦・不要な補足による中断、depends_onとの不整合、子節と親の問いの不一致を確認する。好みだけではfailにしない。reasonに設計された回答または不足を具体的に示す。reader_progression_reviewsはprogression_section_idsの各節を個別に確認する。この章が解消する範囲と実際の回答要素、読後の理解/判断/行動、狙う感情を生む説明、次の問いが生じる理由と引継ぎ先の回答、記事目的への役割が対応するか確認する。子節は親から引継ぐ状態を踏まえ、記事全体を毎章で解決することや、親の全要素を各子節へ要求しない。不要な感情変化・次の問い・marketingがないことは合格。感情への働きかけ、潜在課題の顕在化、商業的な誘導があること自体を不合格理由にしない。好みや強さの差と、説明不足・根拠不足・遷移の不整合を区別し、欠陥を具体的に示す。statusはpass/answer_missing/evidence_missing。モデルから全体validは返さず、個別結果からコードで集計する。
'''


def fail(message):
    from .content_quality import ContentQualityError
    raise ContentQualityError(message)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def obj(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


STRING = {'type':'string'}
STRINGS = {'type':'array','items':STRING}
BUDGET_SCHEMA = obj({'chars':{'type':'integer','minimum':0},'importance':{'type':'integer','minimum':0,'maximum':5},'reason':STRING})


def nullable(schema):
    return {'anyOf':[schema,{'type':'null'}]}


PROGRESSION_SCHEMA = obj({
    'before':obj({'understanding':STRING,'open_questions':STRINGS,'emotion':STRING}),
    'after':obj({'resolved_questions':STRINGS,'understanding':STRING,'decision':STRING,'action':STRING,'emotion':STRING}),
    'next_questions':{'type':'array','items':obj({'question':STRING,'reason':STRING,
        'handling':{'type':'string','enum':['same_section','next_section','related','out_of_scope']},'target_section_id':STRING})},
    'marketing':nullable(obj({'role':STRING,'mechanism':STRING,'next_action':STRING}))})


SECTION_SCHEMA = obj({
    'id':STRING,'parent_id':STRING,'level':{'type':'integer','enum':[2,3,4]},'title':STRING,
    'need_ids':STRINGS,'decision_ids':STRINGS,'research_ids':STRINGS,
    'question':STRING,'conclusion':STRING,
    'answer_elements':{'type':'array','minItems':1,'items':obj({'instruction':STRING,'claim_ids':STRINGS})},
    'reader_progression':nullable(PROGRESSION_SCHEMA),'example':STRING,
    'format':{'type':'string','enum':['文章','箇条書き','番号付きリスト','比較表','データ表']},
    'items':STRINGS,'cta':STRING,'budget':BUDGET_SCHEMA})
SCHEMA = obj({'titles':{'type':'array','minItems':1,'maxItems':3,'items':STRING},'lead_direction':STRING,
    'sections':{'type':'array','minItems':1,'items':SECTION_SCHEMA},
    'omissions':{'type':'array','items':obj({'research_id':STRING,'reason':STRING})}})
def model_output_config(schema):
    """Keep semantic bounds locally; send Claude's supported schema subset.

    https://platform.claude.com/docs/en/build-with-claude/structured-outputs
    Literal enums and object shape stay constrained by the API. Unsupported
    numeric/length bounds become instructions and remain enforced by validate.
    """
    value=copy.deepcopy(schema)
    def visit(node):
        if not isinstance(node,dict):return
        constraints={k:node.pop(k) for k in ('minimum','maximum','multipleOf','minLength','maxLength','maxItems','uniqueItems') if k in node}
        if node.get('minItems') not in (None,0,1):constraints['minItems']=node.pop('minItems')
        if constraints:
            node['description']=(node.get('description','')+' アプリ側で検証する条件: '+json.dumps(constraints,ensure_ascii=False)).strip()
        for key,child in node.items():
            if key=='properties':
                for prop in child.values():visit(prop)
            elif key in ('items','additionalProperties') and isinstance(child,dict):visit(child)
            elif key in ('anyOf','allOf','oneOf'):
                for branch in child:visit(branch)
    visit(value)
    return {'format':{'type':'json_schema','schema':value}}


OUTPUT_CONFIG = model_output_config(SCHEMA)


def context(plan, matrix):
    from .generation_context import bundle_data
    bundle = bundle_data(plan,matrix,quotes=False,planning=True)
    claims={}
    for row in bundle['decisions']:
        if not row['publishable']:continue
        # Explicit compatibility for already accepted pre-claims answers.
        units=row.get('claims') or [{'id':row['id']+'.answer','text':row['answer'],
            'conditions':[{'dimension':'other','value':v} for v in row['publication_conditions']],
            'answer_type':row.get('answer_type','sourced'),'source_ids':row['source_ids'],
            'supports_current_conclusion':row.get('supports_current_conclusion',False)}]
        for unit in units:
            claims[unit['id']]={**copy.deepcopy(unit),'research_id':row['id'],'claim_limits':copy.deepcopy(row['claim_limits'])}
    return {'reader_design':bundle['reader_design'],'reader_decisions':bundle['reader_decisions'],
        'topics':copy.deepcopy(plan['items']),'accepted_claims':claims,'sources':bundle['sources'],
        'content_gaps':bundle['editorial_planning']['content_gaps']}


def validate_shape(value, schema):
    if 'anyOf' in schema:
        from .content_quality import ContentQualityError
        for branch in schema['anyOf']:
            try:
                validate_shape(value,branch)
                return
            except ContentQualityError:
                pass
        fail('編集設計の任意項目が不正です。')
    kind=schema['type']
    if kind=='null':
        if value is not None:fail('編集設計の未指定項目が不正です。')
    if kind=='object':
        if not isinstance(value,dict) or set(value)!=set(schema['properties']):fail('編集設計の項目が不足・不明です。')
        for k,v in value.items():validate_shape(v,schema['properties'][k])
    elif kind=='array':
        if not isinstance(value,list) or not schema.get('minItems',0)<=len(value)<=schema.get('maxItems',10**9):fail('編集設計の配列が不正です。')
        for v in value:validate_shape(v,schema['items'])
    elif kind=='string':
        if not isinstance(value,str):fail('編集設計の文字列が不正です。')
    elif kind=='integer':
        if type(value) is not int or not schema.get('minimum',-10**9)<=value<=schema.get('maximum',10**9):fail('編集設計の整数が不正です。')
    if 'enum' in schema and value not in schema['enum']:fail('編集設計の選択値が不正です。')



def canonicalize_research_refs(value, ctx):
    """Derive accepted claim provenance, never invent claims or evidence."""
    validate_shape(value,SCHEMA)
    result=copy.deepcopy(value)
    for section in result['sections']:
        for element in section['answer_elements']:
            for ref in element['claim_ids']:
                claim=ctx['accepted_claims'].get(ref)
                if claim is None:fail('回答要素が未採用の主張を参照しています。')
                if claim['research_id'] not in section['research_ids']:
                    section['research_ids'].append(claim['research_id'])
    return result

def validate(value, ctx):
    """Structural referential integrity only; semantic acceptance belongs to review."""
    validate_shape(value,SCHEMA)
    sections=value['sections'];seen={};stack={};h2_titles=set()
    needs={n['id'] for n in ctx['reader_design'].get('reader_needs',[])}
    decisions={d['id']:d for d in ctx['reader_decisions']};questions={q['id']:q for q in ctx['topics']}
    claims=ctx['accepted_claims'];assigned=set();assigned_decisions=set()
    for s in sections:
        sid=s['id'];level=s['level']
        if not re.fullmatch(r's\d+',sid) or sid in seen:fail('編集設計の節IDが不正・重複しています。')
        for key in ('title','question','conclusion'):
            if not s[key].strip() or '\n' in s[key]:fail('編集設計の問い・結論・読後状態が不足または複数行です。')
        for key in ('example','cta','budget'):
            if key!='budget' and '\n' in s[key]:fail('編集設計の任意指示は1行にしてください。')
        if level==2:
            if s['parent_id'] or s['title'] in h2_titles:fail('章の親ID・タイトルが重複または不正です。')
            if s['budget']['chars']<=0 or s['budget']['importance']<1 or not s['budget']['reason'].strip():fail('章の説明に基づく分量設計が不足しています。')
            h2_titles.add(s['title'])
        elif s['parent_id']!=stack.get(level-1):fail('節の階層・親IDが記事順と一致しません。')
        elif s['budget']!={'chars':0,'importance':0,'reason':''}:fail('子節の分量を章と二重計上できません。')
        stack={k:v for k,v in stack.items() if k<level};stack[level]=sid;seen[sid]=s
        for key,allowed in [('need_ids',needs),('decision_ids',decisions),('research_ids',questions)]:
            if len(set(s[key]))!=len(s[key]) or any(i not in allowed for i in s[key]):fail('編集設計の参照IDが不明・重複しています: '+key)
        # Planned question links guide collection; they are not exclusive
        # evidence ownership. Relevance is checked in independent section QA.
        if not (set(s['decision_ids']) & needs)<=set(s['need_ids']):fail('分析と判断の対応IDが一致しません。')
        for element in s['answer_elements']:
            if not element['instruction'].strip() or '\n' in element['instruction']:fail('具体的な回答要素が不足しています。')
            refs=element['claim_ids']
            if len(set(refs))!=len(refs) or any(c not in claims or claims[c]['research_id'] not in s['research_ids'] for c in refs):fail('回答要素が未採用の主張・別の質問を参照しています。')
        for key in ('items',):
            if any(not x.strip() or '\n' in x for x in s[key]):fail('掲載項目の形式が不正です。')
        if s['cta'] and ctx['reader_design'].get('product_fit',{}).get('cta_role') in ('omit','defer'):fail('読者設計で保留・省略したCTAを構成へ戻せません。')
        assigned.update(s['research_ids']);assigned_decisions.update(s['decision_ids'])
    omitted={}
    for omission in value['omissions']:
        qid=omission['research_id']
        if qid not in questions or qid in omitted or not omission['reason'].strip():fail('省略論点のID・理由が不正です。')
        omitted[qid]=omission['reason']
    for qid,q in questions.items():
        if q.get('required') or q.get('priority')=='essential':
            if qid not in assigned:fail('必須論点が編集設計から失われています: '+qid)
        elif q.get('priority')=='important' and qid not in assigned and qid not in omitted:fail('重要論点の省略理由がありません: '+qid)
    for did,d in decisions.items():
        if d['role']!='supporting' and did not in assigned_decisions:fail('重要な読者判断が構成へ割り当てられていません: '+did)
    validate_progression(value)
    if not all(t.strip() and '\n' not in t for t in value['titles']) or not value['lead_direction'].strip():fail('タイトル・リード方針が不足しています。')
    return value


def trace(value, ctx):
    """These edges record intended use, never assert answer coverage."""
    return [{'section_id':s['id'],'need_ids':s['need_ids'],'decision_ids':s['decision_ids'],
        'research_ids':s['research_ids'],
        'claim_ids':list(dict.fromkeys(c for e in s['answer_elements'] for c in e['claim_ids'])),
        'reader_outcome':copy.deepcopy(s['reader_progression']['after']) if s['reader_progression'] else None,
        'next_section_ids':list(dict.fromkeys(q['target_section_id'] for q in (s['reader_progression'] or {}).get('next_questions',[]) if q['handling']=='next_section'))}
        for s in value['sections']]


def render(value, ctx):
    lines=['# 記事構成案','', '### タイトル案','']
    lines += ['- '+t for t in value['titles']]
    lines += ['', '### リード文の設計方針','',value['lead_direction'],'']
    def field(name, val):lines.append('- '+name+'：'+val)
    for s in value['sections']:
        lines += ['#'*(s['level']+1)+f" H{s['level']}：{s['title']}",'']
        field('セクションID',s['id'])
        if s['level']==2:lines += ['','**H2直下方針**：'+s['conclusion'],'']
        field('読者の疑問',s['question']);field('伝える結論',s['conclusion'])
        field('読者意図ID',', '.join(s['need_ids']) or 'なし')
        field('読者判断ID',', '.join(s['decision_ids']) or 'なし')
        field('対応論点',', '.join(s['research_ids']) or 'なし')
        field('回答要素','')
        used=[]
        for e in s['answer_elements']:
            lines.append('  - '+e['instruction']+('（'+', '.join(e['claim_ids'])+'）' if e['claim_ids'] else ''))
            used += e['claim_ids']
        progression=s['reader_progression']
        if progression:
            before,after=progression['before'],progression['after']
            if before['understanding']:field('読む前の理解',before['understanding'])
            if before['open_questions']:field('この時点の未解決の問い','／'.join(before['open_questions']))
            if before['emotion']:field('読む前の心理（仮説）',before['emotion'])
            field('この章で解消する疑問','／'.join(after['resolved_questions']))
            for name,key in [('読後に理解できること','understanding'),('読後に判断できること','decision'),('読後に実行できること','action'),('狙う感情の変化','emotion')]:
                if after[key]:field(name,after[key])
            for q in progression['next_questions']:
                handling={'same_section':'この節で解消','next_section':'後続の節へ','related':'関連情報へ案内','out_of_scope':'記事の範囲外'}[q['handling']]
                field('次の問い',q['question']+'（'+q['reason']+'／'+handling+('：'+q['target_section_id'] if q['target_section_id'] else '')+'）')
            marketing=progression['marketing']
            if marketing:
                field('記事目的への役割',marketing['role']);field('変化を生む説明・導線',marketing['mechanism'])
                if marketing['next_action']:field('導きたい次の行動',marketing['next_action'])
        if s['example']:field('説明用の仮の具体例',s['example'])
        field('表現形式',s['format']);field('掲載項目','／'.join(s['items']) or 'なし')
        field('使用する根拠','' if used else '該当資料なし（一般的助言は事実と区別する）')
        for cid in dict.fromkeys(used):
            claim=ctx['accepted_claims'][cid]
            urls=list(dict.fromkeys(ctx['sources'][r]['url'] for r in claim['source_ids']))
            lines.append('  - '+claim['research_id']+'：'+(' '.join(urls) or '一般的な提案')+'（'+cid+'）')
            conditions='／'.join(c['value'] for c in claim['conditions'])
            if conditions:lines.append('    - この主張の実条件：'+conditions)
        if s['cta']:field('CTA設計',s['cta'])
        lines.append('')
    if value['omissions']:
        lines += ['### 編集メモ','']+['- '+o['research_id']+'：'+o['reason'] for o in value['omissions']]+['']
    lines += ['### セクション別ボリューム設計','',
        '| H2タイトル | 重要度(1-5) | 推奨文字数 | 根拠（1文） |',
        '|-----------|------------|-----------|-----------|']
    for s in value['sections']:
        if s['level']==2:
            b=s['budget'];cell=lambda t:t.replace('|','／').replace('\n',' ')
            lines.append(f"| {cell(s['title'])} | {b['importance']} | {b['chars']:,}字 | {cell(b['reason'])} |")
    return '\n'.join(lines)+'\n'


def metadata(value, ctx):
    return {'blueprint_version':VERSION,'editorial_blueprint':copy.deepcopy(value),
        'blueprint_inputs_sha256':fingerprint(ctx),'blueprint_context':copy.deepcopy(ctx),
        'reader_trace':trace(value,ctx)}


def require_projection(artifact, ctx=None):
    meta=artifact.get('meta') or {}
    if not meta.get('blueprint_version'):return None
    if meta['blueprint_version']!=VERSION:fail('編集設計の版が現在と一致しません。')
    saved=meta.get('blueprint_context');value=meta.get('editorial_blueprint')
    if not isinstance(saved,dict) or meta.get('blueprint_inputs_sha256')!=fingerprint(saved):fail('編集設計の採用資料が不整合です。')
    if ctx is not None and fingerprint(ctx)!=meta['blueprint_inputs_sha256']:fail('編集設計後に読者設計・調査回答が変更されています。再確認が必要です。')
    validate(value,saved)
    if artifact['content_text']!=render(value,saved) or meta.get('reader_trace')!=trace(value,saved):fail('編集設計と表示用構成・対応表が一致しません。')
    return value


def review_config(config, ctx):
    result=copy.deepcopy(config);root=result['format']['schema'];entries={}
    ids=[s for s in ctx.get('section_ids',[])]
    refs={'type':'array','items':{'type':'string','enum':ids}}
    for d in ctx['reader_decisions']:
        checks={str(i):obj({'status':{'type':'string','enum':['pass','answer_missing','evidence_missing','omitted']},
            'reason':STRING,'section_ids':refs}) for i in range(len(d['answer_requirements']))}
        entries[d['id']]=obj({'requirements':obj(checks),'order':obj({
            'status':{'type':'string','enum':['pass','fail']},'reason':STRING,'section_ids':refs})})
    root['properties']['blueprint_reviews']=obj(entries);root['required'].append('blueprint_reviews')
    progression_check=obj({'status':{'type':'string','enum':['pass','answer_missing','evidence_missing']},'reason':STRING})
    root['properties']['reader_progression_reviews']=obj({sid:copy.deepcopy(progression_check) for sid in ctx.get('progression_section_ids',[])})
    root['required'].append('reader_progression_reviews')
    return result


def checked_review(value, ctx):
    rows=value.get('blueprint_reviews');decisions=ctx['reader_decisions'];section_ids=set(ctx['section_ids']);issues=[]
    if not isinstance(rows,dict) or set(rows)!={d['id'] for d in decisions}:fail('編集設計の個別確認が不足しています。')
    for d in decisions:
        row=rows[d['id']]
        if not isinstance(row,dict) or not isinstance(row.get('requirements'),dict) or set(row['requirements'])!={str(i) for i in range(len(d['answer_requirements']))}:fail('必要回答の確認件数が一致しません。')
        for index,r in list(row['requirements'].items())+[('order',row.get('order'))]:
            allowed=('pass','fail') if index=='order' else ('pass','answer_missing','evidence_missing','omitted')
            if not isinstance(r,dict) or r.get('status') not in allowed or not isinstance(r.get('reason'),str) or not r['reason'].strip():fail('編集設計の確認結果が不正です。')
            refs=r.get('section_ids')
            if not isinstance(refs,list) or len(set(refs))!=len(refs) or any(i not in section_ids for i in refs) or (r['status']!='omitted' and not refs):fail('編集設計の確認箇所が不明です。')
            if r['status']=='omitted' and d['role']!='supporting':fail('主要な読者判断を省略済みとして承認できません。')
            if r['status'] not in ('pass','omitted') and (d['role']!='supporting' or index=='order'):
                issues.append({'decision_id':d['id'],'requirement_index':index,'section_ids':refs,
                    'reason':r['reason'],'repair_kind':'source' if r['status']=='evidence_missing' else 'editorial'})
    issues.extend(checked_progression_reviews(value,ctx))
    return issues


def apply_review(report, value, ctx):
    issues=checked_review(value,ctx)
    report['blueprint_reviews']=copy.deepcopy(value['blueprint_reviews']);report['reader_progression_reviews']=copy.deepcopy(value['reader_progression_reviews']);report['blueprint_issues']=issues
    if issues:
        report['valid']=False
        coverage=next(c for c in report['checks'] if c['key']=='coverage')
        coverage.update(status='fail',reason=coverage['reason']+'\n編集設計: '+json.dumps(issues,ensure_ascii=False))
        if any(i['repair_kind']=='source' for i in issues):report['evidence_pending']=True
    return report


def apply_edits(value, response, ctx, *, structural, targets):
    candidate=copy.deepcopy(value);by_id={s['id']:s for s in candidate['sections']};seen=set()
    if set(response)!={'edits','delete_ids','section_order'}:fail('編集設計の局所修正形式が不正です。')
    for edit in response['edits']:
        sid=edit['id'];s=edit['section']
        if sid in seen or sid!=s['id'] or (sid not in targets and (not structural or sid in by_id)):fail('対象外・重複の節を修正できません。')
        if sid in by_id and not structural and any(s[k]!=by_id[sid][k] for k in ('title','parent_id','level')):fail('構造変更が許可されていません。')
        if sid not in by_id:
            target_topics={q for section in value['sections'] if section['id'] in targets for q in section['research_ids']}
            if not set(s['research_ids']) & target_topics:fail('局所修正と関係のない新しい節を追加できません。')
        by_id[sid]=copy.deepcopy(s);seen.add(sid)
    deleted=response['delete_ids'];order=response['section_order']
    if not structural and (deleted or order):fail('節の削除・移動は許可されていません。')
    if len(set(deleted))!=len(deleted) or any(i not in targets or i not in by_id for i in deleted):fail('削除する節の指定が不正です。')
    for sid in deleted:by_id.pop(sid)
    if order:
        readonly=[s['id'] for s in value['sections'] if s['id'] not in targets]
        if [i for i in order if i in readonly]!=readonly:fail('対象外の節どうしの順序を変更できません。')
        if len(set(order))!=len(order) or set(order)!=set(by_id):fail('修正後の節順序が不正です。')
        candidate['sections']=[by_id[i] for i in order]
    else:
        if set(by_id)-{s['id'] for s in value['sections']}:fail('新しい節の挿入位置がありません。')
        candidate['sections']=[by_id[s['id']] for s in value['sections'] if s['id'] in by_id]
    if candidate==value:fail('編集設計に変更がありません。')
    return validate(canonicalize_research_refs(candidate,ctx),ctx)


def repair_config():
    return model_output_config(obj({
        'edits':{'type':'array','items':obj({'id':STRING,'section':SECTION_SCHEMA})},
        'delete_ids':STRINGS,'section_order':STRINGS}))


def review_context(artifact):
    value=require_projection(artifact)
    if value is None:return None
    ctx=artifact['meta']['blueprint_context']
    return {'version':VERSION,'section_ids':[s['id'] for s in value['sections']],
        'progression_section_ids':[s['id'] for s in value['sections'] if s['reader_progression'] is not None],
        'reader_decisions':copy.deepcopy(ctx['reader_decisions']),
        'inputs_sha256':artifact['meta']['blueprint_inputs_sha256'],'blueprint_sha256':fingerprint(value)}


def require_review(report, artifact):
    ctx=review_context(artifact)
    if ctx is None:return
    if report.get('blueprint_review_context')!=ctx or checked_review(report,ctx):
        fail('現在の編集設計に対する個別の回答・順序確認が未合格です。')


def repair_targets(artifact, report):
    """Map exact reviewed paragraph IDs to sections; never guess from keywords."""
    from .article_quality import outline_sections
    from .content_edits import content_blocks
    value=require_projection(artifact);text=artifact['content_text'];targets=set()
    spans=outline_sections(text)
    blocks=content_blocks(text);ranges={};cursor=0
    for block in blocks:
        start=text.index(block['text'],cursor);cursor=start+len(block['text'])
        ranges[block['id']]=(start,cursor)
    for check in report['checks']:
        if check['status']!='fail':continue
        for loc in check.get('affected_blocks',[]):
            start,end=ranges.get(loc['id'],(-1,-1))
            for span,s in zip(spans,value['sections']):
                if start<span['end'] and end>span['start']:targets.add(s['id'])
    for issue in report.get('blueprint_issues',[]):targets.update(issue['section_ids'])
    for issue in report.get('structural_issues',[]):
        title=issue.get('title') or issue.get('h2_title')
        targets.update(s['id'] for s in value['sections'] if s['title']==title)
    # Unlocalized legacy audit checks explicitly scope to the current design,
    # not an unrelated prose block; unrelated sections remain read-only by policy.
    if not targets:targets={s['id'] for s in value['sections']}
    # A changed/deleted destination can require a corresponding change to the
    # incoming question; expose the directly connected sections, not the graph.
    related=set()
    for section in value['sections']:
        destinations={q['target_section_id'] for q in (section['reader_progression'] or {}).get('next_questions',[]) if q['target_section_id']}
        if section['id'] in targets:related.update(destinations)
        if destinations & targets:related.add(section['id'])
    return sorted(targets | related)


def repair(job_id, keyword, report, artifact, client):
    from .ai import create_with_retry,get_step_config,message_text,tiered_review_enabled
    from .db import upsert_artifact,get_artifact,get_job
    from .research_requirements import load_plan,require_matrix
    from .step_structure_guard import validate_structure
    value=require_projection(artifact);ctx=context(load_plan(job_id),require_matrix(job_id));require_projection(artifact,ctx)
    if report.get('evidence_pending') or any(i['repair_kind']=='source' for i in report.get('blueprint_issues',[])):
        fail('資料不足を構成の書換えで解決できません。確認結果を保持します。')
    job=get_job(job_id)
    targets=repair_targets(artifact,report)
    structural=bool(report.get('structural_issues')) or any(c['key']=='coverage' and c['status']=='fail' and not c.get('requires_source_check') for c in report['checks'])
    from .evidence_policy import REVIEW_RESOLUTION_POLICY
    from .reader_presentation import READER_PRESENTATION_POLICY
    from .generation_context import POLICY as EVIDENCE_POLICY
    model,limit=get_step_config('content_repair')
    request=dict(model=model,max_tokens=min(limit,6000),output_config=repair_config(),
        system=REVIEW_RESOLUTION_POLICY+READER_PRESENTATION_POLICY+EVIDENCE_POLICY+POLICY+'''
今回は局所修正。editsには指摘箇所と同じ誤りを直す節だけを全フィールドで返す。全文再出力は禁止。
既存節のうちtarget_section_ids以外は読取専用。タイトル案・リード・省略理由は維持する。
structural=trueのときだけ関連節の追加・削除・順序・階層変更を許可する。新しい節には未使用IDを付ける。
section_orderは追加・移動・削除が必要なときのみ修正後全IDを順に返し、変更不要なら空配列。delete_idsも不要なら空配列。
主回答の説明と根拠・条件・必須論点を維持し、文字数合わせの水増し・追加調査・未確認の断定をしない。''',
        messages=[{'role':'user','content':json.dumps({'keyword':keyword,'blueprint':value,'accepted_context':ctx,
            'target_section_ids':targets,'structural':structural,'word_count_setting':job.get('word_count_setting'),'failed_checks':[c for c in report['checks'] if c['status']=='fail'],
            'blueprint_issues':report.get('blueprint_issues',[]),'structural_issues':report.get('structural_issues',[])},ensure_ascii=False)}])
    if tiered_review_enabled():
        from .tiered_research import checked_request
        response,_=checked_request(job_id,'outline_blueprint_local_repair_response',request)
    else:
        msg=create_with_retry(client,**request)
        if getattr(msg,'stop_reason',None)!='end_turn':fail('編集設計の局所修正が途中で中断しました。未適用です。')
        response=json.loads(message_text(msg))
    validate_shape(response,repair_config()['format']['schema'])
    candidate=apply_edits(value,response,ctx,structural=structural,targets=targets)
    text=render(candidate,ctx);contract=json.loads(get_artifact(job_id,'content_contract')['content_text'])
    issues=validate_structure(text,contract,outline=True)+budget_issues(candidate,job.get('word_count_setting'))
    if issues:fail('編集設計の修正後も必須構造が不足しています: '+json.dumps(issues,ensure_ascii=False))
    upsert_artifact(job_id=job_id,step='outline_before_local_repair',content_type='text/markdown',
        content_text=artifact['content_text'],meta={**artifact['meta'],'audited':False})
    upsert_artifact(job_id=job_id,step='research_validation',content_type='application/json',
        content_text=json.dumps({'valid':False,'status':'local_repair_requires_recheck'}),meta={'valid':False})
    from .outline_design import topic_assignments
    return upsert_artifact(job_id=job_id,step='outline',content_type='text/markdown',content_text=text,
        meta={**artifact['meta'],**metadata(candidate,ctx),'local_repaired':True,'audited':False,'topic_assignments':topic_assignments(text)})


def budget_issues(value, setting):
    """Only explicit user limits constrain total allocation; no competitor floor."""
    from .article_quality import parse_length_budget
    budget=parse_length_budget(setting)
    if not budget:return []
    total=sum(s['budget']['chars'] for s in value['sections'] if s['level']==2)
    if budget.minimum<=total<=budget.maximum:return []
    return [{'key':'outline_volume','repair_kind':'volume','allocated_chars':total,
        'minimum':budget.minimum,'maximum':budget.maximum,
        'reason':'ユーザー指定の分量と章別配分が不一致。主回答を残し、重複・補足から調整する。'}]


def placement(artifact):
    value=require_projection(artifact)
    if value is None:return None
    targets=[];current=None;instructions={}
    for s in value['sections']:
        if s['level']==2:current=s['title']
        if s['cta'] and current:
            if current not in targets:targets.append(current)
            instructions[current]=(instructions.get(current,'')+' '+s['cta']).strip()
    return {'service_section_type':'integrated' if targets else 'none','primary_h2':targets[0] if targets else '',
        'per_section_instructions':instructions,'cta_after_h2':targets,'reasoning':'承認対象の編集設計から描画',
        'blueprint_version':VERSION,'blueprint_inputs_sha256':artifact['meta']['blueprint_inputs_sha256'],
        'blueprint_sha256':fingerprint(value)}


def validate_progression(value):
    """Check shape and link integrity; explanatory/marketing merit is reviewed."""
    sections=value['sections'];positions={s['id']:i for i,s in enumerate(sections)}
    def single_line(text):
        if '\n' in text or '\r' in text:fail('章ごとの読者の変化は項目ごとに1行で指定してください。')
    for s in sections:
        progression=s['reader_progression']
        if progression is None:
            if s['level']==2:fail('章全体の読者の変化が未設計です。')
            continue
        before,after=progression['before'],progression['after']
        for row in (before,after):
            for val in row.values():
                if isinstance(val,str):single_line(val)
                else:
                    if len(set(val))!=len(val) or any(not item.strip() for item in val):fail('解消する疑問・未解決の問いが空または重複しています。')
                    for item in val:single_line(item)
        if s['level']==2 and not (before['understanding'].strip() or before['open_questions']):fail('章を読む前の理解・問いが不足しています。')
        if not after['resolved_questions'] or not any(after[k].strip() for k in ('understanding','decision','action','emotion')):
            fail('この章が解消する疑問・読後の変化が不足しています。')
        marketing=progression['marketing']
        if marketing is not None:
            if not marketing['role'].strip() or not marketing['mechanism'].strip():fail('記事目的への役割と変化を生む説明が不足しています。')
            for text in marketing.values():single_line(text)
        seen=set()
        for q in progression['next_questions']:
            for text in q.values():single_line(text)
            if not q['question'].strip() or not q['reason'].strip() or q['question'] in seen:fail('次の問いと、その問いが生まれる理由が不足・重複しています。')
            seen.add(q['question']);target=q['target_section_id']
            if q['handling']=='same_section':
                if target!=s['id']:fail('この節で解消する問いの参照先が一致しません。')
            elif q['handling']=='next_section':
                if target not in positions or positions[target]<=positions[s['id']]:fail('次の問いの引継ぎ先が存在しないか、後続の節ではありません。')
            elif target:fail('関連情報・範囲外の問いに記事内の引継ぎ先を混在できません。')


def checked_progression_reviews(value, ctx):
    ids=ctx.get('progression_section_ids',[]);rows=value.get('reader_progression_reviews');issues=[]
    if not isinstance(rows,dict) or set(rows)!=set(ids):fail('章ごとの読者の変化・導線の確認が不足しています。')
    for sid,row in rows.items():
        if (not isinstance(row,dict) or set(row)!={'status','reason'}
            or row['status'] not in ('pass','answer_missing','evidence_missing')
            or not isinstance(row['reason'],str) or not row['reason'].strip()):fail('章ごとの変化・導線の確認結果が不正です。')
        if row['status']!='pass':
            issues.append({'section_id':sid,'section_ids':[sid],'requirement_index':'reader_progression',
                'reason':row['reason'],'repair_kind':'source' if row['status']=='evidence_missing' else 'editorial'})
    return issues
