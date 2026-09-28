"""Child browser process. All page HTTP traffic goes through public-only fetching."""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import re
import sys
import time

from .browser_fetch import check_reference_url
from .public_fetch import get_public_page

BLOCKED_TITLE = r'just a moment|access denied|attention required|captcha|sign in|log in|ログイン|アクセスが拒否'
EXTRACT = """() => {
  const root = document.querySelector('main') || document.querySelector('article') || document.body;
  if (!root) return {title: document.title, text: '', headings: []};
  return {title: document.title, text: root.innerText,
    headings: [...root.querySelectorAll('h2,h3')].filter(e => e.getClientRects().length)
      .map(e => ({level: e.tagName.toLowerCase(), text: e.innerText.trim().slice(0, 300)})).filter(e => e.text)};
}"""


async def render(payload):
    from playwright.async_api import async_playwright
    async with async_playwright() as engine:
        browser = await engine.chromium.launch(headless=True, args=[
            '--disable-dev-shm-usage', '--disable-background-networking',
            '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
            '--disable-features=WebRtcHideLocalIpsWithMdns',
        ])
        try:
            context = await browser.new_context(service_workers='block', accept_downloads=False, locale='ja-JP')
            # Prevent socket traffic and popups, and never import login state.
            await context.route_web_socket('**/*', lambda socket: socket.close())
            await context.add_init_script("delete window.RTCPeerConnection; delete window.webkitRTCPeerConnection; delete window.WebTransport;")
            page = await context.new_page()
            page.on('dialog', lambda dialog: dialog.dismiss())
            if payload.get('probe'):
                await context.route('**/*', lambda route: route.abort())
                await page.set_content('<main id="check"></main><script>document.getElementById("check").textContent="render-ok"</script>')
                return {'status': 'success' if await page.locator('main').inner_text() == 'render-ok' else 'failed'}
            blocked = payload.get('blocked') or []
            started = time.monotonic()
            requests_used, bytes_used = 0, 0
            network_limit = asyncio.Semaphore(5)
            ua = await page.evaluate('navigator.userAgent')

            async def route_request(route):
                nonlocal requests_used, bytes_used
                request = route.request
                requests_used += 1
                if (request.method not in ('GET', 'HEAD') or request.resource_type in ('image', 'media', 'font')
                        or requests_used > 70 or bytes_used > 12_000_000 or time.monotonic() - started > 25):
                    await route.abort()
                    return
                try:
                    if request.is_navigation_request() and request.frame != page.main_frame:
                        await route.abort()
                        return
                    async with network_limit:
                        response = await asyncio.to_thread(get_public_page, request.url,
                            headers={'User-Agent': ua, 'Cache-Control': 'no-cache, no-store', 'Pragma': 'no-cache'},
                            timeout=5, max_bytes=3_000_000,
                            destination_check=lambda url: check_reference_url(url, blocked))
                    bytes_used += len(response.content)
                    if bytes_used > 12_000_000:
                        await route.abort()
                    elif response.url != request.url:
                        # Preserve the final document URL/base for relative JS and API requests.
                        await route.fulfill(status=302, headers={'Location': response.url})
                    else:
                        headers = {k: v for k, v in response.headers.items() if k.lower() in (
                            'content-type', 'access-control-allow-origin', 'content-security-policy', 'x-content-type-options')}
                        await route.fulfill(status=response.status_code, headers=headers, body=response.content)
                except Exception:
                    await route.abort()

            await context.route('**/*', route_request)
            try:
                await page.goto(payload['url'], wait_until='domcontentloaded', timeout=25000)
                await page.wait_for_function("() => (document.querySelector('main') || document.querySelector('article') || document.body)?.innerText.length >= 100", timeout=8000)
                # Allow hydration and asynchronous content to settle, with a bounded wait.
                last = ''
                for _ in range(6):
                    data = await page.evaluate(EXTRACT)
                    if data['text'] == last:
                        break
                    last = data['text']
                    await page.wait_for_timeout(700)
                data = await page.evaluate(EXTRACT)
                check_reference_url(page.url, blocked)
                text = re.sub(r'\s+', ' ', data['text']).strip()
                if re.search(BLOCKED_TITLE, data['title'], re.I) or len(text) < 100:
                    return {'status': 'failed', 'reason_code': 'browser_unverified', 'reason': 'ブラウザで再取得しても本文を確認できませんでした'}
                return {'status': 'success', 'fetch_method': 'browser', 'final_url': page.url,
                        'title': data['title'], 'text': text[:24000], 'truncated': len(text) > 24000,
                        'headings': data['headings'][:150], 'word_count': len(re.sub(r'\s+', '', text)),
                        'fetched_at': datetime.now(timezone.utc).isoformat(), 'sha256': hashlib.sha256(text.encode()).hexdigest()}
            except Exception:
                return {'status': 'failed', 'reason_code': 'browser_unverified', 'reason': 'ブラウザで再取得しても本文を確認できませんでした（アクセス制限や通信状況など）'}
        finally:
            await browser.close()


if __name__ == '__main__':
    try:
        result = asyncio.run(render(json.load(sys.stdin)))
    except Exception:
        result = {'status': 'failed', 'reason_code': 'browser_unavailable', 'reason': 'ブラウザ取得を開始できませんでした'}
    print(json.dumps(result, ensure_ascii=False))
