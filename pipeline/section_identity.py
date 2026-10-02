"""Stable outline identities carried in artifact metadata, never reader prose."""
import hashlib
from .article_quality import article_sections, outline_sections, heading_matches, delivery_heading_matches


def _hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def bind_sections(text, outline, contract=None):
    actual = article_sections(text)
    expected = outline_sections(outline)
    used = set()
    entries = []
    for index, section in enumerate(actual):
        matches = [i for i, e in enumerate(expected) if i not in used
                   and section['level'] == e['level'] and delivery_heading_matches(section, e, contract)
                   and (e['level'] == 2 or heading_matches(section['parent'] or '', e['parent'] or ''))]
        outline_index = matches[0] if len(matches) == 1 else None
        if outline_index is not None:
            used.add(outline_index)
        entries.append({'id': f'section-{outline_index:03d}' if outline_index is not None else f'extra-{index:03d}',
                        'outline_index': outline_index, 'level': section['level'], 'title': section['title'],
                        'parent': section['parent']})
    return {'version': 1, 'outline_sha256': _hash(outline), 'entries': entries}


def valid_binding(text, outline, binding):
    if not isinstance(binding, dict) or binding.get('outline_sha256') != _hash(outline):
        return False
    entries = binding.get('entries', [])
    actual = article_sections(text)
    if len(entries) != len(actual):
        return False
    ids = [e.get('id') for e in entries]
    if len(set(ids)) != len(ids):
        return False
    indices = [e.get('outline_index') for e in entries if e.get('outline_index') is not None]
    expected = outline_sections(outline)
    if len(indices) != len(set(indices)) or any(type(i) is not int or not 0 <= i < len(expected) for i in indices):
        return False
    return all(all(e.get(k) == a[k] for k in ('level', 'title', 'parent'))
               and (e.get('outline_index') is None or e['level'] == expected[e['outline_index']]['level'])
               for e, a in zip(entries, actual))


def carry_sections(before, after, outline, binding, *, allow_h4=False):
    """Only for exact local edits: no heading insertion, deletion or level changes.

    These edits act on existing sections; their IDs survive title corrections.
    Full rewrites must rebind by title rather than guess identities by position.
    """
    if not valid_binding(before, outline, binding):
        raise ValueError('section identity is stale')
    old, new = article_sections(before), article_sections(after)
    if allow_h4 and [s["level"] for s in old] != [s["level"] for s in new]:
        entries, cursor, parent_level = [], 0, None
        used_ids = {e['id'] for e in binding['entries']}
        for index, section in enumerate(new):
            expected = old[cursor] if cursor < len(old) else None
            is_existing = expected is not None and section['level'] == expected['level'] and (
                section['level'] != 4 or section['title'] == expected['title'])
            if is_existing:
                entry = binding['entries'][cursor]
                entries.append({**entry, **{k:section[k] for k in ('title','level','parent')}})
                cursor += 1
                if section['level'] in (2,3): parent_level = section['level']
            elif section['level'] == 4 and parent_level == 3:
                new_id = 'layout-' + _hash(str(index) + section['title'])[:16]
                if new_id in used_ids: raise ValueError('duplicate layout identity')
                used_ids.add(new_id)
                entries.append({'id':new_id, 'outline_index':None,
                                **{k:section[k] for k in ('title','level','parent')}})
            else:
                raise ValueError('content repair changed protected section topology')
        if cursor != len(old): raise ValueError('content repair removed an existing section')
        result = {**binding, 'entries':entries}
        if not valid_binding(after, outline, result): raise ValueError('invalid repaired binding')
        return result
    if [s['level'] for s in old] != [s['level'] for s in new]:
        raise ValueError('content repair changed section topology')
    return {**binding, 'entries': [{**e, **{k: a[k] for k in ('title','level','parent')}}
                                   for e,a in zip(binding['entries'],new)]}


def unchanged_heading_binding(before, after, binding):
    keys = lambda text: [(s['level'], s['title'], s['parent']) for s in article_sections(text)]
    return binding if keys(before) == keys(after) else None
