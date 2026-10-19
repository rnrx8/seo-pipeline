"""Three lead decisions inside the existing coverage/readiness review call."""
import copy
import re
from .content_edits import content_blocks

VERSION='lead-review-v4-concrete-reader-value'
KEYS=('subject_and_reader','motivation_bridge','reading_payoff')
LABELS={'subject_and_reader':'書き出しの主題と対象読者','motivation_bridge':'背景から読む動機への接続','reading_payoff':'読後にできる判断'}
POLICY='''【リードを三つの判断に分ける】
lead_reviewのsubject_and_reader / motivation_bridge / reading_payoffを個別に返す。構成ではlead_direction、本文では実際のリードを対象にする。三項目に対応する文を探すだけで合格にせず、最初から最後まで一続きに読んだときの意味と視点のつながりを確認する。reasonには前の文から次の文へ何が引き継がれたか、またはどこで途切れるかを具体的に示す。
subject_and_readerは、冒頭の発話自体が、書き手から何を探す・選ぶ・解決したい読者への案内として自然に読めるかを見る。主題語を含んでも、冒頭が記事そのものの内容紹介だけなら、主題についての自然な話の入口になっているかを指摘する。望みや体験を述べる場合は誰のものかを文脈から確認し、書き手自身の告白や自己紹介のように読める、または誰に向けた発話か分からないなら、主題語が存在してもfail。二人称・問いかけ・明示的な主語は一律要求せず、省略しても視点が伝わる文は通す。H1や後半の予告から逆算しない。一般的な望みの説明として別の主題の記事にもそのまま読める場合、読者像が示されているだけで主題を特定済みとしない。対象属性・心理・記事の制作行為が長い修飾句に重なり、文を解きほぐさないと主題が分からない場合も指摘する。検索語の完全一致や固定の呼びかけは要求しない。
motivation_bridgeは、選んだ具体的な事情・得たい体験・影響が読者に何を意味し、記事を読む理由まで自然につながるかを見る。抽象的な感情の説明や検索語の言い換えの問いかけだけで理解済みとしない。読者の内面を一方的に決めつけて同意を求めていないか、次の文がそこで生まれる疑問を受け止めているかを読む。選んだパターン名や場面描写の存在は合格の証明ではない。記事の作業説明→人物像の説明→比較条件→作業説明のように視点や話題が飛ぶ場合、各要素が存在してもfail。対応する語や接続詞の存在を根拠にpassとしない。構成でも必須要素の列挙だけで自然な流れを設計済みとせず、短い一つの迷いと読む理由へ絞れるか確認する。心理を使わない手順・定義で目的と答えが直接つながる場合はnot_applicableでよい。固定の文数・共感テンプレート・より強い感情は要求しない。
reading_payoffは、流れの中で何を理解・判断・実行する助けが得られるか分かるかを見る。項目名を列挙しただけ、方法・サービス・選び方・始め方など異なる対象を曖昧にひとまとめにした予告は指摘する。本文を読む入口と、すぐ答える導入の役割を区別する。記事のCV目的を理由にリードの推薦を一律要求せず、ユーザーが本文へ任せた解決や推薦を先出ししていないかも確認する。比較軸・推薦カテゴリ・読後判断をそれぞれ明示することを一律要求せず、本文で得られる助けが自然に伝わることを優先する。主題と読む動機を保ち、不要な定義・全候補・詳細な予告で長さ指定を圧迫していないかも共通リード基準に照らす。未検証の成果や本文にない答えを約束しない。
statusはpass/fail/not_applicable。passとnot_applicableのaffected_blocksは空、failには実在するリードの対象ブロックと理由を返す。実際にリードがある場合、subject_and_readerとreading_payoffをnot_applicableにしない。リードを含まない抜粋ではnot_applicableを許可する。三判断のfailは既存coverageの必須修正へ集約し、他の回答・構成や全体の良さで相殺しない。文法が成立し各要素がそろっていても、意味の流れや読者への伝わり方に具体的な欠陥があれば指摘する。温度感をさらに高められるという好みと、文章がつながらず読む理由を理解できない欠陥は区別する。'''



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
