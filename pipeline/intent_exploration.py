"""Inspectable editorial hypotheses, not a transcript of model reasoning.

The saved method is separate from the reader-needs format so existing articles
remain usable without being presented as having passed the new analysis method.
"""
import copy
import json

from .content_quality import ContentQualityError

VERSION = 'intent-exploration-v1'
POLICY = '''【検索意図を探索してから、記事で答える問いを選ぶ】
次の手順を同じ主分析の中で実施し、explorationに簡潔な編集上の仮説・関係を記録する。内部の思考実況や長い独白は出力しない。
1. KWを、対象・行為・条件や修飾・評価を表す意味のある単語へ分ける。複合語の中に役割や限定を表す語がある場合は、それも分け、なぜその条件を付けて対象を求めるかを考える。固有の商品名や意味を失う分割はしない。「おすすめ」「探し方」だけから始めず、対象そのものと、その条件を選んだ理由から探索する。keyword_partsは元KWに実在する連続文字列だけ。
2. 各語について5W1H（いつ・どこで・誰が・何を・なぜ・どうする）から事情ときっかけを広げる。KWの意味や入力から説明できる範囲で、実態が未確認の事情も候補仮説として考える。原因が一つに決まらない場合は、異なる事情を別のbranchとして残し、同じ人の全事情へ合成しない。仮説として設定した5W1Hはcontextへ書き、実際の検索者にその事情が当てはまるかはunknownsへ残す。候補としても説明できない軸だけを空文字にする。
3. 方法を問うKWでも、「方法が分からない理由」と「そもそもその対象・関係・行為を求める理由」を分け、主な背景の枝では後者から掘る。具体的な事情から、何が満たされず本人にとってなぜ不満なのか、どんな状態・経験を得られれば満たされるのか、それをなぜ求めるのかを「それはなぜ」「それで何が変わる」でたどり、linksへ簡潔に残す。前向きに得たい経験から始まる場合も、その人にとっての意味をたどり、属性だけから不満を作らない。
各リンクはfromに対する問いとtoの関係を表す。whyは直前の事情・不満・望みが生まれる理由、outcomeはそれによって得られる変化を説明し、問いと答えの論点を揃える。次に生じる別の障壁を、直前の望みの原因として答えない。感情や経験として得たいものと、それを得るための相手・商品・方法の条件を分ける。条件の利点を説明できても、何が満たされるかの説明の代わりにはならない。根拠・仮説はsupport、比較・推薦への使い方はarticle_effectへ残す。
固定の回数ではなく、具体的な不満・得たい経験・求める理由のつながりを説明しながら、マズローの生理・安全・所属・承認・自己実現に当たる根源的欲求まで掘る。root_desire.meaningでは、その分類がこの人のどんな不足・願いを表すのかを説明する。分類できたこと、理解者や安心が欲しいと言い換えられたこと、記事の判断が変わらないことだけでは終了しない。stop_reasonには、具体的な不満と望みの中身、その必要性がつながり、これ以上の理由が根源的欲求そのものになると判断した根拠を示す。仮説としても説明できない点はunknownとして不足を記す。実読者への該当性が未確認なことと、候補仮説の不満・欲求が曖昧なことは区別する。操作の直接回答に深層心理が関係しない場合はnot_applicableとして理由を示す。
4. 反対・代替の視点から、なぜ別の対象や既存の手段ではないのかも検討し、alternativeへ候補と違いを残す。入手経路どうしの比較だけで終わらず、新たな対象や関係を求めずに既存の状態・関係・手段で満たす選択も、KWの限定や役割に応じて検討する。比較する前に、その人が得たい体験を具体化する。その体験のどの部分を別案では満たしにくいか、仮説として説明する。特定の属性が必要と考える場合は、その属性があることで具体的に何が満たされるかを掘る。属性を変えても同じ体験を得られる説明なら、属性を根本の欲求に含めず、条件の候補として扱う。関係や方法の名称・属性が違うことだけを、欲求を満たせない理由にしない。その事情の該当性が未確認であることと、推定理由をsupport.reasonとunknownsに残す。仮説としても説明できなければ不明とし、利用者全員の不満・経験として確定しない。商品やCTAの指定から背景を逆算しない。
5. 根源的欲求の名前ではなく、途中で見つけた具体的な事情・得たい体験・生活への影響をconcrete_materialへ残す。article_effectには、その理解で回答・比較軸・説明順序・推薦・具体例がどう変わるかを書く。数字・体験・多数派の実態を具体性のために創作しない。説明用の場面は場面例と分かるようにする。
6. selectedのhypothesis_idsは、その問いで採用する読者事情のbranchだけを参照する。対比・定義・対象外の説明にだけ使うbranchは、analysisやexcludedで扱い、採用読者の背景として結ばない。reader_situation・background_emotion・desired_state・barrierは、採用したbranchの文脈に沿ってまとめる。問いへの直接回答と、欲求が生まれた背景の両方を残し、似た仮説をまとめても具体的な不満・得たい体験を消さない。その事情を持つ読者への仮説として保ち、同じ属性の人全員がその対象や関係を求めるという説明へ広げない。desired_stateは望む生活・体験、after_readingはこの記事で進める一歩として区別する。
exploration.modeは通常exploratory。単純な操作・定義・案内で深い感情が答えを変えない場合だけdirectとし、scope_reasonに理由を残す。directでもKW分解、必要な文脈、代替の視点は確認する。exploratoryは通常2〜4枝、最大6枝。linksは各枝2〜6件を目安に、directは必要な関係だけでよい。事情の候補は広く検討し、保存する枝は異なる不満・得たい体験・理由を持つものに絞る。対象外・定義補足だけの候補はexcludedで短く扱い、全候補の詳細を埋めるための枝を作らない。
探索のlinksとalternativeは、この記事の読者について立てる設計仮説なので、support.kindはhypothesisとする。入力で観測した原文はsource・quote、そこから読者の事情や関係を推定した理由と限界はreasonへ分ける。SERPがその心理や利点を述べていても、検索者本人の事情・因果関係が確認できたことにはならない。引用は入力keyword・serp・requestの短い連続引用に限り、同じ単語の存在だけで関係・差異全体を裏付けない。機能の有無や利用条件などの未確認事項はunknownsへ残し、推測で確定しない。出典のある原文を保持することと、設計仮説を検証済みにすることを区別する。
出力前に、語の分解→複数の事情→理由/結果→根源的欲求→代替視点→具体的素材→記事への使い方を確認する。形式が埋まることと、背景の説明が十分であることを同一視しない。各欄は判断に必要な要点を残す。support.reasonはその関係を推定した理由を短く書き、読者への該当性など共通の限界はbranchのunknownsへまとめる。不満・得たい体験・理由のつながりを削って短くせず、同じ背景や一般的な留保を各欄で繰り返さない。analysisは採用した背景の要点と記事への影響を示し、探索記録の全文を再掲しない。'''


def obj(fields):
    return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}


def arr(item):
    return {'type': 'array', 'items': item}


STR = {'type': 'string'}
SUPPORT = obj({'kind': {'type': 'string', 'enum': ['observed', 'hypothesis']},
               'source': {'type': 'string', 'enum': ['', 'keyword', 'serp', 'request']},
               'quote': STR, 'reason': STR})
BRANCH = obj({'id': STR, 'keyword_parts': arr(STR),
              'context': obj({k: STR for k in ('when', 'where', 'who', 'what', 'why', 'how')}),
              'links': arr(obj({'from': STR, 'question': {'type': 'string', 'enum': ['why', 'outcome']},
                               'to': STR, 'support': SUPPORT})),
              'root_desire': obj({'category': {'type': 'string', 'enum': [
                  'physiological', 'safety', 'belonging', 'esteem', 'self_actualization', 'unknown', 'not_applicable']},
                                 'meaning': STR, 'stop_reason': STR}),
              'alternative': obj({'option': STR, 'difference': STR, 'support': SUPPORT}),
              'concrete_material': STR, 'article_effect': STR, 'unknowns': arr(STR)})
SCHEMA = obj({'version': {'type': 'string', 'enum': [VERSION]},
              'mode': {'type': 'string', 'enum': ['exploratory', 'direct']},
              'scope_reason': STR, 'keyword_parts': arr(STR), 'branches': arr(BRANCH)})


def model_schema():
    """Encode this nested subdocument as JSON text to bound wire grammar size.

    The canonical shape is still enforced locally before saving/handoff. Even
    unconstrained nested objects exceeded the provider's grammar limit when
    combined with reader-needs; a string avoids multiplying those grammars.
    """
    schema = copy.deepcopy(SCHEMA)
    properties = schema['properties']['branches']['items']['properties']
    for support in (properties['links']['items']['properties']['support'], properties['alternative']['properties']['support']):
        support['properties']['kind']['enum'] = ['hypothesis']
    return {'type': 'string', 'description':
            'JSON-encoded exploration object. Serialize the full object as this string; do not omit fields. '
            'It must match this JSON schema: ' + json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}


def _shape(value, schema):
    """Validate the small supported schema vocabulary without a new dependency."""
    kind = schema['type']
    if kind == 'object':
        if not isinstance(value, dict) or set(value) != set(schema['properties']):
            _fail('探索オブジェクトの必須欄・余分な欄を確認してください。')
        for key, child in schema['properties'].items(): _shape(value[key], child)
    elif kind == 'array':
        if not isinstance(value, list): _fail('探索配列の形式が不正です。')
        for item in value: _shape(item, schema['items'])
    elif kind == 'string':
        if not isinstance(value, str): _fail('探索テキストの形式が不正です。')
        if 'enum' in schema and value not in schema['enum']: _fail('探索の区分が不正です。')


def decode(value):
    """Normalize wire encoding only; never edit generated wording or conclusions."""
    if isinstance(value.get('exploration'), str):
        try: value['exploration'] = json.loads(value['exploration'])
        except ValueError as error: _fail('探索のJSONテキストを読み取れません。')
    _shape(value.get('exploration'), SCHEMA)
    return value


def _fail(detail):
    raise ContentQualityError('検索意図の探索記録が不十分です: ' + detail)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _support(ref, sources):
    if not isinstance(ref, dict) or ref.get('kind') not in ('observed', 'hypothesis') or not _text(ref.get('reason')):
        _fail('関係の根拠・仮説区分がありません。')
    source, quote = ref.get('source'), ref.get('quote')
    if source not in ('', 'keyword', 'serp', 'request') or not isinstance(quote, str):
        _fail('入力にない根拠を参照しています。')
    if quote and (not source or quote not in sources.get(source, '')):
        _fail('引用が入力資料にありません。')
    if ref['kind'] == 'observed' and (not source or not quote.strip()):
        _fail('観測には入力の引用が必要です。')


def validate(value, sources, *, require_hypothesis_relations=False):
    """Check completeness/provenance/links, never certify semantic depth."""
    decode(value)
    exploration = value.get('exploration')
    if not isinstance(exploration, dict) or exploration.get('version') != VERSION:
        _fail('現方式の探索結果がありません。')
    mode = exploration.get('mode')
    if mode not in ('exploratory', 'direct') or not _text(exploration.get('scope_reason')):
        _fail('探索範囲と理由がありません。')
    parts = exploration.get('keyword_parts')
    if (not isinstance(parts, list) or not parts or len(parts) > 12
            or any(not _text(p) or p not in sources.get('keyword', '') for p in parts)
            or len(set(parts)) != len(parts)):
        _fail('KWの分解が元の語句と一致しません。')
    branches = exploration.get('branches')
    if not isinstance(branches, list) or not 1 <= len(branches) <= 6:
        _fail('仮説の枝が不足または過大です。')
    selected_ids = {ref for need in value.get('selected', [])
                    for ref in need.get('hypothesis_ids', []) if isinstance(ref, str)}
    ids, used_parts = set(), set()
    for branch in branches:
        if not isinstance(branch, dict) or not _text(branch.get('id')) or branch['id'] in ids:
            _fail('仮説IDが不足・重複しています。')
        ids.add(branch['id'])
        refs = branch.get('keyword_parts')
        if not isinstance(refs, list) or not refs or any(p not in parts for p in refs):
            _fail('分解した語と仮説が結ばれていません。')
        used_parts.update(refs)
        context = branch.get('context')
        if (not isinstance(context, dict) or any(not isinstance(context.get(k), str) for k in
                ('when', 'where', 'who', 'what', 'why', 'how')) or not _text(context.get('what'))):
            _fail('5W1Hの文脈がありません。')
        unknowns = branch.get('unknowns')
        if not isinstance(unknowns, list) or any(not _text(u) for u in unknowns):
            _fail('不明点の形式が不正です。')
        if any(not context[k].strip() for k in context) and not unknowns:
            _fail('文脈の不明点が記録されていません。')
        links = branch.get('links')
        adopted = 'selected' not in value or branch['id'] in selected_ids
        minimum = 2 if mode == 'exploratory' and adopted else 1
        if not isinstance(links, list) or not minimum <= len(links) <= 6:
            _fail('理由・結果のつながりが不足または過大です。')
        for link in links:
            if (not isinstance(link, dict) or not _text(link.get('from')) or not _text(link.get('to'))
                    or link.get('question') not in ('why', 'outcome') or link['from'].strip() == link['to'].strip()):
                _fail('理由・結果の関係が空または同語反復です。')
            _support(link.get('support'), sources)
            if require_hypothesis_relations and link['support']['kind'] != 'hypothesis':
                _fail('引用があっても読者の背景・欲求の関係は設計仮説として扱ってください。')
        root = branch.get('root_desire')
        if (not isinstance(root, dict) or root.get('category') not in
                SCHEMA['properties']['branches']['items']['properties']['root_desire']['properties']['category']['enum']
                or not _text(root.get('stop_reason')) or not isinstance(root.get('meaning'), str)):
            _fail('根源的欲求と探索を止める理由がありません。')
        if root['category'] not in ('unknown', 'not_applicable') and not _text(root['meaning']):
            _fail('欲求の分類名だけで内容がありません。')
        alternative = branch.get('alternative')
        if (not isinstance(alternative, dict) or not _text(alternative.get('option'))
                or not _text(alternative.get('difference'))):
            _fail('代替の視点がありません。')
        _support(alternative.get('support'), sources)
        if require_hypothesis_relations and alternative['support']['kind'] != 'hypothesis':
            _fail('引用の観測と、読者が代替を選ぶ理由の仮説を区別してください。')
        if any(not _text(branch.get(k)) for k in ('concrete_material', 'article_effect')):
            _fail('具体的素材と記事への使い方がありません。')
    if used_parts != set(parts):
        _fail('分解した語の一部が探索から抜けています。')
    for need in value.get('selected', []):
        refs = need.get('hypothesis_ids')
        if (not isinstance(refs, list) or not refs or any(not isinstance(r, str) or r not in ids for r in refs)
                or len(set(refs)) != len(refs)):
            _fail('採用した問いと探索仮説が結ばれていません。')
    return exploration


def for_need(exploration, need):
    """Retain concrete motivation links, without raw quotes or unused branches."""
    selected = set(need['hypothesis_ids'])
    return [dict(hypothesis_id=b['id'], context=copy.deepcopy(b['context']),
                 motivation_links=[{k: link[k] for k in ('from', 'question', 'to')} for link in b['links']],
                 alternative={k: b['alternative'][k] for k in ('option', 'difference')},
                 desired_experience=b['root_desire']['meaning'],
                 concrete_material=b['concrete_material'], article_effect=b['article_effect'],
                 unknowns=copy.deepcopy(b['unknowns']), status='editorial_hypothesis_not_verified')
            for b in exploration['branches'] if b['id'] in selected]
