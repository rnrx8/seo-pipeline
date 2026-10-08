"""Topic-to-section handoff within the existing outline generation/review."""
import re
from .article_quality import outline_sections

POLICY = '''【構成の論点選定と章への割当て】
検索意図への直接回答・ユーザー指定を先に確保し、調査計画のessential/important/supportingと理由を引き継ぐ。確認済み情報があることだけを掲載理由にしない。essentialは回答を失わない形で必ず割り当て、importantは主要な判断に使い、supportingは主要な疑問の理解に役立つ場合だけ採用する。調査の優先度を文字数や自社訴求の都合で変更しない。
各H2/H3/H4には「読者の疑問」「回答要素」「対応論点」を記載する。回答要素は、その節で説明する事実・違い・理由・判断条件を具体的に列挙する短い指示書とする。「詳しく解説」「安全性を説明」だけで済ませず、本文そのものや根拠全文までは作らない。対応論点は入力資料の質問IDを使い、例「- 対応論点：q01, q02」のように書く。
H2は章全体の回答範囲、H3/H4はその内訳を担う。概要の章は後続の詳細章で何を説明するか区別し、同じ内容を二度詳述しない。独立した理由やリスクを詳述する場合は、論点ごとに子節を設計する。章の案内だけを独立した節にしない。まとめは既出の回答を要約する。
入力の回答が採用不可または省略となっている論点を、対応IDを書くだけで充足としない。必須論点の実際の回答要素と根拠が揃うか、既存の構成確認で照合する。importantを省く場合は主要な判断が維持される理由を構成内の編集メモへ短く記録する。supportingを全件採用したり、文字数を埋めるために候補を追加しない。
章の内容と子節を確定してから、その説明に必要な分量を見積もる。競合文字数は全体の規模を考える参考であり、合計を一致させるために章・調査・反復説明を増やさない。ユーザー指定の分量がある場合は、必須回答を確保して重複と低優先度の補足から調整する。
'''


def topic_assignments(text):
    result = []
    for section in outline_sections(text):
        body = text[section['start']:section['end']]
        lines = re.findall(r'^\s*-\s*対応論点\s*[：:]\s*(.+)$', body, re.M)
        ids = list(dict.fromkeys(re.findall(r'\bq\d+\b', ' '.join(lines))))
        result.append({'title': section['title'], 'level': section['level'], 'ids': ids})
    return result


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
            if not re.search(r'^\s*-\s*' + field + r'\s*[：:]\s*\S', body, re.M):
                issues.append({'key': 'missing_outline_answer_design', 'title': section['title'], 'field': field})
    return issues
