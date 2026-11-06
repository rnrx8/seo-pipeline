"""Original registered documents from the frozen assignment, not verified facts."""
import copy
import hashlib
import re
import unicodedata


def documents(product_input=None, *, questions=None):
    if product_input is None:
        from .editorial_contract import CURRENT
        active = CURRENT.get()
        product_input = active['product_input'] if active else {}
    rows = []
    def normalized(value):
        return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or '')))
    asked = [normalized(q.get('question')) for q in questions] if questions is not None else None
    explicit = {mid for q in (questions or []) for mid in q.get('material_ids', [])}
    for material in product_input.get('materials', []):
        if material.get('origin') != 'registered_material' or not material.get('text'):
            continue
        title = normalized(material.get('title'))
        mid = material['id']
        if asked is not None and mid not in explicit and not any(
                normalized(mid) in question or (len(title) >= 4 and title in question)
                for question in asked):
            continue
        row = copy.deepcopy(material)
        row.update(text_sha256=hashlib.sha256(row['text'].encode()).hexdigest(),
                   truncated=False, verification='supplied_not_verified')
        rows.append(row)
    return rows


def attach(payload, *, product_input=None, questions=None):
    """Unrelated subject requests stay byte-identical for paid cache reuse."""
    rows = documents(product_input, questions=questions)
    if rows:
        payload['registered_materials'] = rows
    return payload
