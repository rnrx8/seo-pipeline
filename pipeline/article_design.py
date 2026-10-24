"""Trace intended reader answers into actual sections, then use existing coverage QA."""
import copy
import hashlib
from .editorial_blueprint import require_projection,fingerprint
from .content_edits import content_blocks
from .section_identity import bind_sections

VERSION='article-reader-design-v4-chapter-outcomes'
REVIEW_POLICY='''本文のdecision_reviewsでは、article_designの読者判断ごとに必要回答の実現を個別に確認する。
構成・reader_correspondenceは設計と位置対応であり、実際の回答の証明ではない。本文の具体的記述をreasonに示し、比較・選び分け・手順・判断に必要な説明が実在するか確認する。lead_directionは設計指示であり、実際のリードの主題・対象・読む動機を照合する資料。原設計の弱さをそのまま合格基準にせず、共通の対象・選ぶ結論・章の約束の確認も行う。設計した感情の文言そのものや、毎章の共感・CTA文は要求しない。章の役割に応じた理解・判断・説明を確認し、商材への誘導があること自体を不合格にしない。
不足はmissing_requirementsと、補うべき実在する段落IDのaffected_blocksへ記録する。全体coverageがpassでも主要な必要回答の不足を相殺しない。'''
from .lead_review import POLICY as LEAD_REVIEW_POLICY
REVIEW_POLICY+='\n'+LEAD_REVIEW_POLICY
from .article_progression import POLICY as PROGRESSION_REVIEW_POLICY
REVIEW_POLICY+='\n'+PROGRESSION_REVIEW_POLICY


def design_context(artifact):
    value=require_projection(artifact)
    if value is None:return None
    ctx=artifact['meta']['blueprint_context']
    fields=('id','parent_id','level','title','need_ids','decision_ids','research_ids','question','conclusion','answer_elements','reader_progression','cta')
    from .lead_authoring import focus_context
    return {'version':VERSION,'blueprint_sha256':fingerprint(value),'lead_direction':value['lead_direction'],
        'reader_focus':focus_context(value['reader_focus'],ctx['reader_design']),
        'reader_decisions':copy.deepcopy(ctx['reader_decisions']),
        'sections':[{k:copy.deepcopy(s[k]) for k in fields} for s in value['sections']]}


def part_reader_brief(blueprint, h2_titles):
    """Bring this part's own reader states beside its final writing instruction.

    Use the validated design, rather than inferring feelings or copying a global
    persona into every child. Null child progressions remain inherited context.
    """
    if not blueprint or not h2_titles:return ''
    import json
    titles=set(h2_titles);selected=False;rows=[]
    for section in blueprint['sections']:
        if section['level']==2:selected=section['title'] in titles
        progression=section.get('reader_progression')
        if not selected or not progression:continue
        before=progression['before'];after=progression['after']
        row={'section_id':section['id'],'heading':section['title'],
             'question':section['question'],'answer':section['conclusion'],
             'open_questions':before['open_questions'],
             'reader_feeling':before['emotion'],
             'outcome':{k:v for k,v in after.items() if v}}
        rows.append(row)
    if not rows:return ''
    return ('\n\n【今回のPARTで実現する読者への回答】\n'
        '以下はこのPARTだけの編集設計。完成文や引用ではない。reader_feelingがある章では、'
        'その章固有の迷い・心配・期待を読者が自分のこととして理解できる言葉で短く受け止め、'
        'answerや具体策へつなぐ。具体策の提示だけで気持ちまで受け止めた扱いにしない。'
        '仮説は読者全員の気持ちと断定せず、分析用の語句を転記しない。'
        'reader_feelingが空の章に感情を作り足さず、同じ共感文や固定位置を全章に課さない。\n'
        +json.dumps(rows,ensure_ascii=False,separators=(',',':')))


def correspondence(text, outline, design, binding=None):
    """Location mapping is diagnostic; no semantic pass is inferred from it."""
    from .section_identity import valid_binding
    binding=binding if binding and valid_binding(text,outline,binding) else bind_sections(text,outline)
    blocks=content_blocks(text)
    # Actual paragraph ranges keep repeated text anchored to its own occurrence.
    ranges=[];cursor=0
    for block in blocks:
        start=text.index(block['text'],cursor);cursor=start+len(block['text']);ranges.append((block['id'],start,cursor))
    import re
    headings=list(re.finditer(r'^(#{2,4})\s+(.+)$',text,re.M))
    rows=[]
    for index,expected in enumerate(design['sections']):
        located=[];ids=[]
        for actual_index,entry in enumerate(binding['entries']):
            if entry.get('outline_index')!=index:continue
            located.append(entry['id'])
            start=headings[actual_index].start();end=headings[actual_index+1].start() if actual_index+1<len(headings) else len(text)
            ids.extend(i for i,a,b in ranges if a<end and b>start)
        rows.append({'blueprint_id':expected['id'],'article_section_ids':located,'block_ids':list(dict.fromkeys(ids)),
            'need_ids':expected['need_ids'],'decision_ids':expected['decision_ids'],
            'status':'mapped_not_semantically_verified' if located else 'unresolved_location'})
    from .article_progression import add_review_ranges
    return add_review_ranges({'version':VERSION,'blueprint_sha256':design['blueprint_sha256'],'text_sha256':hashlib.sha256(text.encode()).hexdigest(),
        'outline_sha256':hashlib.sha256(outline.encode()).hexdigest(),'sections':rows},design)


def review_config(config, design):
    from .reader_decisions import review_schema
    result=review_schema(config,design['reader_decisions'],locations=True)
    if 'lead_direction' in design:
        from .lead_review import output_config
        result=output_config(result)
    from .article_progression import output_config
    return output_config(result,design)


def checked_result(checks, value, design, text, *, outline=None):
    from .reader_decisions import checked_reviews
    rows=checked_reviews(value,design['reader_decisions'],block_ids={b['id'] for b in content_blocks(text)})
    result=copy.deepcopy(checks);coverage=next((c for c in result if c['key']=='coverage'),None)
    if coverage is None:
        from .content_quality import ContentQualityError
        raise ContentQualityError('本文の回答品質確認が不足しています。')
    for decision in design['reader_decisions']:
        row=rows[decision['id']]
        if row['status']!='fail' or decision['role']=='supporting':continue
        coverage['status']='fail'
        coverage['reason']+='\n'+decision['question']+'：'+row['reason']
        coverage.setdefault('affected_blocks',[])
        for loc in row['affected_blocks']:
            if loc not in coverage['affected_blocks']:coverage['affected_blocks'].append(copy.deepcopy(loc))
        coverage['research_ids']=sorted(set(coverage.get('research_ids',[]))|set(decision['research_ids']))
    if 'lead_direction' in design:
        from .lead_review import checked,article_block_ids,LABELS
        for key,row in checked(value,article_block_ids(text)).items():
            if row['status']!='fail':continue
            coverage['status']='fail';addition=LABELS[key]+'：'+row['reason']
            if addition not in coverage['reason']:coverage['reason']+='\n'+addition
            for loc in row['affected_blocks']:
                if loc not in coverage['affected_blocks']:coverage['affected_blocks'].append(copy.deepcopy(loc))
    from .article_progression import checked
    if outline is None:
        outline='\n\n'.join('#'*(s['level']+1)+' H'+str(s['level'])+'：'+s['title'] for s in design.get('sections',[]))
    for issue in checked(value,design,correspondence(text,outline,design),text):
        coverage['status']='fail'
        coverage['reason']+='\n'+issue['title']+'：'+issue['reason']
        coverage.setdefault('affected_blocks',[])
        coverage['research_ids']=sorted(set(coverage.get('research_ids',[]))|set(issue['research_ids']))
        for loc in issue['affected_blocks']:
            if loc not in coverage['affected_blocks']:coverage['affected_blocks'].append(copy.deepcopy(loc))
    return result
