"""Topic-to-section handoff within the existing outline generation/review."""
import re
from .article_quality import outline_sections

POLICY = '''【構成の論点選定と章への割当て】
検索意図への直接回答・ユーザー指定を先に確保し、調査計画のessential/important/supportingと理由を引き継ぐ。記事目的が設定されている場合は、論点の採否・説明の強弱・章の役割・順序へ反映する。CV目的では、必要な疑問や不安を解消したうえで確認済みの強みから選択・申込みを判断できる流れにする。認知目的では、テーマやサービスの理解・記憶に役立つ説明を重視する。その他の目的も登録内容に沿って設計し、目的未設定なら検索意図への回答を中心にする。目的への寄与は該当する章の回答要素で具体化し、全章への推薦・CTAや目的説明欄の追加を要求しない。記事目的を理由に検索意図への必須回答を省いたり、事実・条件を曲げたりしない。確認済み情報があることだけを掲載理由にしない。essentialは回答を失わない形で必ず割り当て、importantは主要な判断に使い、supportingは主要な疑問の理解と設定された目的に役立つ場合だけ採用する。調査の優先度を文字数や自社訴求の都合で変更しない。
各H2/H3/H4には「読者の疑問」「回答要素」「対応論点」を記載する。回答要素は、その節で説明する事実・違い・理由・判断条件を具体的に列挙する短い指示書とする。「詳しく解説」「安全性を説明」だけで済ませず、本文そのものや根拠全文までは作らない。対応論点は入力資料の質問IDを使い、例「- 対応論点：q01, q02」のように書く。
H2は章全体の回答範囲、H3/H4はその内訳を担う。概要の章は後続の詳細章で何を説明するか区別し、同じ内容を二度詳述しない。独立した理由やリスクを詳述する場合は、論点ごとに子節を設計する。章の案内だけを独立した節にしない。まとめは既出の回答を要約する。
入力の回答が採用不可または省略となっている論点を、対応IDを書くだけで充足としない。必須論点の実際の回答要素と根拠が揃うか、既存の構成確認で照合する。importantを省く場合は主要な判断が維持される理由を構成内の編集メモへ短く記録する。supportingを全件採用したり、文字数を埋めるために候補を追加しない。
章の内容と子節を確定してから、その説明に必要な分量を見積もる。競合文字数は全体の規模を考える参考であり、合計を一致させるために章・調査・反復説明を増やさない。ユーザー指定の分量がある場合は、必須回答を確保して重複と低優先度の補足から調整する。
'''


def topic_assignments(text):
    result = []
    for section in outline_sections(text):
        body = text[section['start']:section['end']]
        ids = []
        for match in re.finditer(r'\bq(\d+)(?:\s*[〜～–—-]\s*q(\d+))?\b', _design_field(body, '対応論点')):
            first, last = match[1], match[2]
            if last and 0 <= int(last) - int(first) <= 1000:
                width = max(len(first), len(last))
                ids.extend('q' + str(n).zfill(width) for n in range(int(first), int(last) + 1))
            else:
                ids.extend('q' + n for n in (first, last) if n)
        ids = list(dict.fromkeys(ids))
        result.append({'title': section['title'], 'level': section['level'], 'ids': ids})
    return result


def _design_field(body, name):
    """Read a design field independent of inline emphasis and list wrapping.

    Field names are metadata; a nested list is the field's value, not a new
    field. Stop at the next sibling so an empty answer cannot borrow roots
    or publication items from another field.
    """
    lines = body.splitlines()
    for i, line in enumerate(lines):
        plain = line.replace('**', '').replace('__', '')
        match = re.match(r'^([ \t]*)[-*+]\s+' + re.escape(name) + r'\s*(?:[：:]\s*(.*))?$', plain)
        if not match:
            continue
        value = [match.group(2) or '']
        indentation = len(match.group(1).expandtabs(4))
        for following in lines[i + 1:]:
            if not following.strip():
                continue
            leading = len(following) - len(following.lstrip(' \t'))
            if len(following[:leading].expandtabs(4)) <= indentation:
                break
            nested = re.sub(r'^\s*[-*+]\s*', '', following).strip()
            if nested.strip('*_ :：'):
                value.append(nested)
        return '\n'.join(value).strip()
    return ''


def assignment_issues(text, plan):
    questions = {q['id']: q for q in plan.get('items', [])}
    if not questions:
        return []
    assigned = {i for section in topic_assignments(text) for i in section['ids']}
    issues = [{'key': 'unknown_outline_topic', 'id': i} for i in sorted(assigned - questions.keys())]
    for i, q in questions.items():
        if (q.get('priority') == 'essential' or q.get('required') is True) and i not in assigned:
            issues.append({'key': 'unassigned_required_topic', 'id': i, 'question': q['question']})
    for section in outline_sections(text):
        body = text[section['start']:section['end']]
        for field in ('読者の疑問', '回答要素'):
            if not _design_field(body, field):
                issues.append({'key': 'missing_outline_answer_design', 'title': section['title'], 'field': field})
    return issues
