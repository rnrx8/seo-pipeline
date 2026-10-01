"""Immutable paragraph addresses for audit findings and local corrections."""
import json
import re


def content_blocks(text: str) -> list[dict]:
    """Address immutable paragraphs/tables without asking a model to copy them."""
    parts = re.split(r'(\n[ \t]*\n)', text)
    return [{'id': f'block-{index:04d}', 'text': part}
            for index, part in enumerate(parts) if part.strip()]


def apply_block_edits(text: str, raw: str) -> str:
    from .content_quality import ContentQualityError
    raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip())
    try:
        edits = json.loads(raw)['edits']
        if not isinstance(edits, list) or not 1 <= len(edits) <= 80:
            raise ValueError('empty or excessive edits')
        blocks = {b['id']: b['text'] for b in content_blocks(text)}
        replacements = {}
        for edit in edits:
            block_id, new = edit['id'], edit['new']
            if not isinstance(block_id, str) or block_id not in blocks or block_id in replacements:
                raise ValueError('unknown or duplicate block id')
            if not isinstance(new, str) or not new.strip() or new == blocks[block_id]:
                raise ValueError('empty or unchanged replacement')
            replacements[block_id] = new
        parts = re.split(r'(\n[ \t]*\n)', text)
        result = ''.join(replacements.get(f'block-{index:04d}', part) for index, part in enumerate(parts))
        if not result.strip().startswith('#'):
            raise ValueError('article heading removed')
        return result
    except (ValueError, TypeError, KeyError) as exc:
        raise ContentQualityError(f'内容修正の段落IDを確認できません: {exc}') from exc

