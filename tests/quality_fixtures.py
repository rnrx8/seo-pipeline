import json
from types import SimpleNamespace
from pipeline.focused_quality import ROLES

def phases(fingerprint):
    return {role:{'snapshot':fingerprint,'checks':[{'key':k,'status':'pass','reason':'検証済み','affected_blocks':[]} for k in keys]} for role,keys in ROLES.items()}

def responses(failed=None, block='block-0000'):
    return [SimpleNamespace(stop_reason='end_turn',content=[SimpleNamespace(text=json.dumps({'checks':[
        {'key':k,'status':'fail' if k==failed else 'pass','reason':'具体的な検証理由',
         'affected_blocks':[{'id':block,'reason':'修正対象'}] if k==failed else []} for k in keys]}))],
        usage=SimpleNamespace(input_tokens=1,output_tokens=1)) for keys in ROLES.values()]
