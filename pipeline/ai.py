"""Shared Claude API helper with rate-limit retry and per-step model config."""
import os
import time
import anthropic

OPUS_THINKING_ALLOWANCE = 8000

# Centralized model config per step
STEP_CONFIG: dict[str, dict] = {
    "search_intent": {"model": "claude-opus-5-5",   "max_tokens": 10000},
    "query_attrs":   {"model": "claude-haiku-4-5-20251001", "max_tokens": 800},
    "intent_chains": {"model": "claude-haiku-4-5-20251001", "max_tokens": 3000},
    "outline":       {"model": "claude-opus-5-5",   "max_tokens": 16000},
    "fact_sheet":    {"model": "claude-sonnet-5-5", "max_tokens": 39000},
    "article":       {"model": "claude-opus-5-5",   "max_tokens": 16000},
    "review":        {"model": "claude-sonnet-4-6", "max_tokens": 50000},
    "fact_review":   {"model": "claude-sonnet-4-6", "max_tokens": 28000},
    "service_map":   {"model": "claude-sonnet-4-6", "max_tokens": 2048},
}


def astra_review_enabled() -> bool:
    mode = os.getenv("ARTICLE_REVIEW_PROVIDER", "astra")
    if mode not in ("astra", "sonnet", "tiered"):
        raise ValueError("ARTICLE_REVIEW_PROVIDER must be astra, sonnet or tiered")
    return mode in ("astra", "tiered")


def tiered_review_enabled():
    return os.getenv("ARTICLE_REVIEW_PROVIDER", "astra") == "tiered"


def hybrid_writing_enabled():
    mode = os.getenv('ARTICLE_WRITING_FLOW', 'legacy')
    if mode not in ('legacy', 'sonnet_opus'):
        raise ValueError('Unsupported ARTICLE_WRITING_FLOW')
    if mode == 'sonnet_opus' and not tiered_review_enabled():
        raise ValueError('sonnet_opus requires tiered quality review')
    return mode == 'sonnet_opus'


def is_openai_model(model):
    return model in ("gpt-6-astra", "gpt-6.1-sol", "gpt-6-luna")


def get_step_config(step: str) -> tuple[str, int]:
    """Return (model, max_tokens) for the given step name."""
    hybrid = hybrid_writing_enabled()
    draft_model = os.getenv('ARTICLE_DRAFT_MODEL', '').strip()
    if step == 'article' and draft_model:
        if draft_model not in ('claude-sonnet-5-5', 'claude-opus-5-5', 'claude-opus-4-8'):
            raise ValueError('Unsupported ARTICLE_DRAFT_MODEL')
        return draft_model, STEP_CONFIG['article']['max_tokens']
    if hybrid:
        if step == 'article':
            return ('claude-sonnet-5-5', STEP_CONFIG['article']['max_tokens'])
        if step == 'content_repair':
            return ('claude-opus-5-5', 6000)
    if tiered_review_enabled() and step in ("content_audit", "content_repair", "review"):
        return ("gpt-6.1-sol", 6000 if step != "content_repair" else 10000)
    if step in ("content_audit", "content_repair"):
        return ("gpt-6-astra", 24000) if astra_review_enabled() else ("claude-sonnet-4-6", 50000)
    cfg = STEP_CONFIG.get(step)
    if cfg is None:
        raise ValueError(f"Unknown step: {step!r}. Add it to STEP_CONFIG in ai.py.")
    model = cfg["model"]
    if step in ("search_intent", "outline", "article"):
        model = os.getenv("ARTICLE_GENERATION_MODEL", model)
        if model not in ("claude-opus-5-5", "claude-opus-4-8"):
            raise ValueError("Unsupported ARTICLE_GENERATION_MODEL")
    return model, cfg["max_tokens"]


def message_text(message) -> str:
    """Thinking blocks can precede text on Opus 5.5; never read by position."""
    return "\n".join(b.text for b in message.content
                     if getattr(b, "type", "text") == "text" and hasattr(b, "text"))


def validate_model_credentials(job: dict) -> None:
    if tiered_review_enabled() and not os.getenv('QUALITY_BUDGET_DIR', '').strip():
        from .content_quality import ContentQualityError
        raise ContentQualityError('段階式確認の予算保存先が未設定です。生成開始前に停止しました。')
    if astra_review_enabled() and not os.getenv('OPENAI_API_KEY', '').strip():
        from .content_quality import ContentQualityError
        raise ContentQualityError('品質確認に必要なOPENAI_API_KEYが未設定です。生成開始前に停止しました。')

    if tiered_review_enabled():
        from .quality_budget import CLAUDE_RATES, CLAUDE_USAGE_RATES, RATES
        from .content_quality import ContentQualityError
        models = {get_step_config(step)[0] for step in (*STEP_CONFIG, 'content_audit', 'content_repair')}
        supported = (CLAUDE_RATES.keys() & CLAUDE_USAGE_RATES.keys()) | RATES.keys()
        if models - supported:
            raise ContentQualityError('生成モデルと費用定義が一致しません。課金開始前に設定を確認してください。')
        from .research_search import require_search_config
        require_search_config()


def prepare_provider_request(request, *, output_budget_includes_thinking=False):
    """Pure wire normalization shared by sending and saved-input comparison."""
    import copy
    from .editorial_contract import enrich_request
    kwargs = enrich_request(copy.deepcopy(request))
    if is_openai_model(kwargs.get("model")):
        return kwargs,None
    from .blueprint_transport import prepare
    kwargs, original_blueprint_schema = prepare(kwargs)
    if kwargs.get("model") in ("claude-opus-5-5", "claude-sonnet-5-5"):
        kwargs["output_config"] = {"effort": "medium", **kwargs.get("output_config", {})}
        # Existing budgets were for visible text only; reserve room for thinking.
        if kwargs["model"] == "claude-opus-5-5" and not output_budget_includes_thinking:
            kwargs["max_tokens"] = min(128000, kwargs["max_tokens"] + OPUS_THINKING_ALLOWANCE)
    return kwargs,original_blueprint_schema


def create_with_retry(client: anthropic.Anthropic, max_retries: int = 5, *, output_budget_includes_thinking=False, **kwargs):
    """Stream a provider request; preserve durable expenditure and saved inputs."""
    kwargs, original_blueprint_schema=prepare_provider_request(kwargs,
        output_budget_includes_thinking=output_budget_includes_thinking)
    if is_openai_model(kwargs.get("model")):
        from .initial_authoring import record_request
        record_request(kwargs)
        from .openai_review import create_review_response
        return create_review_response(**kwargs)
    from .blueprint_transport import validate
    if client is None:
        client = anthropic.Anthropic(max_retries=0)
    from .initial_authoring import record_request
    record_request(kwargs)
    if tiered_review_enabled():
        from .quality_budget import reserve_claude, settle
        def count_input(payload):
            fields={k:payload[k] for k in ('model','messages','system','thinking','output_config','tools','tool_choice','cache_control') if k in payload}
            return client.with_options(max_retries=0).messages.count_tokens(**fields,timeout=30).input_tokens
        reservation=reserve_claude(kwargs,input_counter=count_input)
        accounted=False
        try:
            with client.with_options(max_retries=0).messages.stream(**kwargs) as stream:
                result=stream.get_final_message()
            settle(reservation,{**result.usage.model_dump(),'stop_reason':getattr(result,'stop_reason',None)});accounted=True
            validate(result, original_blueprint_schema)
            return result
        finally:
            if not accounted:settle(reservation)
    wait = 30
    for attempt in range(max_retries):
        try:
            with client.messages.stream(**kwargs) as stream:
                result = stream.get_final_message()
            validate(result, original_blueprint_schema)
            return result
        except anthropic.RateLimitError:
            if attempt == max_retries - 1:
                raise
            print(f"  [rate limit] waiting {wait}s before retry ({attempt + 1}/{max_retries})...")
            time.sleep(wait)
            wait = min(wait * 2, 120)
