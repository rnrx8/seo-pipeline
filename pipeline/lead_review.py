"""Three lead decisions inside the existing coverage/readiness review call."""
import copy
import re
from .content_edits import content_blocks

VERSION='lead-review-v8-grounded-background'
KEYS=('subject_and_reader','motivation_bridge','reading_payoff')
LABELS={'subject_and_reader':'書き出しの主題と対象読者','motivation_bridge':'背景から読む動機への接続','reading_payoff':'読後にできる判断'}
POLICY='''【リードを三つの判断に分ける】
構成ではlead_direction、本文では実際のリードについて、subject_and_reader / motivation_bridge / reading_payoffを個別に返す。最初から最後まで一続きに読み、reasonは対象の短い引用と、その文章から何が伝わるか、どこが欠けるかを説明する。要素や接続詞があることを合格の根拠にしない。
本文の判定では、H1・次章・検索意図・設計から意味を補ってリードに書かれていることにしない。設計は期待する働きを照合する資料。読者背景の要約が抽象的なら、context・motivation_linksの具体的な事情と望む体験も確認する。新しい事情を本文へ作る指摘はせず、元分析自体に必要なつながりがないならその不足と戻す工程を示す。
subject_and_reader：冒頭の発話自体から、何を探す・選ぶ・解決したい人への話かが伝わるか。対象属性と一般的な行為だけで主題を特定済みにせず、別の主題の記事にもそのまま読めないか確かめる。書き手の告白、人物紹介、長い修飾で解きほぐさないと分からない文も指摘する。KWの完全一致・二人称・特定の呼びかけは要求しないが、後半やH1から逆算して通さない。
motivation_bridge：選んだ背景から、なぜその読者が今回の対象・情報・手段を求めるのかが、実際の文章の中でつながるか。感情理解を使う導入では、単なる忙しさ・状況と、そこで望む経験が満たされない事情を区別する。分析に具体的なやり取り・反応・ためらいがあるのに、本文がそれを落として状況→抽象的な欲求へ飛ぶ場合は、その省略が主題を求める理由や感情の伝わり方へ与える影響を指摘する。「理解されない」「役割を離れたい」等の説明があるだけで共感を実現済みにしない。何が起き、なぜその選択を望み、次に何を知る必要があるのかを、引用した本文から説明できるか確かめる。読者の心理を決めつけた同意要求や、視点・対象が途中で変わる流れも指摘する。前向きな希望や直接の操作回答に不満・失敗を強制せず、心理が不要ならnot_applicableでよい。
reading_payoff：前の文から生まれる疑問を受け、本文で何を理解・判断・実行する助けが得られるかが具体的に分かるか。曖昧な作業予告や異なる比較対象の混同を指摘する。記事目的と指定された訴求の強さに対し、必要な判断や読むメリットが明確かを確かめる。リードに推薦を一律要求せず、本文へ任せた推薦・解決を先出ししない。本文にない回答・未検証の成果・安全の保証を約束しない。長さ指定を、全候補・比較軸の詳述や重なる予告が圧迫していないかも確認する。
statusはpass/fail/not_applicable。failには実在する対象ブロックと、文章が読者にどう伝わらないか、何を直す必要があるかを返す。pass/not_applicableのaffected_blocksは空。実際にリードがある場合subject_and_readerとreading_payoffはnot_applicableにしない。リードを含まない抜粋ではnot_applicableを許可する。
文章がつながらない、主題を求める背景や読む理由が伝わらない欠陥と、より好みの表現にできる任意改善を区別する。明示された感情理解・文体の要件は守る。文法、情報量、他の章の良さでfailを相殺せず、三判断のfailを既存coverageの必須修正へ集約する。'''



def output_config(config):
    result=copy.deepcopy(config);root=result['format']['schema']
    location={'type':'object','properties':{'id':{'type':'string'},'reason':{'type':'string'}},
              'required':['id','reason'],'additionalProperties':False}
    row={'type':'object','properties':{'status':{'type':'string','enum':['pass','fail','not_applicable']},
        'reason':{'type':'string'},'affected_blocks':{'type':'array','items':location}},
        'required':['status','reason','affected_blocks'],'additionalProperties':False}
    root['properties']['lead_review']={'type':'object','properties':{key:copy.deepcopy(row) for key in KEYS},
        'required':list(KEYS),'additionalProperties':False}
    root['required'].append('lead_review')
    return result


def article_block_ids(text):
    ids=[];after_title=False
    for block in content_blocks(text):
        body=False
        for line in block['text'].splitlines():
            if re.match(r'^\s*#\s+',line):after_title=True;continue
            if re.match(r'^\s*#{2,6}\s+',line):return ids+([block['id']] if body else [])
            if after_title and line.strip():body=True
        if body:ids.append(block['id'])
    return ids


def checked(value, block_ids):
    from .content_quality import ContentQualityError
    rows=value.get('lead_review');allowed=set(block_ids)
    def fail():raise ContentQualityError('リードの対象・読む動機・読後判断の個別確認が不足または不整合です。')
    if not isinstance(rows,dict) or set(rows)!=set(KEYS):fail()
    for key,row in rows.items():
        if (not isinstance(row,dict) or row.get('status') not in ('pass','fail','not_applicable')
                or not isinstance(row.get('reason'),str) or not row['reason'].strip()
                or not isinstance(row.get('affected_blocks'),list)):fail()
        locs=row['affected_blocks']
        if (row['status']=='fail')!=bool(locs):fail()
        if allowed and key!='motivation_bridge' and row['status']=='not_applicable':fail()
        if any(not isinstance(loc,dict) or loc.get('id') not in allowed
               or not isinstance(loc.get('reason'),str) or not loc['reason'].strip() for loc in locs):fail()
    return copy.deepcopy(rows)
