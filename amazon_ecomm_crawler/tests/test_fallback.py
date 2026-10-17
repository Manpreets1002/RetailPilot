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
        body = "" if url.endswith("robots.txt") else f'<span id="glow-ingress-line2">{self.location}</span>'
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
                        lambda s, pin: calls.append(pin) or {"cookies": [], "location": "Mumbai 400001"})
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

    def blocked(s, pin):
        raise browser_session.BrowserError("captcha")
    monkeypatch.setattr(browser_session, "bootstrap", blocked)
    f = fetcher(browser_fallback="auto")
    with pytest.raises(http_client.LocationError, match="captcha"):
        f.warm_up()
    with pytest.raises(http_client.LocationError, match="recently"):
        f.browser_session()


def test_browser_cookies_must_carry_location_to_http(fetcher, monkeypatch):
    _http_location_fails(monkeypatch)
    monkeypatch.setattr(browser_session, "bootstrap", lambda s, pin: {"cookies": [], "location": "Mumbai 400001"})
    f = fetcher(browser_fallback="auto")
    monkeypatch.setattr(http_client.Fetcher, "_new_session",
                        lambda self: setattr(self, "session", FakeSession("Delhi 110001")))
    with pytest.raises(http_client.LocationError, match="did not carry"):
        f.warm_up()
