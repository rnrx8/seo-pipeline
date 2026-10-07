"""Offline packet preparation through the existing repair selector, no manual IDs."""
import json,sys
from pathlib import Path
from pipeline.review_scope import repair_packet
from pipeline.readability import READABILITY_POLICY
from pipeline.reader_presentation import EFFECT_CLAIM_POLICY
from pipeline.content_edits import REPAIR_OUTPUT_CONFIG
r=Path(sys.argv[1]);text=(r/'reader_v2_sonnet.md').read_text()
packet=repair_packet(text,{},[],{'checks':[]})
request={'model':'claude-opus-5-5','max_tokens':3000,'output_config':{**REPAIR_OUTPUT_CONFIG,'effort':'medium'},'system':'提供された指摘箇所を修正する編集者です。target_block_idsのみを変更し、変更が必要な段落全体をJSON editsのid/newで返す。指定対象のうち変更不要な段落は返さなくてよい。事実・数値・条件・見出し・表を維持し、資料にない事実は追加しない。読者の望みと確認済みの強みの接続、自然で丁寧な提案を維持する。\n'+EFFECT_CLAIM_POLICY+'\n'+READABILITY_POLICY,'messages':[{'role':'user','content':json.dumps(packet,ensure_ascii=False)}]}
(r/'reader_auto_edit-request.json').write_text(json.dumps(request,ensure_ascii=False));(r/'reader-auto-packet.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2));print(packet['target_block_ids'])
