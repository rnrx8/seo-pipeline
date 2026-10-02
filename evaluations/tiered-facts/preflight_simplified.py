"""Exercise the existing budget guard without a provider call or production writes."""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from pipeline import quality_budget as budget
from pipeline.content_quality import ContentQualityError

def legacy():
    payload={'model':'claude-sonnet-4-6','max_tokens':9000,
             'tools':[{'type':'web_search_20250305','name':'web_search','max_uses':3}],
             'messages':[{'role':'user','content':'offline reservation check'}]}
    with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'QUALITY_BUDGET_DIR':folder,'QUALITY_COMPLETION_EVAL':'0'}), budget.scope('offline-preflight','research_completeness'):
        try:
            budget.reserve_claude(payload)
        except ContentQualityError as exc:
            return {'allowed':False,'reason':str(exc),'quality_limit_usd':budget.LIMIT,
                'conservative_request_reservation_usd':(4_000_000*budget.CLAUDE_RATES[payload['model']][0]+9000*budget.CLAUDE_RATES[payload['model']][1])/1e6+.03,
                'external_api_calls':0,
                'note':'Worst-case reservation, not expected cost or a bill. Live test remains blocked by this preflight.'}
        raise AssertionError('Budget behavior changed; review the forecast before running a paid test.')

def run():
    from pipeline.research_search import SEARCH_TOOL
    from pipeline.fresh_sources import FETCH_TOOL, DIRECT_POLICY
    from pipeline.step_fact_sheet import SYSTEM_PROMPT
    baseline=legacy()
    # Representative serialized Japanese input, not measured model usage.
    payload={'model':'claude-sonnet-4-6','max_tokens':6000,
             'system':SYSTEM_PROMPT+DIRECT_POLICY,'tools':[SEARCH_TOOL,FETCH_TOOL],
             'messages':[{'role':'user','content':'対象の質問と取得済み資料。'*1000}]}
    with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'QUALITY_BUDGET_DIR':folder,'QUALITY_COMPLETION_EVAL':'0'}), budget.scope('offline-bounded-preflight','research_completeness'):
        index=budget.reserve_claude(payload)
        with budget.ledger() as value:amount=value['calls'][index]['reserved_usd']
    return {'legacy_server_search':baseline,'bounded_client_search':{
        'allowed':True,'reservation_usd':amount,'payload_bytes':len(json.dumps(payload,ensure_ascii=False).encode()),
        'separate_search_allowance_usd':.001,'note':'Representative request only, not a complete article forecast.'},
        'external_api_calls':0,'article_cost_verified':False}

if __name__=='__main__':
    with patch.object(socket.socket,'connect',side_effect=AssertionError('Offline only')), patch.object(socket,'create_connection',side_effect=AssertionError('Offline only')):
        result=run()
    Path(sys.argv[1]).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
