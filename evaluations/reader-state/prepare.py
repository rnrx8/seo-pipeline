"""Offline pilot: replace prose planning with sourced reader-state notes.
This tests a manually prepared outline hypothesis, not automated outline quality.
"""
import json,re,hashlib
from pathlib import Path

def prepare(root,artifacts):
 root=Path(root);a={x['step']:x for x in json.loads(Path(artifacts).read_text())}
 old=a['outline']['content_text'];h3=list(re.finditer(r'^#### H3.*',old,re.M));old=old[:h3[2].start()]
 new=old
 lead='''## リード文の設計方針
- 理解済み：40代の既婚者向けサービスを探しているが、どのサービスが自分に合うかは未判断。
- 知りたい・判断したい：今の生活を大切にしながら、同じ立場で話せる相手を探す選択肢があるか。
- 残る不安：家族に知られること、怪しい相手との接触。これは検索意図からの仮説で、読者全員の実態ではない。
- 応える確認済みの強み：40代の利用者構成、男女別料金、身バレ対策を比較できる記事の情報。ここでは個別機能を詳説せず、読むと何を判断できるかを示す。
- 長さ：200字以内。読者の生活を統計や実際の発言として断定しない。
- 締め：40代の利用者層・男女別料金・身バレ対策で5サービスを比べ、違法性の範囲と安全な使い方まで分かることを伝える。

'''
 new=re.sub(r'## リード文の設計方針\n.*?(?=---)',lambda m:lead,new,count=1,flags=re.S)
 h2='''**H2直下方針**：対象・カテゴリ・おすすめを第一文で答え、短い比較理由を1〜2文で述べて比較表へ進む。各商材の詳しい利用場面はH3の役割。

- **セクション内容（読者心理メモ）**：
  - 理解済み：5サービスの利用者層・料金・身バレ対策を比べられること。
  - 知りたい・判断したい：40代の既婚者向けマッチングアプリで、まずどれを選ぶと自分の希望に合うか。
  - 残る不安：話せる同世代がいるか、家族に知られず始めやすいか、予算や希望に合うか。
  - 応える確認済みの強み：既婚者クラブの40代約4割とWeb利用・写真ぼかし。第一候補の理由として要約する。他社の料金・機能は比較表と目的別リストで選び分けられるようにする。
'''
 new=re.sub(r'\*\*H2直下方針\*\*.*?(?=- \*\*表現形式\*\*)',lambda m:h2,new,count=1,flags=re.S)
 start=new.index('#### H3：既婚者クラブ');end=new.index('- **表現形式**',start)
 prev=new[start:end];head=prev.split('\n')[0]
 keep=prev[prev.index('  - **数値の扱い**'):]
 replacement=head+'''

- **セクション内容（読者心理メモ）**：
  - 理解済み：比較H2で既婚者クラブが第一候補で、40代が約4割、身バレ対策があると分かっている。
  - 知りたい・判断したい：自分と同じ立場で趣味や悩みを話せる相手を探す場として、どう役立つか。無料で何を確かめてから始められるか。
  - 残る不安：誰かと話したくても、今の家族との生活は大切にしたい。顔や名前を広く公開したくない。怪しい相手も避けたい。
  - 応える確認済みの強み：40代の利用者構成は同世代探しの材料。Web・写真ぼかし・ニックネーム・閲覧制限は公開範囲への不安に、本人年齢確認・監視は相手への不安に対応する判断材料。男性の無料検索は有料登録前の判断に使える。機能は安全や家庭への影響防止を保証するものではない。
  - 展開：冒頭は単体のおすすめ概要を回答し、表の後に望みと機能のつながり、利用時の選択肢を説明する。H2の選定理由をそのまま繰り返さない。
'''+keep
 new=new[:start]+replacement+new[end:]
 start=new.index('#### H3：ヒールメイト');end=new.index('- **表現形式**',start);head=new[start:end].split('\n')[0]
 new=new[:start]+head+'''

- **セクション内容（読者心理メモ）**：
  - 理解済み：既婚者クラブという第一候補の利用方法と強み。他にも比較できる候補がある。
  - 知りたい・判断したい：ヒールメイトで癒やし合える同世代の話し相手を探すことが、自分の希望に合うか。
  - 残る不安：年齢の近い相手がいるか、写真の公開範囲を調整できるか、課金前に会員を見られるか。
  - 応える確認済みの強み：40代が最多の約42％、写真ぼかし・非公開設定、男性の無料検索・閲覧、女性のメッセージ利用前の本人確認。前の商材の説明を繰り返さず、この候補を判断する材料として伝える。
'''+new[end:]
 # Every factual source and table specification outside prose-planning replacement is retained.
 assert re.findall(r'https?://\S+',old)==re.findall(r'https?://\S+',new)
 baseline=json.loads((root/'sonnet-request.json').read_text());candidate=json.loads(json.dumps(baseline));msg=candidate['messages'][0]['content'];assert msg.count(old)==1
 candidate['messages'][0]['content']=msg.replace(old,new)
 for key in baseline:
  if key!='messages':assert candidate[key]==baseline[key]
 (root/'reader_sonnet-request.json').write_text(json.dumps(candidate,ensure_ascii=False))
 (root/'reader-outline.md').write_text(new)
 (root/'reader-outline-original.md').write_text(old)
 (root/'reader-plan.json').write_text(json.dumps({'changed':'outline prose-planning only','manual_outline_pilot':True,'frozen':['research','settings','writer system','model','effort','output limit','article scope'],'paid_call_limits':{'reader_sonnet':50,'reader_control_edit':25,'reader_candidate_edit':25},'existing_total_jpy':51.4048,'max_total_jpy':200,'automatic_retries':0,'reader_notes_sources':['existing search_intent','existing intent_chains','existing outline evidence'],'not_proven':'Automatic production of these notes; full-article quality; general model superiority'},ensure_ascii=False,indent=2))
 print('prepared; only outline replaced; source URLs preserved')
if __name__=='__main__':
 import sys
 prepare(*sys.argv[1:])
