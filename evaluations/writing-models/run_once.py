"""One explicitly authorized evaluation request; no production writes or retries."""
import fcntl, hashlib, json, sys, time
from pathlib import Path
import anthropic


def run(root, arm):
    root=Path(root)
    models = {'sonnet':'claude-sonnet-5-5','opus_edit':'claude-opus-5-5','sonnet_edit':'claude-sonnet-5-5','reader_sonnet':'claude-sonnet-5-5','reader_control_edit':'claude-opus-5-5','reader_candidate_edit':'claude-opus-5-5'}
    allocations = {'sonnet':50,'opus_edit':50,'sonnet_edit':15,'reader_sonnet':50,'reader_control_edit':25,'reader_candidate_edit':25}
    assert arm in models, 'Arm is not authorized'
    with (root/'budget.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        state=json.loads((root/'budget.json').read_text())
        assert state['approved'] and state['max_total_jpy']==200
        assert arm not in state['calls'], 'Arm already attempted; do not resend'
        request=json.loads((root/f'{arm}-request.json').read_text())
        assert request['model']==models[arm]
        assert request['max_tokens']<=10000
        client=anthropic.Anthropic(max_retries=0)
        count=client.messages.count_tokens(model=request['model'],system=request['system'],messages=request['messages'],timeout=45).input_tokens
        ir,orr=(2,10) if models[arm]=='claude-sonnet-5-5' else (4,20)
        bound=((count*1.05+1024)*ir+request['max_tokens']*orr)/1e6*200
        allocation = allocations[arm]
        assert bound<=allocation, 'Per-arm allocation would be exceeded'
        used=sum(x.get('cost_jpy',x['reserved_jpy']) for x in state['calls'].values())
        assert used+allocation<=state['max_total_jpy']
        state['calls'][arm]={'status':'pending','reserved_jpy':allocation,'input_count':count,'request_sha256':hashlib.sha256(json.dumps(request,ensure_ascii=False,sort_keys=True).encode()).hexdigest()}
        def save():
            tmp=root/'budget.tmp';tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2));tmp.replace(root/'budget.json')
        save();started=time.monotonic()
        try:
            with client.messages.stream(**request,timeout=600) as stream:
                msg=stream.get_final_message()
            usage=msg.usage.model_dump();cost=(usage['input_tokens']*ir+usage['output_tokens']*orr+usage.get('cache_creation_input_tokens',0)*ir*2+usage.get('cache_read_input_tokens',0)*ir)/1e6*200
            state['calls'][arm].update(status='accounted',cost_jpy=cost,usage=usage,seconds=round(time.monotonic()-started,2),stop_reason=msg.stop_reason)
            save()
            text='\n'.join(b.text for b in msg.content if b.type=='text')
            (root/f'{arm}-raw.txt').write_text(text)
            assert msg.stop_reason=='end_turn' and text.strip(), 'Incomplete output retained; no retry'
            (root/f'{arm}.md').write_text(text)
            print(json.dumps(state['calls'][arm],ensure_ascii=False),flush=True)
        except BaseException as exc:
            state['calls'][arm].update(error=type(exc).__name__)
            if state['calls'][arm]['status']=='pending':state['calls'][arm]['status']='unknown_cost_reserved'
            save();raise

if __name__=='__main__':run(sys.argv[1],sys.argv[2])
