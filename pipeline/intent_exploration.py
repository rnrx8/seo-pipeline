"""Inspectable editorial hypotheses, not a transcript of model reasoning.

The saved method is separate from the reader-needs format so existing articles
remain usable without being presented as having passed the new analysis method.
"""
import copy
import json

from .content_quality import ContentQualityError

VERSION = 'intent-exploration-v1'
POLICY = '''【検索意図を探索してから、記事で答える問いを選ぶ】
explorationは、読者について考えた編集上の仮説と、それを記事へ使う根拠を記録する。内部の思考実況は書かない。検索語から新たな関係・商品・行動を求める理由を調べ、次の順に、一つの事情として説明できる候補を作る。

1. 語から事情を広げる
KWを対象・行為・条件・評価に分ける。複合語の限定や役割にも着目し、なぜその対象や条件を求めるかを5W1Hで考える。keyword_partsは元KWに実在する連続文字列だけ。固有名を意味のない断片へ分けない。「探し方」「おすすめ」という方法の問いだけで始めない。
入力で確認できない読者の事情も、KWから考えられる候補仮説として具体化できる。contextには候補の内容、unknownsには実際の検索者への該当性が未確認な点を分けて残す。仮説の内容まで「不明」にして掘り下げを止めない。年齢・性別・利用者の多さを決める必要はない。商品・CTAに都合のよい悩みを逆算せず、元の問いから候補を考える。

2. 状況・不足・望む経験をつなぐ
主な背景では、今どんなことが起きているか、そこで本人が何を話す・試す・期待するか、どんな反応や結果があり何が満たされないか、新たな対象にどんな経験を求めるかを、一つの場面として説明する。実際の出来事の記録ではなく、そういう事情の読者を想定した仮説でよい。喪失・失敗がなく前向きな希望から始まる候補も、その体験を求める本人なりの理由を具体化する。
忙しい、会話が少ない、使いにくい等の状況だけから、欲しい・変えたいへ飛ばない。「理解されない」「安心したい」「個人として認められたい」等は、その言葉が指すやり取り・反応・不足を説明して初めて背景の素材になる。何が起きたかと、それを本人がどう受け止めたかを区別する。方法を選べない理由は、対象自体を求める動機とは別の枝にできる。
「それはなぜ」「それで何が変わる」を使い、linksへ関係を短く残す。fromは誰が何を望む・避ける・満たされていないかが分かる命題。whyのtoはその命題が生まれる理由、outcomeのtoはfromの経験を得たときの変化。次に起こる別の障壁や、解決方法への話の移動を原因・結果の答えにしない。便利になる、手間や費用を減らせる、失敗しないという変化からは、それが本人の一日の過ごし方・人との関わり・自分の納得にどんな意味を持つかも検討する。記事の判断を変える関係がある場合は、そのつながりをoutcomeにも具体化する。機能上の不足に対する不快感だけで、望む生活まで理解できたことにしない。前向きな期待と、まだ言葉にしきれていない気がかりの両方を検討し、読者の事情として筋が通る候補だけを残す。根拠の区分はsupportへ書く。

3. 反対・既存の選択肢で確かめる
その人が得たい経験を固定し、なぜ別の対象や今ある相手・関係・手段ではないかを検討する。入手経路の比較だけにしない。alternativeでは候補ごとに満たせる部分と満たされにくい部分を分け、一方だけの理由を別の候補にも適用しない。
既存の選択で満たされない事情を見つけたら、それは対象を求める原因の候補でもある。context・linksへ戻し、同じ事情を持つ一人の読者として背景を組み直す。実読者が既に試した事実とは扱わない。元の望みで別案も満たせるならそれを認め、退けるために別の欲求を後付けしない。原因・得たい経験・代替で満たせる範囲が違う候補はbranchを分ける。属性の一致だけを、共感や満足が得られる理由にしない。

4. 具体的な背景から根源的欲求を整理する
主な動機のうち少なくとも1枝で、不満または希望、得たい経験、それを求める理由のつながりを説明し、生理・安全・所属・承認・自己実現に当たる欲求まで掘る。root_desireは分析上の整理であり、そこへ分類できたこと自体を背景の説明の代わりにしない。meaningには分類が表すこの人の願い、stop_reasonには、本人が大切にしたい具体的な経験・日常と目の前の問いがどうつながり、なぜその深さで記事の判断に十分かを書く。「高額だから失敗したくない→安全」「不便がなく快適に暮らしたい」など、費用・機能の失敗回避や抽象的な暮らしの良さで終了せず、その選択のあとにどんな時間・関係・自分で納得できる状態を得たいかを検討する。関係する事情がない操作・定義まで人生や人間関係へ広げず、これ以上は別の事情の創作になる場合は、その理由を残して止める。
既存の関係や手段で満たされない事情が新たな対象を求める理由に必要なら、そこが抽象語のままでは終了しない。実際の読者にその生活や関係があるか未確認なことと、候補仮説としてその人の望む経験を説明できないことを分ける。日常の行為から考えられる時間の使い方や他者との過ごし方の意味を、未観測だからすべて創作だとして打ち切らない。仮説として具体化し、実読者への該当性はunknownsへ残す。離婚・対立等の事件や深刻な関係悪化を事実として足す必要はない。説明できない部分はunknownとして不足を残す。方法選択・利用条件・単純な操作や定義だけの枝は、感情的な動機を作らずnot_applicableにできる。全枝を同じ深さへ掘る必要はない。

具体化の参考（別KWの仮説例。今回の読者の事実や固定の文章型ではない）：
「iPhoneを買った→大切にしたい→安全の欲求」では、なぜ必要かが伝わらない。「傷のない状態を保ちたい→傷が付くと修理や下取りで出費・損失が生じうる→生活費を減らしたくない」なら、途中の事情を説明に使える。また「なぜAndroidではなくiPhoneか」を考えると、「以前からかわれ疎外感があったので仲間に入りたい」という別の候補も考えられる。これらはユーザーの記事にある分析方法の例であり、今回にも同じ原因を当てはめたり、具体額や実体験を作ったりする指示ではない。

5. 記事へ渡す素材を一つの背景として残す
concrete_materialには、選んだ場面・満たされない事情・望む経験をつないだ短い説明を残す。代替を検討して初めて見つけた原因や、選択後の生活にとっての意味も、同じ事情として採用したものはここへ反映する。感情名・根本欲求の説明・関連語の一覧に置き換えない。article_effectには、この背景によって必要な回答、比較軸、順序、推薦理由や具体例がどう変わるかを書く。深い心理を文章に足すだけでなく、何を選び・確かめる判断が変わるかを示す。完成リードは書かない。
selectedのhypothesis_idsは、その問いの読者事情として採用する枝だけを参照する。reader_situation・background_emotion・desired_stateへ採用した事情を要約しても、対象を求める理由を消さない。desired_stateは望む体験・生活、after_readingはこの記事で進める一歩。対比・定義・対象外の説明だけの候補はexcludedやanalysisへ置き、採用背景に混ぜない。

【記録と根拠の境界】
exploratoryでは通常2〜4枝、最大6枝。異なる背景を残し、単なる補足だけの枝や同じ話の再掲を増やさない。linksは各枝2〜6件が目安。単純な操作・定義・案内で深い心理が回答を変えない場合だけdirectとし、scope_reasonに理由を示す。directもKW分解、必要な文脈と代替の視点は確認する。
links・alternativeは読者について立てた仮説なのでsupport.kind=hypothesis。観測した入力の短い連続引用はsource・quote、その引用から背景を推定した理由はreasonへ分ける。sourceはkeyword・serp・request、または空文字。入力にない原因の候補はsourceとquoteを空文字にし、仮説として考えた理由を書く。引用があっても、読者本人の心理や因果関係を確認済みにはしない。全読者の事情・人数・体験・商品機能を推測で事実化しない。
出力前に、各枝のcontext・links・alternative・concrete_materialが同じ事情と望みを説明しているか確認する。代替で見つけた背景だけが脇の欄に残り、採用素材が元の抽象的な説明のままなら統合し直す。分類名、形式の充足、繰り返す留保で説明不足を埋めない。analysisは採用した背景の要点と記事への使い方を簡潔に示し、探索やselectedの全欄を再掲しない。'''


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
    motivation_ids = {b['id'] for b in branches if isinstance(b, dict)
                      and b.get('root_desire', {}).get('category') in
                      ('physiological', 'safety', 'belonging', 'esteem', 'self_actualization')
                      and ('selected' not in value or b.get('id') in selected_ids)
                      and isinstance(b.get('links'), list) and len(b['links']) >= 2}
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
        supporting_condition = (branch.get('root_desire', {}).get('category') == 'not_applicable'
                                and bool(motivation_ids - {branch['id']}))
        minimum = 2 if mode == 'exploratory' and adopted and not supporting_condition else 1
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
                 root_desire=copy.deepcopy(b['root_desire']),
                 concrete_material=b['concrete_material'], article_effect=b['article_effect'],
                 unknowns=copy.deepcopy(b['unknowns']), status='editorial_hypothesis_not_verified')
            for b in exploration['branches'] if b['id'] in selected]
