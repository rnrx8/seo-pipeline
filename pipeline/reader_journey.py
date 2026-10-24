"""Evidence for receiving a chapter's reader state and guiding the next step."""
import copy
import re

PLANNING_POLICY = '''【章の読者理解から回答までのつながり】
ユーザーが感情を指定したかどうかにかかわらず、その章へ来た時点の理解・状況から、何を心配し、何に戸惑い、何を期待するかを考える。妥当な気持ちは複数あり、一つの例文や語句へ合わせない。before.emotionは仮説として具体化し、その気持ちをどう言葉にして受け止め、どの回答によって次の理解・判断・行動へ進めるかをanswer_elementsへ設計する。感情欄だけに名前を書く設計では不足。
conclusionは、その人がどう考え、何を確かめ、どう進めればよいかが分かる答えにする。比較の制約や「判断できない」という留保が必要でも、採用根拠の範囲で示せる進み方まで説明する。調査上の注意は読者の判断に必要な範囲へ絞り、その説明が章の主回答を覆わないようにする。前後の章に説明済み・後続で扱う内容を増やして、この章の受け止めと回答を薄めない。
受け止めと回答の順序・文数・位置は章に合わせる。短い条件句と回答を同じ文にしてもよい。料金・定義など明確な回答自体で完結する節は、その回答で進める理由を持ち、定型的な共感文を足さない。'''

WRITING_POLICY = '''【読者の実感から次の一歩へ】
章の状況と問いから、読者が何を気にし、どう感じているかを自分の言葉で捉える。設計の心理語やユーザー例文の再現ではなく、その読者が自分のこととして受け取れる短い状況・実感を、章の回答へつなぐ。気持ちの名前、客観的な制約、具体策の有用性は、それぞれ別のものである。受け止めを必要とする章では、何に迷う・ためらう・期待するかが伝わり、その気持ちに応える進み方が分かる文章にする。
主回答を早く示し、理由・条件は必要な範囲で支える。注意の列挙だけで判断を読者へ戻さず、この章で分かったことを使って何を考え・確かめればよいかまで案内する。型・感情語・共感の位置は固定しない。'''

REVIEW_POLICY = '''【章ごとの受け止めと案内の独立確認】
reader_progression_reviewsのjourneyへ、reader_state（この章を読む時点の具体的な事情と気持ち）、attunement（それを言語化して受け止める働き）、guidance（受け止めた状態から次の理解・判断・行動へ進める働き）を分けて記録する。ユーザーから指摘があった感情や設計のemotionの語句との一致を合格基準にしない。前章で理解したこと、今の問い、本文・設計の内容から、その章に妥当な気持ちを独立に判断する。
attunementは、その人がその状況をどう受け取っているかを表す実際の記述、構成ではそれを伝えるanswer_elementsの指示をevidenceに示す。心理ラベルの存在、問題や制約の説明、時間・費用の条件、手順の有用性だけで受け止めも成立したとしない。感情語そのものは不要だが、引用とreasonから読者の実感が伝わる理由が分かること。受け止めが不要な定義・料金・直接回答ではnot_neededと具体的な理由を返せる。
guidanceは、その気持ちに対して、この章の結論を使い何を理解・判断・実行すればよいかが分かるかを独立に判定する。一覧や手順が存在するだけでpassにせず、何を勧めるかが埋もれたり、注意点や「判断できない」という留保で読者が止まったりしていないか確認する。後続章で答える問いをすべてこの章で解決する必要はないが、この章で進めたことと次の問いのつながりを読む。不要な詳説・重複で主回答が見えなくなる場合は不足として扱う。
各evidenceは提示された章内のidと、その箇所に実在する短いquote。構成では結論・回答要素の指示、本文では実際の文章を引用する。before/afterの心理欄だけを構成の受け止め実現の根拠にしない。欠落で引用できない場合はfailとし、reasonに何がなくどう困るかを書く。具体策の合格でattunementのfailを相殺せず、感情表現の合格でguidanceのfailを相殺しない。'''


def schema():
    def obj(fields):return {'type':'object','properties':fields,'required':list(fields),'additionalProperties':False}
    string={'type':'string'}
    evidence={'type':'array','items':obj({'id':string,'quote':string})}
    def check(states):return obj({'status':{'type':'string','enum':states},'reason':string,'evidence':evidence})
    return obj({'reader_state':string,'attunement':check(['pass','fail','not_needed']),
                'guidance':check(['pass','fail'])})


def checked(value, evidence):
    """Validate actual quoted addresses; semantic merit remains an LLM judgment."""
    from .content_quality import ContentQualityError
    def fail():raise ContentQualityError('章の受け止め・次の一歩の個別確認または引用が不整合です。')
    if not isinstance(value,dict) or set(value)!={'reader_state','attunement','guidance'}:fail()
    if not isinstance(value['reader_state'],str) or not value['reader_state'].strip():fail()
    normalize=lambda text:re.sub(r'\s+',' ',text).strip()
    missing=[]
    for name,states in [('attunement',('pass','fail','not_needed')),('guidance',('pass','fail'))]:
        row=value[name]
        if (not isinstance(row,dict) or set(row)!={'status','reason','evidence'} or row['status'] not in states
            or not isinstance(row['reason'],str) or not row['reason'].strip() or not isinstance(row['evidence'],list)):fail()
        if row['status']=='pass' and not row['evidence']:fail()
        for loc in row['evidence']:
            if (not isinstance(loc,dict) or set(loc)!={'id','quote'} or loc['id'] not in evidence
                or not isinstance(loc['quote'],str) or not loc['quote'].strip()
                or not any(normalize(loc['quote']) in normalize(text) for text in evidence[loc['id']])):fail()
        if row['status']=='fail':missing.append(('気持ちの受け止め' if name=='attunement' else '次の理解・判断・行動への案内')+'：'+row['reason'])
    return missing


def outline_evidence(sections):
    return {s['id']:{'parent_id':s['parent_id'],'texts':[s['conclusion']]+[e['instruction'] for e in s['answer_elements']]}
            for s in sections}


def chapter_evidence(sid, sections):
    ids={sid}
    for key,row in sections.items():
        if row['parent_id'] in ids:ids.add(key)
    return {key:copy.deepcopy(sections[key]['texts']) for key in ids if key in sections}
