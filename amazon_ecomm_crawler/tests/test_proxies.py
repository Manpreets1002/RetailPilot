import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import http_client
import proxies
from config import settings

LIST = "1.1.1.1:6014:user:secret1\n2.2.2.2:6641:user:secret2, http://u:p@3.3.3.3:80"
PAGE = '<span id="glow-ingress-line2">Mumbai 400001</span>'


def test_parse_both_formats_and_labels_hide_passwords():
    urls = proxies.parse_list(LIST)
    assert urls == ["http://user:secret1@1.1.1.1:6014", "http://user:secret2@2.2.2.2:6641", "http://u:p@3.3.3.3:80"]
    assert [proxies.label(u) for u in urls] == ["1.1.1.1:6014", "2.2.2.2:6641", "3.3.3.3:80"]
    assert proxies.label(None) == "direct"
    assert proxies.playwright_proxy(urls[0]) == {"server": "http://1.1.1.1:6014", "username": "user", "password": "secret1"}


def test_routes_by_mode():
    s = settings.model_copy(update={"crawl_proxies": LIST})
    fallback = proxies.routes(s.model_copy(update={"crawl_proxy_mode": "fallback"}), "mumbai")
    assert fallback[0] is None and sorted(fallback[1:]) == sorted(proxies.parse_list(LIST))
    always = proxies.routes(s.model_copy(update={"crawl_proxy_mode": "always"}), "mumbai")
    assert None not in always and len(always) == 3
    assert proxies.routes(s.model_copy(update={"crawl_proxy_mode": "off"}), "mumbai") == [None]


def test_empty_list_uses_builtin_groups_in_order():
    s = settings.model_copy(update={"crawl_proxies": "", "crawl_proxy_mode": "always"})
    got = proxies.routes(s, "mumbai")
    groups = [[proxies.parse_proxy(e) for e in g] for g in proxies.BUILTIN]
    assert len(got) == sum(map(len, groups))
    start = 0
    for g in groups:  # Webshare first, then Decodo, then proxifly
        assert sorted(got[start:start + len(g)]) == sorted(g)
        start += len(g)


def test_only_https_proxies_skip_the_proxy_certificate_check():
    assert proxies.verify_proxy_tls(None) and proxies.verify_proxy_tls("http://u:p@1.1.1.1:80")
    assert not proxies.verify_proxy_tls("https://1.1.1.1:443")
    assert proxies.playwright_proxy(proxies.parse_proxy("h:1:user:a+b"))["password"] == "a+b"


def test_routes_spread_workers_over_proxies():
    many = ",".join(f"10.0.0.{i}:80:u:p" for i in range(10))
    s = settings.model_copy(update={"crawl_proxies": many, "crawl_proxy_mode": "always"})
    firsts = {proxies.routes(s, f"city-{i}")[0] for i in range(10)}
    assert len(firsts) > 3  # different cities/workers start on different IPs


def test_blocked_direct_route_switches_to_a_proxy(monkeypatch):
    """Direct gets a firewall 503; the fetcher moves to a proxy and gets the page."""
    s = settings.model_copy(update={"crawl_proxies": "9.9.9.9:80:u:p", "crawl_proxy_mode": "fallback",
                                    "max_retries": 3})

    class FakeSession:
        def __init__(self, proxy): self.proxy, self.headers = proxy, {}
        def get(self, url, **kw):
            if self.proxy is None:
                return SimpleNamespace(status_code=503, text="blocked")
            return SimpleNamespace(status_code=200, text=PAGE)

    monkeypatch.setattr(http_client.Fetcher, "_new_session", lambda self: setattr(self, "session", FakeSession(self.proxy)))
    monkeypatch.setattr(http_client.Fetcher, "_wait", lambda self: None)
    monkeypatch.setattr(http_client.Fetcher, "_reset_session", lambda self, prefer_browser=False: None)
    monkeypatch.setattr(http_client.time, "sleep", lambda s: None)
    f = http_client.Fetcher(s, city={"slug": "mumbai", "name": "Mumbai", "pincode": "400001"})
    f.location = {"pincode": "400001"}
    f.robots.parse(["User-agent: *", "Allow: /"])
    assert f.proxy is None
    assert f.get("/dp/B000TEST") == PAGE
    assert proxies.label(f.proxy) == "9.9.9.9:80" and f.stats["proxy_switches"] == 1 and f.stats["blocked"] == 1
