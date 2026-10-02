"""Isolated, bounded browser fallback, for high-accuracy jobs and research of dynamic public pages."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
from urllib.parse import urlsplit

from .public_fetch import require_public_destination

_BROWSER_SLOT = threading.BoundedSemaphore(1)


def check_reference_url(url, blocked=()):
    require_public_destination(url)
    target = urlsplit(url)
    for item in blocked:
        other = urlsplit(item)
        path = other.path.rstrip('/')
        if target.hostname == other.hostname and (target.path.rstrip('/') == path or target.path.startswith(path + '/')):
            raise ValueError('参照禁止URLです')


def _worker(payload, timeout=40):
    # Browser child does not receive database/provider keys or the user's profile.
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'TMPDIR', 'PLAYWRIGHT_BROWSERS_PATH', 'SYSTEMROOT') if key in os.environ}
    env['PYTHONUNBUFFERED'] = '1'
    with _BROWSER_SLOT:
        process = subprocess.Popen([sys.executable, '-m', 'pipeline.browser_render_worker'],
            cwd=Path(__file__).resolve().parent.parent, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            stdout, _ = process.communicate(json.dumps(payload), timeout=timeout)
            if process.returncode != 0:
                return {'status': 'failed', 'reason_code': 'browser_unavailable', 'reason': 'ブラウザでの再取得を完了できませんでした'}
            return json.loads(stdout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            return {'status': 'failed', 'reason_code': 'browser_timeout', 'reason': 'ブラウザでの再取得が時間内に完了しませんでした'}
        except json.JSONDecodeError:
            return {'status': 'failed', 'reason_code': 'browser_unavailable', 'reason': 'ブラウザの取得結果を確認できませんでした'}


def fetch_rendered_page(url, blocked=()):
    try:
        check_reference_url(url, blocked)
    except Exception:
        return {'status': 'failed', 'reason_code': 'blocked_destination', 'reason': '取得を許可していないURLです'}
    return _worker({'url': url, 'blocked': list(blocked)})


def browser_probe():
    return _worker({'probe': True}).get('status') == 'success'
