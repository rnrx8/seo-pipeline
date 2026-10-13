"""Reject stale editorial briefs before paid downstream generation."""
import hashlib
from .reader_presentation import READER_PRESENTATION_POLICY
from .readability import READABILITY_POLICY


def current_policy():
    from .step_outline import SYSTEM_PROMPT, USER_TEMPLATE
    from .generation_context import POLICY_VERSION
    contract = '\n'.join(('outline-editorial-v2-topic-design-reference-length', POLICY_VERSION, SYSTEM_PROMPT, USER_TEMPLATE,
                          READER_PRESENTATION_POLICY, READABILITY_POLICY))
    return hashlib.sha256(contract.encode()).hexdigest()


def _require_current_outline_strict(artifact):
    from .content_quality import ContentQualityError
    if (artifact.get('meta') or {}).get('editorial_policy') != current_policy():
        raise ContentQualityError('構成が現在の編集方針に対応していません。旧本文・調査記録は保持し、構成を更新して再検証してから再開してください。自動再生成はしません。')


def require_current_outline(*args, **kwargs):
    from .quality_warnings import enabled, warn
    from .content_quality import ContentQualityError
    try:
        return _require_current_outline_strict(*args, **kwargs)
    except ContentQualityError as exc:
        if not enabled(): raise
        warn(str(exc))
