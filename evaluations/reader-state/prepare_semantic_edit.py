"""Pass actual reviewer findings through existing scoped repair, no hand-picked IDs."""
import json,sys
from pathlib import Path
from pipeline.focused_quality import parse_focus,ROLES
from pipeline.review_scope import repair_packet
from pipeline.readability import READABILITY_POLICY
from pipeline.reader_presentation import EFFECT_CLAIM_POLICY
from pipeline.content_edits import REPAIR_OUTPUT_CONFIG
r=Path(sys.argv[1]);text=(r/'reader-auto-final.md').read_text()
checks=parse_focus((r/'organization_review.md').read_text(),ROLES['organization'],text)
if not any(c['status']=='fail' for c in checks):
 print('No failed organization findings; no edit request prepared');sys.exit(0)
packet=repair_packet(text,{'citation_style':'none','article_purpose':'cv'},[],{'checks':checks})
request={'model':'claude-opus-5-5','max_tokens':1500,'output_config':{**REPAIR_OUTPUT_CONFIG,'effort':'medium'},'system':'指摘されたtarget_block_idsだけを修正する編集者です。JSON editsのid/newで変更する段落全体を返す。事実・数値・料金条件・既存の見出しと表は維持する。資料取得などの内部判断を読者向け注釈へ転記しない。変更不要な段落は返さない。不要な本文はnewを空文字にして削除できる。対象外は編集せず、新しい事実は加えない。\n'+EFFECT_CLAIM_POLICY+'\n'+READABILITY_POLICY,'messages':[{'role':'user','content':json.dumps(packet,ensure_ascii=False)}]}
(r/'organization_edit-request.json').write_text(json.dumps(request,ensure_ascii=False));(r/'organization-edit-packet.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2));print(json.dumps(checks,ensure_ascii=False));print(packet['target_block_ids'])
