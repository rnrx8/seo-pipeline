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


def carry_sections(before, after, outline, binding):
    """Only for exact local edits: no heading insertion, deletion or level changes.

    These edits act on existing sections; their IDs survive title corrections.
    Full rewrites must rebind by title rather than guess identities by position.
    """
    if not valid_binding(before, outline, binding):
        raise ValueError('section identity is stale')
    old, new = article_sections(before), article_sections(after)
    if [s['level'] for s in old] != [s['level'] for s in new]:
        raise ValueError('content repair changed section topology')
    return {**binding, 'entries': [{**e, **{k: a[k] for k in ('title','level','parent')}}
                                   for e,a in zip(binding['entries'],new)]}


def unchanged_heading_binding(before, after, binding):
    keys = lambda text: [(s['level'], s['title'], s['parent']) for s in article_sections(text)]
    return binding if keys(before) == keys(after) else None
