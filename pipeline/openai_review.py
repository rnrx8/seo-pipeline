"""Responses API adapter for the bounded final audit/repair, never a fallback."""
import json
import os
import time
from types import SimpleNamespace

import requests


def count_review_input(payload):
    """Count text messages/instructions; reserve schema/settings as bytes + slack.

    Only supports this adapter's self-contained text requests. The count API
    generates no answer. No silent dropping of future tools or history inputs.
    """
    from .content_quality import ContentQualityError
    allowed = {'model','instructions','input','max_output_tokens','reasoning','store','stream','text','service_tier'}
    if set(payload)-allowed or any(not isinstance(m.get('content'),str) for m in payload['input']):
        raise ContentQualityError('この入力形式の事前トークン計測には対応していません。')
    body = {k:payload[k] for k in ('model','instructions','input')}
    response = None
    try:
        response = requests.post('https://api.openai.com/v1/responses/input_tokens',
            headers={'Authorization':'Bearer '+os.environ['OPENAI_API_KEY']},
            json=body, timeout=(15,60))
        if response.status_code != 200:
            raise ContentQualityError(f'入力トークン数の確認に失敗しました（HTTP {response.status_code}）。生成は送信していません。')
        data=response.json();count=data.get('input_tokens')
        if data.get('object')!='response.input_tokens' or type(count) is not int or count<=0:
            raise ContentQualityError('入力トークン数の応答が不正です。')
        extra={k:v for k,v in payload.items() if k not in body}
        return count+len(json.dumps(extra,ensure_ascii=False).encode())+4096
    except (requests.RequestException, ValueError):
        raise ContentQualityError('入力トークン数を確認できませんでした。生成は送信していません。') from None
    finally:
        if response is not None:response.close()


def _completed_response(response, started):
    """Consume SSE privately; partial JSON is never accepted or published."""
    from .content_quality import ContentQualityError
    event_lines, received = [], 0
    for line in response.iter_lines():
        if time.monotonic() - started > 1200:
            raise ContentQualityError('品質確認の処理時間上限を超えました。')
        if isinstance(line, bytes):
            line = line.decode('utf-8')
        received += len(line)
        if received > 8_000_000:
            raise ContentQualityError('品質確認の応答サイズ上限を超えました。')
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
                details = (event.get('response') or {}).get('incomplete_details') or {}
                reason = {'max_output_tokens':'出力上限', 'content_filter':'内容フィルター'}.get(details.get('reason'))
                error = (event.get('response') or {}).get('error') or event.get('error') or event
                code = error.get('code') if isinstance(error, dict) else None
                kind = error.get('type') if isinstance(error, dict) else None
                if code == 'credit_balance_exhausted':
                    raise ContentQualityError('OpenAI APIの利用残高がありません。残高追加後に確認工程から再開してください。')
                if code == 'insufficient_quota' or kind == 'insufficient_quota':
                    raise ContentQualityError('OpenAI APIの利用枠が不足しています。課金残高・利用上限を確認してください。')
                safe_codes = {'server_error', 'rate_limit_exceeded', 'insufficient_quota', 'invalid_api_key',
                              'context_length_exceeded', 'invalid_prompt', 'invalid_request_error',
                              'model_not_found', 'invalid_json_schema', 'unsupported_parameter'}
                if reason is None: reason = code if code in safe_codes else '原因未特定'
                raise ContentQualityError(f'品質確認の応答が未完了です（{event["type"]}: {reason}）。')
    raise ContentQualityError('品質確認の通信が完了通知前に終了しました。')


def create_review_response(*, model, max_tokens, system, messages, output_config):
    # Import lazily: content_quality also uses the shared AI helper.
    from .content_quality import ContentQualityError
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise ContentQualityError("品質確認に必要なOPENAI_API_KEYが未設定です。")
    tiered = model in ("gpt-6-luna", "gpt-6.1-sol")
    schema = output_config["format"]["schema"]
    payload = {"model": model, "instructions": system, "input": messages,
               "max_output_tokens": max_tokens if tiered else max(16000, max_tokens),
               "reasoning": {"effort": ("low" if model=="gpt-6-luna" else "medium") if tiered else "high"}, "store": False, "stream": True,
               "text": {"format": {"type": "json_schema", "name": "article_quality",
                                    "strict": True, "schema": schema}}}
    if tiered: payload["service_tier"] = "default"
    for attempt in range(1 if tiered else 3):
        response = None
        reservation = None
        accounted = False
        started = time.monotonic()
        try:
            if tiered:
                from .quality_budget import reserve, settle
                reservation = reserve(payload, input_counter=count_review_input)
            print('[review '+model+'] Starting streamed review/repair request', flush=True)
            response = requests.post("https://api.openai.com/v1/responses",
                headers={"Authorization": "Bearer " + key}, json=payload,
                stream=True, timeout=(15, 600))
            if response.status_code == 429:
                try:
                    error = response.json().get("error", {})
                    code = error.get("code")
                    if error.get("type") == "insufficient_quota": code = "insufficient_quota"
                except ValueError:
                    code = None
                if code in ("insufficient_quota", "credit_balance_exhausted"):
                    raise ContentQualityError("OpenAI APIの利用枠が不足しています。")
                if not tiered and attempt < 2:
                    response.close()
                    time.sleep(10 * (attempt + 1))
                    continue
            if response.status_code != 200:
                raise ContentQualityError(f"品質確認に失敗しました（HTTP {response.status_code}）。")
            data = _completed_response(response, started)
            if tiered and data.get("usage"):
                settle(reservation,data["usage"]);accounted=True
            if data.get("status") != "completed":
                raise ContentQualityError("品質確認の応答が未完了です。")
            parts = [part for item in data.get("output", []) if item.get("type") == "message"
                     for part in item.get("content", [])]
            if any(p.get("type") == "refusal" for p in parts):
                raise ContentQualityError("確認モデルが確認を完了できませんでした。")
            text = "\n".join(p["text"] for p in parts if p.get("type") == "output_text")
            if not text.strip():
                raise ContentQualityError("品質確認の本文が空です。")
            usage = data["usage"]
            print(f'[review {model}] Completed in {time.monotonic() - started:.1f}s', flush=True)
            return SimpleNamespace(stop_reason="end_turn", model=data.get("model", model),
                id=data.get("id"), content=[SimpleNamespace(type="text", text=text)],
                usage=SimpleNamespace(input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"]))
        except ContentQualityError:
            raise
        except requests.RequestException as exc:
            # Preserve only the exception type, never URLs/headers or raw errors.
            raise ContentQualityError(f"品質確認の通信に失敗しました（{type(exc).__name__}）。未確認の本文は完了にしません。") from None
        except (ValueError, KeyError, TypeError):
            raise ContentQualityError("品質確認の応答形式が不正です。") from None
        finally:
            if reservation is not None and not accounted:
                settle(reservation)
            if response is not None:
                response.close()
