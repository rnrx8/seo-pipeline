"""Evidence for receiving a chapter's reader state and guiding the next step."""
import copy
import re

PLANNING_POLICY = '''【章の読者理解から回答までのつながり】
ユーザーが感情を指定したかどうかにかかわらず、その章へ来た時点の理解・状況から、何を心配し、何に戸惑い、何を期待するかを考える。妥当な気持ちは複数あり、一つの例文や語句へ合わせない。before.emotionは仮説として具体化する。情報が分からない・手順が難しいという作業上の障壁だけで止めず、それを本人がなぜ気にしているか、何が思いどおりにならず、どんな状態を望むかを、既存の背景・欲求とこの章の状況から考える。answer_elementsには、本人がその状況をどう感じるかを言葉にして受け止め、その回答でどう進めるかを設計する。望みを述べて実務への有用性を説明するだけでは、その間にある戸惑い・ためらい・もどかしさの言語化を代替しない。感情欄だけに名前を書く設計では不足。
conclusionは、その人がどう考え、何を確かめ、どう進めればよいかが分かる答えにする。比較の制約や「判断できない」という留保が必要でも、採用根拠の範囲で示せる進み方まで説明する。調査上の注意は読者の判断に必要な範囲へ絞り、その説明が章の主回答を覆わないようにする。前後の章に説明済み・後続で扱う内容を増やして、この章の受け止めと回答を薄めない。
本文の見出し直下は第一文で問いへの回答・結論を示す。感情理解は、結論の適用場面・理由・具体例・説明の結びへ反映し、共感を前置きにして結論を遅らせない。冒頭の短い条件句と同じ第一文で具体的な答えを述べる形は使えるが、気持ちと答えを無理に一文へ詰め込まない。料金・定義など明確な回答自体で完結する節に、定型的な共感文を足さない。'''

WRITING_POLICY = '''【結論から、読者の実感に応える説明へ】
見出し直下の第一文は、その見出しの問いへの具体的な回答・結論にする。共感だけの前置きで答えを次の文へ送らない。感情を汲み取ることと結論を先に示すことは両立させ、その後の説明で読者の事情・気持ち・望みに応える。
読者理解を作業の難しさや効率だけへ縮めない。本人にとって何が引っかかり、何をかなえたいからこの情報を求めるのかを、章の背景と分析の欲求から捉える。その状況で本人がどう感じるかを、本人が「そこが気になっている」と受け取れる日常の言葉で表す。客観的な問題や希望する結果を述べ、方法が役立つ理由を解説しただけでは、その間にある戸惑い・ためらい・もどかしさまで表したことにはならない。心理ラベルや感情語を足すのでなく、その実感を踏まえて説明・具体例・結びを進め、本人の望みや気がかりにどう応えるかを伝える。実務的に正しい具体策があることだけで、気持ちも伝わったことにしない。
必要な事実と適用条件は保ち、分析にない出来事・人間関係・深刻な不安を創作して感情を強めない。心理・感情の深さを全章で同じにせず、受け止めの位置・文数・語句は固定しない。注意の列挙で判断を読者へ戻さず、この章で分かったことを使って何を考え・確かめればよいかまで案内する。'''

REVIEW_POLICY = '''【章ごとの受け止めと案内の独立確認】
reader_progression_reviewsのjourneyへ、reader_state（この章を読む時点の事情と、本人がなぜ気にして何を望むか）、attunement（それを言語化して受け止める働き）、guidance（受け止めた状態から次の理解・判断・行動へ進める働き）を分けて記録する。ユーザーから指摘があった感情や設計のemotionの語句との一致を合格基準にしない。前章で理解したこと、今の問い、本文・設計の内容から、その章に妥当な気持ちを独立に判断する。
attunementは、その人がその状況をどう受け取っているかを表す実際の記述、構成ではそれを伝えるanswer_elementsの指示をevidenceに示す。問題・制約、希望する結果、方法の作用をつなぐ説明と、本人がその状況でどう感じるかの言語化を区別する。望む結果が実現しにくいと客観的に解説しただけの箇所に、評価者が心配やもどかしさを補ってpassにしない。「分からない」「整理しにくい」という作業上の不足を説明しただけか、その不足を本人がどう受け取って何を望むのかまで伝わるかを読む。資料の背景・欲求にある本人の実感が失われていれば、欠けた意味と該当箇所を具体的に示す。感情語そのものは不要だが、引用から本人の戸惑い・ためらい・期待等が伝わる理由を示す。気持ちを表す短い語句だけを抜き出さず、その前後を読み、なぜ迷う・望むのかを捉えた応答になっているかを確認する。気持ちに触れた直後に否定や判断不能へ戻すだけなら、気持ちの認知と受け止めを混同しない。
not_neededは、その章の事情・問い・設計を読んでも受け止めるべきためらい・期待・葛藤がなく、事実への直接回答で足りる場合だけ選ぶ。手順を扱う章であることや親章に共感があることだけで不要にしない。before.emotionやreader_stateにその章固有の迷いを挙げた場合は、その迷いを本文でどう扱うかを評価する。その設計自体が不適切なら理由を示し、黙って無視しない。独立した共感文を足す必要と、回答の中で気持ちを受け止める必要は別である。
本文の見出し直下の第一文が具体的な答えになっているかも確認する。感情への応答がよくても結論の後置を合格にしない。構成では答えを先に出し、感情理解を説明へ反映する指示になっているかを見る。
guidanceは、その気持ちに対して、この章の結論を使い何を理解・判断・実行すればよいかが分かるかを独立に判定する。一覧や手順が存在するだけでpassにせず、何を勧めるかが埋もれたり、注意点や「判断できない」という留保で読者が止まったりしていないか確認する。冒頭から結論まで通して読み、主な進み方を短く説明できるか、その進み方が章の中心として早く見つかるかを確認する。末尾の一覧や次章予告だけを引用して、主回答を覆う長い留保・細目の説明を相殺しない。後続章で答える問いをすべてこの章で解決する必要はないが、この章で進めたことと次の問いのつながりを読む。不要な詳説・重複で主回答が見えなくなる場合は不足として扱う。
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
