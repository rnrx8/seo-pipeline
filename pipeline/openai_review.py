"""Responses API adapter for the bounded final audit/repair, never a fallback."""
import os
import time
from types import SimpleNamespace

import requests


def create_review_response(*, model, max_tokens, system, messages, output_config):
    # Import lazily: content_quality also uses the shared AI helper.
    from .content_quality import ContentQualityError
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise ContentQualityError("Astra確認に必要なOPENAI_API_KEYが未設定です。")
    schema = output_config["format"]["schema"]
    payload = {"model": model, "instructions": system, "input": messages,
               "max_output_tokens": max(16000, max_tokens),
               "reasoning": {"effort": "high"}, "store": False,
               "text": {"format": {"type": "json_schema", "name": "article_quality",
                                    "strict": True, "schema": schema}}}
    for attempt in range(3):
        try:
            response = requests.post("https://api.openai.com/v1/responses",
                headers={"Authorization": "Bearer " + key}, json=payload, timeout=(15, 300))
        except requests.RequestException:
            raise ContentQualityError("Astra確認の通信に失敗しました。未確認の本文は完了にしません。") from None
        if response.status_code == 429:
            try:
                code = response.json().get("error", {}).get("code")
            except ValueError:
                code = None
            if code == "insufficient_quota":
                raise ContentQualityError("OpenAI APIの利用枠が不足しています。")
            if attempt < 2:
                time.sleep(10 * (attempt + 1))
                continue
        if response.status_code != 200:
            # Do not propagate response bodies, request headers or credentials.
            raise ContentQualityError(f"Astra確認に失敗しました（HTTP {response.status_code}）。")
        try:
            data = response.json()
            if data.get("status") != "completed":
                raise ContentQualityError("Astra確認の応答が未完了です。")
            parts = [part for item in data.get("output", []) if item.get("type") == "message"
                     for part in item.get("content", [])]
            if any(p.get("type") == "refusal" for p in parts):
                raise ContentQualityError("Astraが確認を完了できませんでした。")
            text = "\n".join(p["text"] for p in parts if p.get("type") == "output_text")
            if not text.strip():
                raise ContentQualityError("Astra確認の本文が空です。")
            usage = data["usage"]
            return SimpleNamespace(stop_reason="end_turn", model=data.get("model", model),
                id=data.get("id"), content=[SimpleNamespace(type="text", text=text)],
                usage=SimpleNamespace(input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"]))
        except (ValueError, KeyError, TypeError):
            raise ContentQualityError("Astra確認の応答形式が不正です。") from None
