"""Immutable paragraph addresses for audit findings and local corrections."""
import copy
import json
import re

REPAIR_OUTPUT_CONFIG = {'format': {'type':'json_schema', 'schema': {
    'type':'object', 'properties': {'edits': {'type':'array','items': {
        'type':'object','properties': {'id':{'type':'string'},'new':{'type':'string'}},
        'required':['id','new'],'additionalProperties':False}}},
    'required':['edits'],'additionalProperties':False}}}


def scoped_repair_output_config(target_block_ids: list[str]) -> dict:
    """Constrain generated edits to writable IDs, excluding read-only context."""
    from .content_quality import ContentQualityError
    if not target_block_ids or any(not isinstance(value, str) for value in target_block_ids):
        raise ContentQualityError('局所修正の対象段落IDがありません。')
    config = copy.deepcopy(REPAIR_OUTPUT_CONFIG)
    config['format']['schema']['properties']['edits']['items']['properties']['id']['enum'] = list(dict.fromkeys(target_block_ids))
    return config


def content_blocks(text: str) -> list[dict]:
    """Address immutable paragraphs/tables without asking a model to copy them."""
    parts = re.split(r'(\n[ \t]*\n)', text)
    return [{'id': f'block-{index:04d}', 'text': part}
            for index, part in enumerate(parts) if part.strip()]


def validated_block_edits(text: str, raw: str) -> list[dict]:
    """Validate every submitted address, returning only actual changes."""
    from .content_quality import ContentQualityError
    raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip())
    try:
        edits = json.loads(raw)['edits']
        if not isinstance(edits, list) or not 1 <= len(edits) <= 80:
            raise ValueError('empty or excessive edits')
        blocks = {b['id']: b['text'] for b in content_blocks(text)}
        replacements = {}
        seen = set()
        for edit in edits:
            block_id, new = edit['id'], edit['new']
            if not isinstance(block_id, str) or block_id not in blocks or block_id in seen:
                raise ValueError('unknown or duplicate block id')
            seen.add(block_id)
            if not isinstance(new, str):
                raise ValueError('invalid replacement')
            if new == blocks[block_id]:
                continue
            if not new.strip() and (new != '' or re.search(r'^\s*(?:#{1,6}\s|\|)', blocks[block_id], re.M)):
                raise ValueError('cannot remove headings or tables')
            replacements[block_id] = new
        if not replacements:
            raise ValueError('no effective edits')
        return [{'id':block_id,'new':new} for block_id,new in replacements.items()]
    except (ValueError, TypeError, KeyError) as exc:
        raise ContentQualityError(f'内容修正の段落IDを確認できません: {exc}') from exc


def apply_block_edits(text: str, raw: str) -> str:
    from .content_quality import ContentQualityError
    replacements = {edit['id']:edit['new'] for edit in validated_block_edits(text,raw)}
    try:
        parts = re.split(r'(\n[ \t]*\n)', text)
        result = ''.join(replacements.get(f'block-{index:04d}', part) for index, part in enumerate(parts))
        if not result.strip().startswith('#'):
            raise ValueError('article heading removed')
        return result
    except (ValueError, TypeError, KeyError) as exc:
        raise ContentQualityError(f'内容修正の段落IDを確認できません: {exc}') from exc
