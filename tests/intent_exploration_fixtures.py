"""Synthetic contract data; these are not generated quality examples."""
import copy

from pipeline.intent_exploration import VERSION


def attach_exploration(value, keyword):
    result = copy.deepcopy(value)
    support = {'kind': 'hypothesis', 'source': '', 'quote': '', 'reason': 'テスト用の編集仮説。実態の観測ではない。'}
    branch = {
        'id': 'h01', 'keyword_parts': [keyword],
        'context': {'when': '手段を選ぶ前', 'where': '', 'who': '初めて比較する人という仮説',
                    'what': '手段ごとの違いを知りたい', 'why': '利用場面との適合を判断したい', 'how': '候補を比較する'},
        'links': [
            {'from': '手段ごとの違いを知りたい', 'question': 'why', 'to': '生活に合う方法で続けたい', 'support': copy.deepcopy(support)},
            {'from': '生活に合う方法で続けたい', 'question': 'outcome', 'to': '無理なく必要な相手との関係を築ける', 'support': copy.deepcopy(support)}],
        'root_desire': {'category': 'belonging', 'meaning': '無理なく人とつながれること', 'stop_reason': '関係を求める望みとして整理した。実態は未確認。'},
        'alternative': {'option': '既存の方法を続ける', 'difference': '現在の方法で満たせない点は入力だけでは不明', 'support': copy.deepcopy(support)},
        'concrete_material': '生活に組み込みやすい方法を比べる場面例',
        'article_effect': '方法の列挙で終わらず、利用場面と続けやすさの違いを説明する',
        'unknowns': ['利用場所と現在の方法は不明']}
    result['exploration'] = {'version': VERSION, 'mode': 'exploratory', 'scope_reason': '方法選択の背景が比較軸に関係するため',
                             'keyword_parts': [keyword], 'branches': [branch]}
    for need in result['selected']:
        need['hypothesis_ids'] = ['h01']
    return result
