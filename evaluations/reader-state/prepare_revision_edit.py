"""Frozen local issue-specific edit packet for the revised-policy verification."""
import json
from pathlib import Path
from pipeline.content_edits import content_blocks
from pipeline.reader_presentation import EFFECT_CLAIM_POLICY
from pipeline.readability import READABILITY_POLICY,readability_issues
import sys
r=Path(sys.argv[1]);s=(r/'reader_v2_sonnet.md').read_text();req=json.loads((r/'opus_edit-request.json').read_text());data=json.loads(req['messages'][0]['content']);data['article_blocks']=content_blocks(s)
ids=['block-0002','block-0008','block-0020','block-0026','block-0032','block-0034','block-0036','block-0038']
data['scope']={'allowed_ids':ids,'instruction':'リードと推奨商材の冒頭は、読者の気持ちを決めつけず、望みに応える価値が伝わる文章に磨く。1日3分は検索意図の例であり読者の実態として断定しない。block-0008では資料取得状況の説明を除き、女性料金列もある表をすべて男性料金とは説明しない。税込条件は残す。block-0026は表ですでに累計と明示されている情報の重複説明なので削除可。block-0032〜0038の連続文章は、共感・具体的な提案・必要条件を残し、免責や表との重複を整理する。並列でない文章に記号を付けるだけのリスト化はしない。編集不要な指定段落はそのままにできる。編集後の記事全体の連続300字以内を確認する。新しい事実・見出し・表は追加せず、指定外は変えない。','readability_findings':readability_issues(s)}
req['system']+='\n'+EFFECT_CLAIM_POLICY+'\n'+READABILITY_POLICY
req['messages'][0]['content']=json.dumps(data,ensure_ascii=False)
(r/'reader_v2_edit-request.json').write_text(json.dumps(req,ensure_ascii=False));(r/'reader_v2_edit-scope.json').write_text(json.dumps(data['scope'],ensure_ascii=False,indent=2))
