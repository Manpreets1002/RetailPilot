"""Proxy routes for the crawler.

CRAWL_PROXIES lists proxies, separated by commas, spaces or new lines, in either form:
  host:port:user:password        (Webshare's "username" list format)
  http://user:password@host:port

When CRAWL_PROXIES is empty, the built-in lists below are used, tried in this order:
Webshare, then Decodo, then proxifly.

CRAWL_PROXY_MODE picks how they are used:
  fallback (default)  connect directly first; switch to the next proxy only when blocked.
                      Saves proxy bandwidth (a product page is ~350 KB, a search page ~190 KB).
  always              only ever go through proxies.
  off                 ignore CRAWL_PROXIES.

Each Fetcher (one per city per worker) gets its own order, starting at a different
proxy, so workers spread over the IPs. On a block it moves to the next route; the
fallback browser always uses the same route as the HTTP client, because Amazon ties
its firewall token to the IP as well as the browser.
"""

import os
import re
import zlib
from urllib.parse import quote, unquote, urlsplit

from config import Settings

# Built-in proxies (all reached Amazon search pages when checked on 2026-10-18).
WEBSHARE = [
    "45.38.107.97:6014:hdxlfdyi:momkwkme9bjv",
    "38.154.185.97:6370:hdxlfdyi:momkwkme9bjv",
    "84.247.60.125:6095:hdxlfdyi:momkwkme9bjv",
    "191.96.254.138:6185:hdxlfdyi:momkwkme9bjv",
    "198.46.161.42:5092:hdxlfdyi:momkwkme9bjv",
    "64.137.10.153:5803:hdxlfdyi:momkwkme9bjv",
    "104.164.49.38:7693:hdxlfdyi:momkwkme9bjv",
]
DECODO = [
    "http://spvzl2jpng:d2SS8aj+faqh37QlSc@dc.decodo.com:10001",
    *(f"dc.decodo.com:{port}:spidd09s4x:cj+Y2pl4g12UYxUjne" for port in range(10001, 10011)),
]
# Free proxies from api.proxifly.dev: TLS to the proxy itself, with self-signed certificates
# (see verify_proxy_tls). Free proxies stop working within hours or days; refresh this list.
PROXIFLY = [
    "https://134.1.7.9:443",
    "https://65.0.68.234:443",
    "https://176.94.74.21:443",
    "https://210.48.154.99:443",
    "https://46.29.140.237:443",
    "https://148.153.56.51:443",
    "https://18.143.116.224:443",
    "https://195.231.110.164:443",
    "https://213.109.163.119:443",
    "https://200.133.218.122:443",
]
BUILTIN = [WEBSHARE, DECODO, PROXIFLY]


def parse_proxy(entry: str) -> str:
    """One entry -> proxy URL."""
    entry = entry.strip()
    if "://" in entry:
        return entry
    parts = entry.split(":")
    if len(parts) == 4:
        host, port, user, password = parts
        return f"http://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}"
    if len(parts) == 2:
        return f"http://{entry}"
    raise ValueError(f"Can't read proxy entry '{parts[0]}…'; use host:port:user:password or a URL")


def parse_list(text: str) -> list[str]:
    return [parse_proxy(e) for e in re.split(r"[\s,]+", text or "") if e.strip()]


def label(proxy: str | None) -> str:
    """Safe name for logs: host:port, never the password."""
    if proxy is None:
        return "direct"
    u = urlsplit(proxy)
    return f"{u.hostname}:{u.port}"


def verify_proxy_tls(proxy: str | None) -> bool:
    """False for https:// proxies: their certificates are self-signed, so the TLS hop to the proxy
    isn't checked. The tunnelled connection to Amazon is still verified end to end."""
    return proxy is None or urlsplit(proxy).scheme != "https"


def playwright_proxy(proxy: str | None) -> dict | None:
    """Proxy settings in the form Playwright's new_context(proxy=...) takes."""
    if proxy is None:
        return None
    u = urlsplit(proxy)
    out = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
    if u.username:
        out["username"] = unquote(u.username)
        out["password"] = unquote(u.password or "")
    return out


def routes(settings: Settings, key: str) -> list[str | None]:
    """Routes for one Fetcher, in the order to try them. None means a direct connection.

    CRAWL_PROXIES, if set, is one group; otherwise the built-in groups, in order. Each group
    is rotated to start at a different proxy per Fetcher."""
    mode = settings.crawl_proxy_mode
    groups = [parse_list(settings.crawl_proxies)] if settings.crawl_proxies.strip() else [
        [parse_proxy(e) for e in group] for group in BUILTIN]
    groups = [g for g in groups if g]
    if mode == "off" or not groups:
        return [None]
    ordered = []
    for group in groups:
        start = zlib.crc32(f"{os.getpid()}:{key}".encode()) % len(group)
        ordered += group[start:] + group[:start]
    return ordered if mode == "always" else [None, *ordered]
