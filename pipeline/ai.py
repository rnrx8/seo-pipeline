"""Shared Claude API helper with rate-limit retry and per-step model config."""
import time
import anthropic

# Centralized model config per step
STEP_CONFIG: dict[str, dict] = {
    "search_intent": {"model": "claude-opus-4-7",   "max_tokens": 4096},
    "outline":       {"model": "claude-opus-4-7",   "max_tokens": 8000},
    "fact_sheet":    {"model": "claude-sonnet-4-6", "max_tokens": 30000},
    "article":       {"model": "claude-opus-4-7",   "max_tokens": 8000},
    "review":        {"model": "claude-sonnet-4-6", "max_tokens": 28000},
    "fact_review":   {"model": "claude-sonnet-4-6", "max_tokens": 28000},
}


def get_step_config(step: str) -> tuple[str, int]:
    """Return (model, max_tokens) for the given step name."""
    cfg = STEP_CONFIG.get(step)
    if cfg is None:
        raise ValueError(f"Unknown step: {step!r}. Add it to STEP_CONFIG in ai.py.")
    return cfg["model"], cfg["max_tokens"]


def create_with_retry(client: anthropic.Anthropic, max_retries: int = 5, **kwargs):
    """Call client.messages.create (streaming) with exponential backoff on rate limit errors.

    Uses streaming to support large max_tokens values (>10min threshold).
    Returns a standard Message object identical to non-streaming create().
    """
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
