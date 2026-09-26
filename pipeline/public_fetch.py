"""Public-page retrieval for user-supplied competitor URLs; redirects stay public."""
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import requests

from .serp_sources import canonical_url, SerpQualityError


def require_public_destination(url: str) -> None:
    canonical_url(url)
    parsed = urlsplit(url)
    if parsed.port not in (None, 80, 443):
        raise SerpQualityError('競合URLは通常のWebページを指定してください。')
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    except OSError:
        raise SerpQualityError('競合URLを確認できませんでした。') from None
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise SerpQualityError('競合URLに公開サイト以外は指定できません。')


def get_public_page(url: str, *, headers: dict, timeout: int):
    with requests.Session() as session:
        session.trust_env = False
        for _ in range(4):
            require_public_destination(url)
            response = session.get(url, headers=headers, timeout=timeout, allow_redirects=False)
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get('Location')
                response.close()
                if not location: raise SerpQualityError('競合URLの転送先を確認できません。')
                url = urljoin(url, location)
                continue
            response.raise_for_status()
            return response
    raise SerpQualityError('競合URLの転送回数が上限を超えました。')
