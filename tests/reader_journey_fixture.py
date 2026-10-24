"""Synthetic addresses for transport tests, never semantic quality evidence."""
from pipeline.article_design import correspondence
from pipeline.content_edits import content_blocks
from pipeline.reader_journey import chapter_evidence


def journey(evidence):
    sid=next(iter(evidence));quote=evidence[sid][0]
    return {'reader_state':'合成した章の状況と気持ち。意味品質の証明ではない。',
        'attunement':{'status':'pass','reason':'合成した独立確認','evidence':[{'id':sid,'quote':quote}]},
        'lived_value':{'status':'pass','reason':'合成した独立確認','evidence':[{'id':sid,'quote':quote}]},
        'guidance':{'status':'pass','reason':'合成した独立確認','evidence':[{'id':sid,'quote':quote}]}}


def outline_journey(ctx,sid):return journey(chapter_evidence(sid,ctx['journey_sections']))


def article_journeys(design,text,outline=None):
    outline=outline or '\n\n'.join('#'*(s['level']+1)+' H'+str(s['level'])+'：'+s['title'] for s in design['sections'])
    mapping=correspondence(text,outline,design)
    blocks={b['id']:[b['text']] for b in content_blocks(text)}
    return {r['blueprint_id']:journey({key:blocks[key] for key in r['review_block_ids']})
            for r in mapping['sections'] if r['review_block_ids']}
