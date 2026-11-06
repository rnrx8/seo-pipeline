"""Read JSON text, correcting only one provably superfluous closing delimiter."""
import hashlib
import json


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def _stack_before(text, end):
    stack, quoted, escaped = [], False, False
    for char in text[:end]:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '[{':
            stack.append(char)
        elif char in ']}':
            if not stack or stack.pop() != {']': '[', '}': '{'}[char]:
                raise ValueError('Earlier mismatched JSON delimiter')
    return stack, quoted


def decode(text, check_shape):
    """Never add/remove words, keys, values, quotes, commas or missing fields.

    An unexpected closing bracket at the exact decoder error position can be
    removed only outside strings, with an incompatible container stack, and
    only if the resulting complete object passes its canonical shape check.
    All other syntax/shape failures remain errors; no model request is sent.
    """
    try:
        value = json.loads(text, object_pairs_hook=_unique_object)
    except json.JSONDecodeError as error:
        pos = error.pos
        if pos >= len(text) or text[pos] not in ']}':
            raise
        stack, quoted = _stack_before(text, pos)
        closing = text[pos]
        if quoted or (stack and stack[-1] == {']': '[', '}': '{'}[closing]):
            raise error
        repaired = text[:pos] + text[pos + 1:]
        value = json.loads(repaired, object_pairs_hook=_unique_object)
        check_shape(value)
        return value, {
            'version': 'lossless-json-delimiter-v1',
            'operation': 'remove_unmatched_closing_delimiter',
            'offset': pos, 'removed': closing,
            'before_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'after_sha256': hashlib.sha256(repaired.encode()).hexdigest(),
            'wording_changed': False,
        }
    check_shape(value)
    return value, None
