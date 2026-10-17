"""Three lead decisions inside the existing coverage/readiness review call."""
import copy
import re
from .content_edits import content_blocks

VERSION='lead-review-v1'
KEYS=('subject_and_reader','motivation_bridge','reading_payoff')
LABELS={'subject_and_reader':'書き出しの主題と対象読者','motivation_bridge':'背景から読む動機への接続','reading_payoff':'読後にできる判断'}
POLICY='''【リードを三つの判断に分ける】
lead_reviewのsubject_and_reader / motivation_bridge / reading_payoffを個別に返す。構成ではlead_directionの設計指示、本文では実際のリードを検査する。reasonには該当する書き出し・つなぎ・読後判断を具体的に示す。
subject_and_readerは、最初の一文または冒頭の発話自体に主題の行為・問い・対象があるかを見る。H1や後半の「この記事では」から主題を逆算しない。一般的な望みや不安だけで始まり、何を探す・選ぶ・解決する人向けかが後半で初めて現れる場合はfail。主題が自然に分かれば検索語の完全一致や「あなた」を要求しない。
motivation_bridgeは、使った背景・迷いが、本文で説明する違い・選び方・手順にどう関係するかを見る。「そんな方に向けてこの記事では…」と項目予告へ進むだけを接続の説明としない。背景があることと読む理由が生まれることを分ける。直接の答えから始める手順・定義など、心理的な背景を使わず目的と答えが直接つながる導入はnot_applicableでよい。固定の三段落・文数・長い共感は要求しない。
reading_payoffは、内容の一覧だけでなく、読むことで何を選ぶ・理解する・実行できるかが分かるかを見る。本文で答えないことや未検証の効果は約束しない。
statusはpass/fail/not_applicable。passとnot_applicableのaffected_blocksは空、failには実在するリードの対象ブロックと理由を返す。実際にリードがある場合、subject_and_readerとreading_payoffをnot_applicableにしない。リードを含まない抜粋ではnot_applicableを許可する。三判断のfailは既存coverageの必須修正へ集約し、他の回答・構成や全体の良さで相殺しない。'''


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
