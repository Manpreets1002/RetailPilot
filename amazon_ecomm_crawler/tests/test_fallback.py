import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import browser_session
import http_client
from config import settings

MUMBAI = {"slug": "mumbai", "name": "Mumbai", "pincode": "400001"}


class FakeSession:
    """Stands in for curl_cffi: answers every GET with a page delivering to `location`."""

    def __init__(self, location="Mumbai 400001"):
        self.headers, self.cookies = {}, SimpleNamespace(set=lambda *a, **k: None)
        self.location = location

    def get(self, url, **kw):
        blocked = url.endswith("robots.txt") or self.location is None  # None = firewall page, no header
        body = "" if blocked else f'<span id="glow-ingress-line2">{self.location}</span>'
        return SimpleNamespace(text=body, status_code=200)


@pytest.fixture
def fetcher(monkeypatch):
    monkeypatch.setattr(http_client.Fetcher, "_new_session", lambda self: setattr(self, "session", FakeSession()))
    monkeypatch.setattr(http_client.Fetcher, "_wait", lambda self: None)
    return lambda **overrides: http_client.Fetcher(settings.model_copy(update=overrides), city=MUMBAI)


def _http_location_fails(monkeypatch):
    def fail(self, pincode):
        raise http_client.LocationError("token not found")
    monkeypatch.setattr(http_client.Fetcher, "set_location", fail)


def test_warm_up_falls_back_to_browser_when_http_cannot_set_pincode(fetcher, monkeypatch):
    _http_location_fails(monkeypatch)
    calls = []
    monkeypatch.setattr(browser_session, "bootstrap",
                        lambda s, pin, proxy=None: calls.append(pin) or {"cookies": [], "user_agent": "UA", "location": "Mumbai 400001"})
    f = fetcher(browser_fallback="auto")
    f.warm_up()
    assert calls == ["400001"]
    assert f.location["via"] == "browser" and f.location["pincode"] == "400001"
    assert f.stats["browser_sessions"] == 1


def test_warm_up_without_fallback_raises(fetcher, monkeypatch):
    _http_location_fails(monkeypatch)
    monkeypatch.setattr(browser_session, "bootstrap", lambda *a: pytest.fail("browser must not open"))
    with pytest.raises(http_client.LocationError):
        fetcher(browser_fallback="off").warm_up()


def test_browser_failure_is_reported_and_cooled_down(fetcher, monkeypatch):
    _http_location_fails(monkeypatch)

    def blocked(s, pin, proxy=None):
        raise browser_session.BrowserError("captcha")
    monkeypatch.setattr(browser_session, "bootstrap", blocked)
    f = fetcher(browser_fallback="auto")
    with pytest.raises(http_client.LocationError, match="captcha"):
        f.warm_up()
    with pytest.raises(http_client.LocationError, match="recently"):
        f.browser_session()


def test_browser_cookies_must_carry_location_to_http(fetcher, monkeypatch):
    _http_location_fails(monkeypatch)
    monkeypatch.setattr(browser_session, "bootstrap", lambda s, pin, proxy=None: {"cookies": [], "user_agent": "UA", "location": "Mumbai 400001"})
    f = fetcher(browser_fallback="auto")
    monkeypatch.setattr(http_client.Fetcher, "_new_session",
                        lambda self: setattr(self, "session", FakeSession("Delhi 110001")))
    with pytest.raises(http_client.LocationError, match="did not carry"):
        f.warm_up()


def test_http_still_blocked_after_browser_switches_to_browser_mode(fetcher, monkeypatch):
    """From cloud IPs the firewall blocks HTTP even with browser cookies; pages then go through the browser."""
    _http_location_fails(monkeypatch)
    monkeypatch.setattr(browser_session, "bootstrap", lambda s, pin, proxy=None: {"cookies": [], "user_agent": "UA", "location": "x"})
    page_html = '<span id="glow-ingress-line2">Mumbai 400001</span><div id="product">ok</div>'

    class FakeBrowser:
        opened, fetched = [], []
        def _page(self, pin, proxy=None): self.opened.append(pin)
        def fetch(self, url, pin, proxy=None): self.fetched.append(url); return 200, page_html
        def reset(self, pin, proxy=None): pass
        def fetch_text(self, url, pin, proxy=None): return "User-agent: *\nDisallow: /gp/cart"  # robots.txt via the browser

    fake = FakeBrowser()
    monkeypatch.setattr(browser_session.BrowserFetcher, "shared", classmethod(lambda cls, s: fake))
    f = fetcher(browser_fallback="auto")
    monkeypatch.setattr(http_client.Fetcher, "_new_session",
                        lambda self: setattr(self, "session", FakeSession(location=None)))
    f.warm_up()
    assert f.location["via"] == "browser-mode" and fake.opened == ["400001"]
    assert f.get("/dp/B000TEST") == page_html
    assert fake.fetched[-1].endswith("/dp/B000TEST") and f.stats["browser_pages"] == 1
    assert f.get("/gp/cart") is None  # robots.txt loaded through the browser is still respected
