"""Responses API adapter for the bounded final audit/repair, never a fallback."""
import json
import os
import time
from types import SimpleNamespace

import requests


def _completed_response(response, started):
    """Consume SSE privately; partial JSON is never accepted or published."""
    from .content_quality import ContentQualityError
    event_lines, received = [], 0
    for line in response.iter_lines():
        if time.monotonic() - started > 1200:
            raise ContentQualityError('Astra確認の処理時間上限を超えました。')
        if isinstance(line, bytes):
            line = line.decode('utf-8')
        received += len(line)
        if received > 8_000_000:
            raise ContentQualityError('Astra確認の応答サイズ上限を超えました。')
        if line.startswith('data:'):
            event_lines.append(line[5:].lstrip())
        elif not line and event_lines:
            raw = '\n'.join(event_lines)
            event_lines = []
            if raw == '[DONE]':
                break
            event = json.loads(raw)
            if event.get('type') == 'response.completed':
                return event['response']
            if event.get('type') in ('error', 'response.failed', 'response.incomplete'):
                raise ContentQualityError('Astra確認の応答が未完了です。')
    raise ContentQualityError('Astra確認の通信が完了通知前に終了しました。')


def create_review_response(*, model, max_tokens, system, messages, output_config):
    # Import lazily: content_quality also uses the shared AI helper.
    from .content_quality import ContentQualityError
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise ContentQualityError("Astra確認に必要なOPENAI_API_KEYが未設定です。")
    schema = output_config["format"]["schema"]
    payload = {"model": model, "instructions": system, "input": messages,
               "max_output_tokens": max(16000, max_tokens),
               "reasoning": {"effort": "high"}, "store": False, "stream": True,
               "text": {"format": {"type": "json_schema", "name": "article_quality",
                                    "strict": True, "schema": schema}}}
    for attempt in range(3):
        response = None
        started = time.monotonic()
        try:
            print('[astra] Starting streamed review/repair request', flush=True)
            response = requests.post("https://api.openai.com/v1/responses",
                headers={"Authorization": "Bearer " + key}, json=payload,
                stream=True, timeout=(15, 600))
            if response.status_code == 429:
                try:
                    code = response.json().get("error", {}).get("code")
                except ValueError:
                    code = None
                if code == "insufficient_quota":
                    raise ContentQualityError("OpenAI APIの利用枠が不足しています。")
                if attempt < 2:
                    response.close()
                    time.sleep(10 * (attempt + 1))
                    continue
            if response.status_code != 200:
                raise ContentQualityError(f"Astra確認に失敗しました（HTTP {response.status_code}）。")
            data = _completed_response(response, started)
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
            print(f'[astra] Completed in {time.monotonic() - started:.1f}s', flush=True)
            return SimpleNamespace(stop_reason="end_turn", model=data.get("model", model),
                id=data.get("id"), content=[SimpleNamespace(type="text", text=text)],
                usage=SimpleNamespace(input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"]))
        except ContentQualityError:
            raise
        except requests.RequestException as exc:
            # Preserve only the exception type, never URLs/headers or raw errors.
            raise ContentQualityError(f"Astra確認の通信に失敗しました（{type(exc).__name__}）。未確認の本文は完了にしません。") from None
        except (ValueError, KeyError, TypeError):
            raise ContentQualityError("Astra確認の応答形式が不正です。") from None
        finally:
            if response is not None:
                response.close()
