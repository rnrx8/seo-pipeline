"""Save edited excerpts and compare deterministic properties, without paid judging."""
import json,re,sys
from pathlib import Path
from pipeline.content_edits import content_blocks,apply_block_edits
from pipeline.readability import readability_issues

def check(root):
 root=Path(root);result={}
 for kind,source in [('control','sonnet.md'),('candidate','reader_sonnet.md')]:
  text=(root/source).read_text();raw=(root/f'reader_{kind}_edit.md').read_text();edits=json.loads(raw)['edits'];allowed=set(json.loads((root/f'reader_{kind}_edit-scope.json').read_text())['allowed_ids'])
  assert {e['id'] for e in edits}<=allowed
  output=apply_block_edits(text,raw);before={b['id']:b['text'] for b in content_blocks(text)};after={b['id']:b['text'] for b in content_blocks(output)}
  assert before.keys()==after.keys();assert all(after[k]==v for k,v in before.items() if k not in allowed)
  (root/f'reader-{kind}-final.md').write_text(output)
  intro=output.split('\n## ',1)[1].split('\n### ',1)[0];h2answer=intro.split('\n\n')[1].split('。')[0]+'。'
  result[kind]={'unchanged_outside_selected_blocks':True,'continuous_prose_before':readability_issues(text),'continuous_prose_after':readability_issues(output),'first_h2_answer':h2answer,'h2_answer_length':len(h2answer),'h2_keywords':all(w in h2answer for w in ['40代','既婚者','マッチングアプリ']),'h2_pre_table_characters':len(intro.split('|')[0]),'edits':edits,'editor_paragraph_limit_pass':all(len(e['new'])<=200 for e in edits)}
 budget=json.loads((root/'budget.json').read_text());result['additional_cost_jpy']=sum(c['cost_jpy'] for arm,c in budget['calls'].items() if arm.startswith('reader_'));result['cumulative_cost_jpy']=sum(c['cost_jpy'] for c in budget['calls'].values())
 (root/'reader-checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':check(sys.argv[1])
