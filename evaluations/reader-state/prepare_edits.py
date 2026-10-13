"""Use identical role-based editing instructions in both outline arms."""
import json,sys
from pathlib import Path
from pipeline.content_edits import content_blocks
from pipeline.reader_presentation import EFFECT_CLAIM_POLICY
from pipeline.readability import READABILITY_POLICY

def prepare(root,kind):
 root=Path(root);source='sonnet.md' if kind=='control' else 'reader_sonnet.md'
 blocks=content_blocks((root/source).read_text());h1=next(i for i,b in enumerate(blocks) if b['text'].startswith('# '));h3s=[i for i,b in enumerate(blocks) if b['text'].startswith('### ')]
 assert len(h3s)==2
 lead=blocks[h1+1];opening=blocks[h3s[0]+1];closing=next(b for b in reversed(blocks[h3s[0]+1:h3s[1]]) if not b['text'].startswith(('#','|','- ','[')) and '年齢確認' in b['text'] and '監視' in b['text'])
 targets=[lead,opening,closing];assert all(not b['text'].startswith(('#','|','- ')) for b in targets)
 template=json.loads((root/'opus_edit-request.json').read_text());data=json.loads(template['messages'][0]['content']);data['article_blocks']=blocks
 data['scope']={'allowed_ids':[b['id'] for b in targets],'editing_roles':[{'id':b['id'],'role':role} for b,role in zip(targets,['リード','推奨商材の冒頭','推奨商材の表以降の補足'])],'instruction':'リードは読者の状況を決めつけずに気持ちを受け止め、商材冒頭はその商材がどんな望みに応えるかを結論から説明する。補足は潜在的な不安に具体的な選択肢を丁寧に提案する。既存の事実・結論・条件は変えず、指定3段落を自然な文章に磨く。長さ自体を評価せず、機能の羅列や押し付けを避ける。'}
 template['system'] = template['system'] + '\n' + EFFECT_CLAIM_POLICY + '\n' + READABILITY_POLICY
 template['messages'][0]['content']=json.dumps(data,ensure_ascii=False)
 (root/f'reader_{kind}_edit-request.json').write_text(json.dumps(template,ensure_ascii=False));(root/f'reader_{kind}_edit-scope.json').write_text(json.dumps(data['scope'],ensure_ascii=False,indent=2));print(kind,[b['id'] for b in targets])
if __name__=='__main__':prepare(*sys.argv[1:])
