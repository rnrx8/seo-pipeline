"""Shared authoring decisions; no templates tied to one query or product."""
import json

LEAD_POLICY = '''
【記事タイプに合うリード】
構成のリード方針、中心の問い、読者の状況、記事目的から導入を選ぶ。比較・選択の記事は何をどう選び分けるか、手順・定義の記事は答えと必要な前提、相談・悩みの記事は具体的な状況理解と解決の見通しを中心にする。これらは選択の手掛かりであり、全記事を共感→背景→解決や固定の文数・書き出しへ当てはめない。
読者が記事を読む理由と得られる答えを簡潔に示す。本文で答えないことを約束せず、無関係な市場規模や背景数値で始めない。確認済みの数値が有効なら使えるが、具体性のための数字や体験を創作しない。200字程度を目安に必要な内容で長さを決める。
共感・問いかけ・情景・結論からの書き出しは文脈に合わせて選べる。心理は仮説として内容・例・言葉へ反映し、全読者の実態と決めつけない。concrete_phrase等は理解の素材であり転記必須ではない。原文が自然なら活かしてよいが、分析ラベル・数値・効果を未確認のまま引用しない。
リードにはH2/H3/H4直下の結論先行を機械的に適用しない。校閲は状況に合う導入と実際の回答を照合し、共感や問いかけがある/ないこと、別の書き出しが好みというだけで修正しない。
'''

PROGRESSION_WRITING_POLICY = '''
【章の変化を文章へ実現する】
reader_progressionの読む前の状態・この章で解消する疑問・読後の理解/判断/行動・狙う感情・次の問いを、その章の説明・具体例・比較軸・順序へ反映する。子節の独自指定がなければ親章の役割を担い、毎章で記事全体を解決し直さない。
marketingの役割に沿い、潜在課題への気づき、懸念の重要性、新しい選択肢、期待・納得、商品価値の理解、比較・相談・登録へ意図して導ける。心理は仮説、感情の変化は狙いであり、全員の実態・実測効果と断定しない。虚偽の恐怖/効果/緊急性や脅迫、重要条件の隠蔽は行わない。
設計ラベルや節IDを本文へ転記しない。毎章末に共感・安心・期待・CTA案内を付けず、自然に完結する章はそこで終える。次の問いへの案内は必要な場合だけ内容を具体的に示し、本文が答えない問いへ誘導しない。
推薦は対象読者の状況→確認済みの強みの作用→役立つ変化と必要条件をつなぐ。商品間で共通する機能の説明と選び分けの決め手を区別し、合理的な利用価値すべてへ効果率や他社の不存在証明を要求しない。紹介優先度は編集上の重点であり、人気・品質・成果の順位を作る根拠ではない。
'''

CTA_WRITING_POLICY = '''
【章の判断とCTAの接続】
CTAの位置・登録文面・ボタン・URLは専用の挿入工程が扱う。執筆は、設計した位置で章の判断と登録CTAの次の行動をつなぐ短い案内を必要に応じて書く。CTAブロックやボタンを先に重複生成しない。別の約束・特典・成果・緊急性・料金条件を作らない。
登録CTAの本文は導線の約束を把握する資料であり、登録だけで事実を検証済みとは扱わない。事実・条件は採用済み回答と照合し、未確認の宣伝文句を本文側へ広げない。CTAの省略・保留をCV目的だけで復活させない。比較継続、相談、登録、利用を見送る等、今回の設計で必要な選択を尊重する。
'''


def company_prompt(companies, restriction='ai'):
    selected=[c for c in companies or [] if c.get('recommend_level',0)>0]
    if not selected:return ''
    lines=['\n【紹介企業設定】',
        '紹介・比較する企業は以下のリストに含まれる企業のみ。リスト外は紹介・言及しない。' if restriction=='registered_only' else '以下は紹介優先度の設定。追加候補は確認済み回答と読者の問いに合う範囲で扱う。',
        '優先度は扱う重点・掲載の優先であり、比較表1位・人気順位・効果の優劣を保証しない。構成で決めた順序・対象・推薦理由と実条件を守り、自社に有利な根拠ある価値を具体化する。']
    for c in sorted(selected,key=lambda c:c.get('recommend_level',0),reverse=True):
        row={k:c.get(k) for k in ('name','recommend_level','affiliate_url','notes') if c.get(k) is not None}
        lines.append(json.dumps(row,ensure_ascii=False))
    lines.append('登録notesは確認前の要望・資料。事実は今回の採用回答を使う。個別紹介の表は共通提示基準に従い、対象と関係しない業種別の固定項目や順位ラベルを増やさない。')
    return '\n'.join(lines)+'\n'


def service_prompt(service):
    if not service:return ''
    row={k:service[k] for k in ('name','url','must_include','must_exclude') if service.get(k)}
    return ('\n【紹介サービス設定】\n'+json.dumps(row,ensure_ascii=False)
        +'\n今回の読者設計・記事目的・構成に割り当てた役割で扱う。商材指定だけで主回答を商品推薦に置き換えない。'
        '必須記載の事実・数値は採用回答と照合し、未確認のまま断定しない。\n')


def cta_prompt(cta, placement=None, *, planning=False):
    if not cta:return ''
    data={k:cta[k] for k in ('name','body','button_text','url') if cta.get(k)}
    if placement is not None:
        data['after_h2']=placement.get('cta_after_h2',[])
    instruction='今回の読者設計に合う場合だけ、適切な章のctaへ案内の役割を設計する。登録CTAの完成文面を構成へ複製しない。' if planning else '必要な案内だけを本文に書く。'
    return '\n【登録CTAの内容と配置（事実の根拠ではない）】\n'+json.dumps(data,ensure_ascii=False)+'\n共通の「章の判断とCTAの接続」に従う。'+instruction+'\n'
