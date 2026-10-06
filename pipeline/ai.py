"""Shared Claude API helper with rate-limit retry and per-step model config."""
import os
import time
import anthropic

# Centralized model config per step
STEP_CONFIG: dict[str, dict] = {
    "search_intent": {"model": "claude-opus-5-5",   "max_tokens": 6000},
    "query_attrs":   {"model": "claude-haiku-4-5-20251001", "max_tokens": 800},
    "intent_chains": {"model": "claude-haiku-4-5-20251001", "max_tokens": 3000},
    "outline":       {"model": "claude-opus-5-5",   "max_tokens": 16000},
    "fact_sheet":    {"model": "claude-sonnet-4-6", "max_tokens": 30000},
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


def is_openai_model(model):
    return model in ("gpt-6-astra", "gpt-6.1-sol", "gpt-6-luna")


def get_step_config(step: str) -> tuple[str, int]:
    """Return (model, max_tokens) for the given step name."""
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
        models = {get_step_config(step)[0] for step in STEP_CONFIG}
        supported = (CLAUDE_RATES.keys() & CLAUDE_USAGE_RATES.keys()) | RATES.keys()
        if models - supported:
            raise ContentQualityError('生成モデルと費用定義が一致しません。課金開始前に設定を確認してください。')
        from .research_search import require_search_config
        require_search_config()


def create_with_retry(client: anthropic.Anthropic, max_retries: int = 5, **kwargs):
    """Call client.messages.create (streaming) with exponential backoff on rate limit errors.

    Uses streaming to support large max_tokens values (>10min threshold).
    Returns a standard Message object identical to non-streaming create().
    """
    if is_openai_model(kwargs.get("model")):
        from .openai_review import create_review_response
        return create_review_response(**kwargs)
    if kwargs.get("model") == "claude-opus-5-5":
        kwargs["output_config"] = {"effort": "medium", **kwargs.get("output_config", {})}
        # Existing budgets were for visible text only; reserve room for thinking.
        kwargs["max_tokens"] = min(128000, kwargs["max_tokens"] + 8000)
    if tiered_review_enabled():
        from .quality_budget import reserve_claude, settle
        def count_input(payload):
            fields={k:payload[k] for k in ('model','messages','system','thinking','output_config') if k in payload}
            return client.with_options(max_retries=0).messages.count_tokens(**fields,timeout=30).input_tokens
        reservation=reserve_claude(kwargs,input_counter=count_input)
        accounted=False
        try:
            with client.with_options(max_retries=0).messages.stream(**kwargs) as stream:
                result=stream.get_final_message()
            settle(reservation,result.usage.model_dump());accounted=True
            return result
        finally:
            if not accounted:settle(reservation)
    wait = 30
    for attempt in range(max_retries):
        try:
            with client.messages.stream(**kwargs) as stream:
                return stream.get_final_message()
        except anthropic.RateLimitError:
            if attempt == max_retries - 1:
                raise
            print(f"  [rate limit] waiting {wait}s before retry ({attempt + 1}/{max_retries})...")
            time.sleep(wait)
            wait = min(wait * 2, 120)
