"""Machine-readable failure categories; never infer JavaScript from any error."""
import requests


def classify_failure(exc):
    if isinstance(exc, requests.Timeout):
        return 'timeout'
    if isinstance(exc, requests.HTTPError):
        status = getattr(exc.response, 'status_code', None)
        if status in (401, 403, 429):
            return 'access_restricted'
        if status in (404, 410):
            return 'not_found'
        return 'http_error'
    return 'connection_failed'


def unverified_message(code, attempted=False):
    if attempted:
        return '99％モードでブラウザ再取得も試しましたが、本文を確認できませんでした。アクセス制限や通信状況などの可能性があります。'
    reason = {
        'dynamic_content': 'JavaScriptによる表示の可能性があり、本文は未確認です。',
        'access_restricted': 'アクセス制限のため、本文は未確認です。',
        'not_found': 'ページが見つからず、本文は未確認です。',
        'timeout': '通信が時間内に完了せず、本文は未確認です。',
    }.get(code, '本文を取得できなかったため、未確認です。')
    if code == 'not_found':
        return reason
    return reason + ' 99％モードではブラウザでの再取得を試せます（取得を保証するものではありません）。'
