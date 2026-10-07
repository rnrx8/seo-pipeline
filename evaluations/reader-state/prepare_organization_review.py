"""Capture the real organization-role request without executing other roles."""
import json,sys,os
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from pipeline.focused_quality import audit_article
r=Path(sys.argv[1]);text=(r/'reader-auto-final.md').read_text();captured={}
def fake(job,step,request):
 captured[step]=request
 schema=request['output_config']['format']['schema']['properties']['checks']['items']['properties'];keys=schema['key']['enum']
 return {'checks':[{'key':k,'status':'pass','reason':'offline request capture only','affected_blocks':[]} for k in keys]},SimpleNamespace(input_tokens=0,output_tokens=0)
with patch.dict(os.environ,{'ARTICLE_REVIEW_PROVIDER':'tiered','QUALITY_RESEARCH_ROUTING':''}),patch('pipeline.tiered_research.checked_request',side_effect=fake):
 audit_article(None,text=text,facts='',outline='',contract={},requirements={'citation_style':'none','article_purpose':'cv'},sources='[]')
req=captured['tiered_article_organization'];assert req['model']=='gpt-6.1-sol' and req['max_tokens']==6000
(r/'organization_review-request.json').write_text(json.dumps(req,ensure_ascii=False))
print('Captured normal organization request; no paid calls')
