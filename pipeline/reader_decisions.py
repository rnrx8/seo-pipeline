"""Plan-only adoption of the shared editorial decision model; no added model calls."""
import copy

ROLES = ('primary', 'prerequisite', 'downstream', 'supporting')

POLICY = '''【読者の判断設計】
reader_decisionsは記事で解消する判断・理解・手順と、その前後関係。reader_needsがある場合は採用した問いごとに1件とし、そのIDとquestionを保持する。役割は記事で重要かどうかではなく、読者の検索への問いとの関係で決める。primaryは検索語と検索意図に対する直接回答。prerequisiteはその回答を理解・判断するために先に必要な前提。downstreamは主回答で選択・理解した後に具体化する判断や行動。supportingは主回答の前提や次の行動には当たらない、採用した関連補足。補足の関連先はdepends_onで示せるが、その補足のために主回答の理解・選択が済んでいることを要求しない。CV目的や推奨商材の指定だけでdownstreamをprimaryにしない。検索自体が複数の主質問を含む場合はprimaryが複数でもよい。背景・潜在欲求は判断軸の理由であり、独立した調査義務や全章共通の共感文ではない。
まずクエリへの問いを保ち、何を選ぶ/理解する/実行できれば回答になるかをanswer_requirementsで具体化する。answer_requirementsはその判断の成立に必要な回答だけで構成し、関連する補足まで完了条件へ抱き合わせない。推奨目的があっても候補選びを契約手続き全体の解説へ拡張しない。比較候補の掲載と、全候補で全比較軸を揃える義務は区別する。ユーザーが指定した比較対象・項目は維持する。複数手段から選ぶ問いでは、どの条件ならどの方法を勧めるかと理由もprimaryの必要回答に含める。方法の推奨までdownstreamへ追いやらない。選んだ手段の中で個別商品を選ぶ問いは別の判断であり、CV指定だけで上位の回答を済んだ前提にしない。採用した問いが選定基準と商品選択を含む場合、同じIDのscopeとanswer_requirementsで内訳と順序を示し、構成ではそれぞれの問いを担う章へ分ける。章の役割を分けることと、分析の採用IDを分割・追加することを混同しない。商品比較そのものが主題ならprimaryでよく、単一の操作・定義に不要な比較を追加しない。前提知識と補足の疑問も区別し、主回答に先行する必要のない注意・専門解説をprerequisiteへまとめない。
各判断に必要な調査IDをresearch_idsへ、先に解消すべき判断をdepends_onへ指定する。supportingの場合は補足が関連する判断を指定し、主回答が補足を必須前提とする逆向きの依存にはしない。選定済みの少数の判断だけを記録し、分析中の全連想語を判断にしない。件数・文字数や均等な推薦を要件にせず、判断に必要な違い・条件・理由を具体化する。調査のverifiedは根拠の正しさであり、この回答範囲の充足とは別。下位の商品説明・名前の列挙・質問IDの割当てだけで上位の比較/理解を完了としない。
構成の既存の「読者の疑問」「回答要素」「対応論点」をこの判断に対応させ、見出しの階層と説明順序を設計する。本文も同じ判断と前後関係を引き継ぐ。新しい説明欄・固定のH3件数を増やす指示ではない。
'''

from .authoring_policy import SELECTION_POLICY, SECTION_SCOPE_POLICY
POLICY += SELECTION_POLICY + SECTION_SCOPE_POLICY

REVIEW_POLICY = '''確認はdecision_reviewsで全判断を個別に判定する。answer_requirementsは配列の0始まりの番号で照合し、不足をmissing_requirementsへ記録する。status=passは全要素に実質的な回答がある場合だけ。根拠確認・商材紹介・一覧の存在を理由に不足を相殺しない。supportingの合理的な省略は主要な判断の不足と区別し、省略しても主要な回答と結論が成立する場合はその補足の不足だけで全体を不合格にしない。掲載した主張の根拠不足や誤りは通常どおり別の根拠確認で判定する。reasonには実際の回答または不足を示し、下位の推薦へ進めるかを判断する。'''


PLAN_REVIEW_POLICY = '''確認は調査前の計画確認。decision_design_checksで各判断の役割を入力のクエリ・検索意図から独立にexpected_roleへ分類し、reasonにその判断が直接回答・必要な前提・回答後の具体化・関連補足のどれに当たるかを示す。入力のroleや依存順序を転記せず、役割分類では記事目的や商材指定を外しても同じ直接回答になるかを照合する。ただし、この分類確認を理由に、必要回答から記事目的に沿うカテゴリの推薦方針や商材への接続を削らない。推薦の方向と、primary/downstreamの役割分類は別に評価する。複数primaryを件数だけで不合格にしない。採用済みの複合の問いは、内訳の回答・順序と構成で分けるべき章の役割を確認し、IDの追加・分割を要求しない。
decision_design_checksのunnecessary_requirementsには、その判断の成立に不要な補足や過剰な固定条件を抱き合わせたanswer_requirementsの0始まり番号を返す。全要素を検索意図・記事目的・ユーザー指定から個別に照合し、既存計画に書かれていることや質問があることを必要性の根拠にしない。reasonには役割と回答範囲の妥当性を説明し、過剰な要素がある場合は該当番号と理由を示す。取得失敗を見て必要範囲を削る判定ではない。
decision_reviewsは、それぞれのanswer_requirementsを調べる質問が設計されているかを評価する。回答事実の取得充足はまだ判定しない。failのresearch_idsには不足する既存の質問IDを指定する。判断の分類が違うことと調査質問の不足は別で、分類・前後関係・回答範囲の問題はissuesへ記録する。分類だけの問題をmissing_requirementsへ無理に割り当てたり、質問が揃っていることを分類の合格理由にしない。'''


def plan_review_schema(schema, decisions):
    result=review_schema(schema,decisions)
    if not decisions:return result
    root=result['format']['schema']
    check={'type':'object','properties':{
        'expected_role':{'type':'string','enum':list(ROLES)},
        'reason':{'type':'string','minLength':1}},
        'required':['expected_role','reason','unnecessary_requirements'],'additionalProperties':False}
    check['properties']['unnecessary_requirements']={'type':'array','items':{'type':'integer','minimum':0}}
    root['properties']['decision_design_checks']={'type':'object',
        'properties':{d['id']:copy.deepcopy(check) for d in decisions},
        'required':[d['id'] for d in decisions],'additionalProperties':False}
    root['required'].append('decision_design_checks')
    return result


def validate_plan_design_checks(value, decisions, *, require_scope_checks=True):
    if not decisions:return
    checks=value.get('decision_design_checks')
    if not isinstance(checks,dict) or set(checks)!={d['id'] for d in decisions}:
        fail('読者の判断の役割を独立確認した記録が不足しています。')
    for d in decisions:
        check=checks[d['id']]
        if (not isinstance(check,dict) or check.get('expected_role') not in ROLES
            or not isinstance(check.get('reason'),str) or not check['reason'].strip()):
            fail('読者の判断の役割確認が不正です。')
        excess=check.get('unnecessary_requirements')
        if require_scope_checks or excess is not None:
            if (not isinstance(excess,list)
                or any(type(i) is not int or not 0<=i<len(d['answer_requirements']) for i in excess)
                or len(set(excess))!=len(excess)):
                fail('読者の判断に抱き合わせた不要な完了条件の確認が不足・不正です。')
            if excess:
                value['valid']=False
                value['issues'].append({'id':'overall','reason':
                    f"判断{d['id']}の回答範囲に不要な完了条件{excess}：{check['reason']}"})
        if check['expected_role']!=d['role']:
            value['valid']=False
            value['issues'].append({'id':'overall','reason':
                f"判断{d['id']}「{d['question']}」の役割が不一致：{d['role']}→{check['expected_role']}。{check['reason']}"})


SCHEMA = {'type':'array','minItems':1,'items':{'type':'object','properties':{
    'id':{'type':'string'},'question':{'type':'string'},
    'role':{'type':'string','enum':list(ROLES)},
    'scope':{'type':'string'},'depends_on':{'type':'array','items':{'type':'string'}},
    'research_ids':{'type':'array','minItems':1,'items':{'type':'string'}},
    'answer_requirements':{'type':'array','minItems':1,'items':{'type':'string'}}},
    'required':['id','question','role','scope','depends_on','research_ids','answer_requirements'],'additionalProperties':False}}


def output_schema(decisions=()):
    from .editorial_contract import CURRENT
    result = copy.deepcopy(SCHEMA)
    if CURRENT.get() or any('editorial_notes' in d for d in decisions):
        item = result['items']
        item['properties']['editorial_notes'] = {'type':'array','items':{'type':'string'},
            'description':'AIの編集提案や内部確認。本文に答える必須情報ではなく、ユーザー指定へ昇格させない。'}
        item['required'].append('editorial_notes')
        item['properties']['answer_requirements']['description'] = (
            '読者がこの問いを理解・判断するための回答内容。利用対象・料金期間等の実条件は保つ。'
            '語調や保証否定などの内部注意はeditorial_notesに分け、必須回答に含めない。')
    return result


def fail(message):
    from .content_quality import ContentQualityError
    raise ContentQualityError(message)


def validate(decisions, questions):
    if not isinstance(decisions,list) or not 1<=len(decisions)<=8:
        fail('読者の判断設計が不足または過大です。')
    ids=[d.get('id') for d in decisions if isinstance(d,dict)]
    if len(ids)!=len(decisions) or any(not isinstance(i,str) or not i.strip() for i in ids) or len(set(ids))!=len(ids):
        fail('読者の判断IDが不足・重複しています。')
    qids={q['id'] for q in questions};by_id={d['id']:d for d in decisions}
    for d in decisions:
        if any(not isinstance(d.get(k),str) or not d[k].strip() for k in ('question','scope')) or d.get('role') not in ROLES:
            fail('読者の判断範囲・役割が不正です。')
        for field,allowed in [('depends_on',set(ids)),('research_ids',qids)]:
            refs=d.get(field)
            if not isinstance(refs,list) or any(not isinstance(i,str) or i not in allowed for i in refs) or len(refs)!=len(set(refs)):
                fail('読者の判断の参照先が不正です。')
        req=d.get('answer_requirements')
        if not d['research_ids'] or not isinstance(req,list) or not req or any(not isinstance(r,str) or not r.strip() for r in req):
            fail('読者の判断に必要な回答・調査が不足しています。')
    primary={d['id'] for d in decisions if d['role']=='primary'}
    if not primary:fail('検索への主回答が判断設計にありません。')
    def ancestors(i,trail):
        if i in trail:fail('読者の判断の前後関係が循環しています。')
        return {i}|{a for p in by_id[i]['depends_on'] for a in ancestors(p,trail|{i})}
    for d in decisions:
        parents=ancestors(d['id'],set())-{d['id']}
        if d['role']=='downstream' and not parents & primary:
            fail('記事目的の判断が検索への主回答につながっていません。')
        if d['role']=='primary' and any(by_id[i]['role'] in ('downstream','supporting') for i in parents):
            fail('検索への主回答が下位の商材推薦を前提にしています。')
    return decisions


def review_schema(schema, decisions, *, locations=False):
    if not decisions:return schema
    result=copy.deepcopy(schema);root=result['format']['schema']
    entries={}
    for d in decisions:
        props={'status':{'type':'string','enum':['pass','fail']},'reason':{'type':'string','minLength':1},
               'missing_requirements':{'type':'array','items':{'type':'integer','minimum':0,'maximum':len(d['answer_requirements'])-1}}}
        if locations:
            props['affected_blocks']={'type':'array','items':{'type':'object','properties':{
                'id':{'type':'string'},'reason':{'type':'string','minLength':1}},'required':['id','reason'],'additionalProperties':False}}
        else:
            props['research_ids']={'type':'array','items':{'type':'string','enum':d['research_ids']}}
        entries[d['id']]={'type':'object','properties':props,'required':list(props),'additionalProperties':False}
    root['properties']['decision_reviews']={'type':'object','properties':entries,'required':list(entries),'additionalProperties':False}
    root['required'].append('decision_reviews')
    return result


def checked_reviews(value, decisions, *, block_ids=None):
    rows=value.get('decision_reviews')
    if not isinstance(rows,dict) or set(rows)!={d['id'] for d in decisions}:
        fail('読者の判断ごとの充足確認が不足しています。')
    for d in decisions:
        r=rows[d['id']]
        if not isinstance(r,dict):fail('読者の判断の確認形式が不正です。')
        missing=r.get('missing_requirements')
        if (r.get('status') not in ('pass','fail') or not isinstance(r.get('reason'),str) or not r['reason'].strip()
            or not isinstance(missing,list) or any(type(i) is not int or not 0<=i<len(d['answer_requirements']) for i in missing)
            or len(set(missing))!=len(missing) or (r['status']=='pass') != (not missing)):
            fail('読者の判断の充足と不足要素が一致しません。')
        if block_ids is not None:
            locs=r.get('affected_blocks')
            if (not isinstance(locs,list) or (r['status']=='pass' and bool(locs)) or (r['status']=='fail' and d['role']!='supporting' and not locs)
                or any(not isinstance(l,dict) or l.get('id') not in block_ids or not isinstance(l.get('reason'),str) or not l['reason'].strip() for l in locs)):
                fail('読者の判断の不足箇所が特定されていません。')
        else:
            refs=r.get('research_ids')
            if not isinstance(refs,list) or any(q not in d['research_ids'] for q in refs) or (r['status']=='fail' and not refs):
                fail('判断の不足に対応する調査が特定されていません。')
    return rows


def research_result(value, decisions):
    if not decisions:return value
    rows=checked_reviews(value,decisions);result=copy.deepcopy(value)
    for d in decisions:
        r=rows[d['id']]
        if r['status']=='fail' and d['role']!='supporting':
            result['coverage_sufficient']=False
            for q in r['research_ids']:
                issue={'id':q,'reason':d['question']+'：'+r['reason'],'repair_kind':'editorial'}
                if issue not in result['coverage_issues']:result['coverage_issues'].append(issue)
    return result
