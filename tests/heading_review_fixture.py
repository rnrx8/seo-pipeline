"""Synthetic protocol receipts for tests unrelated to heading semantics."""
import json
from pipeline.heading_answers import entries


def reviews(text):
    return {s['id']: {'heading': {'status': 'pass', 'reason': '合成した見出し確認'},
                     'opening': {'status': 'pass', 'reason': '合成した第一文確認'},
                     'opening_quote': s['opening_quote']} for s in entries(text)}


def with_heading_reviews(value, request):
    payload = json.loads(request['messages'][0]['content'])
    if payload.get('heading_answer_entries'):
        value['heading_answer_reviews'] = {s['id']: {
            'heading': {'status': 'pass', 'reason': '合成した見出し確認'},
            'opening': {'status': 'pass', 'reason': '合成した第一文確認'},
            'opening_quote': s['opening_quote']} for s in payload['heading_answer_entries']}
    return value
