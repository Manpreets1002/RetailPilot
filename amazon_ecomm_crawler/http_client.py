"""HTTP fetching with a browser TLS fingerprint, polite delays, retries, robots.txt checks
and a per-session delivery location (pincode).

Plain httpx/requests get a 503 from Amazon, so we use curl_cffi, which presents
the same TLS fingerprint as a real Chrome browser.

Amazon decides prices, stock, sellers and delivery dates from the delivery
pincode stored in the session cookies. Each Fetcher is pinned to one city; if the
session has to be reset after a block, the pincode is set again.

When HTTP alone can't get a working session (bot check on every try, or the
'Deliver to' API refuses the pincode), a real browser is opened to do it
(see browser_session.py) and its cookies are loaded into the HTTP session.
"""

import html as html_lib
import logging
import random
import re
import time
import urllib.robotparser
from urllib.parse import urljoin

from curl_cffi import requests

import browser_session
from config import Settings

log = logging.getLogger(__name__)

CAPTCHA_MARKERS = browser_session.BOT_CHECK_MARKERS
# After a failed browser attempt, wait this long before opening another one.
BROWSER_COOLDOWN = 300

GLOW_MODAL_TOKEN_RE = re.compile(r'"ajaxHeaders":\{"anti-csrftoken-a2z":"([^"]+)"\}, "name":"glow-modal"')
ADDRESS_TOKEN_RE = re.compile(r'CSRF_TOKEN\s*:\s*"([^"]+)"')
LOCATION_RE = re.compile(r'id="glow-ingress-line2"[^>]*>\s*([^<]+)')


class LocationError(Exception):
    """Amazon did not accept the requested delivery pincode."""


def page_location(html: str) -> str | None:
    """The 'Deliver to ...' text in the page header, e.g. 'Mumbai 400001'."""
    m = LOCATION_RE.search(html)
    if not m:
        return None
    return re.sub(r"[‌\s]+", " ", html_lib.unescape(m.group(1))).strip() or None


class Fetcher:
    def __init__(self, settings: Settings, city: dict | None = None):
        self.s = settings
        self.city = city
        self.robots = urllib.robotparser.RobotFileParser()
        self._last_request = 0.0
        self.stats = {"requests": 0, "failures": 0, "blocked": 0, "location_resets": 0, "browser_sessions": 0}
        self.location: dict | None = None  # what Amazon confirmed, e.g. {"city": "MUMBAI", ...}
        self._browser_failed_at = float("-inf")
        self._new_session()

    # ---------------------------------------------------------------- session

    def _new_session(self) -> None:
        self.session = requests.Session(impersonate=self.s.impersonate)
        self.session.headers.update({"Accept-Language": self.s.accept_language, **self.s.extra_headers})

    def warm_up(self) -> None:
        """Load robots.txt and the home page so the session gets normal cookies, then set the city.

        Tries plain HTTP twice, then falls back to a browser. Raises LocationError if all fail.
        """
        robots_resp = self.session.get(urljoin(self.s.base_url, "/robots.txt"), timeout=self.s.timeout)
        self.robots.parse(robots_resp.text.splitlines())
        if self.s.browser_fallback == "always":
            self.browser_session()
            return
        error: Exception | None = None
        for attempt in (1, 2):
            try:
                self._prime_cookies()
                if self.city:
                    self.set_location(self.city["pincode"])
                return
            except Exception as exc:  # noqa: BLE001 - LocationError or network errors
                error = exc
                log.warning("HTTP warm-up failed (attempt %d): %s", attempt, exc)
                self._new_session()
        if self.s.browser_fallback == "off":
            raise LocationError(f"Could not set up a session: {error}") from error
        self.browser_session()

    def browser_session(self) -> None:
        """Open a browser to pass the bot check and set the pincode, then reuse its cookies over HTTP."""
        if time.monotonic() - self._browser_failed_at < BROWSER_COOLDOWN:
            raise LocationError("Browser fallback failed recently; not retrying yet")
        self.stats["browser_sessions"] += 1
        pincode = self.city["pincode"] if self.city else None
        try:
            result = browser_session.bootstrap(self.s, pincode)
        except browser_session.BrowserError as exc:
            self._browser_failed_at = time.monotonic()
            self.location = None
            raise LocationError(str(exc)) from exc

        self._new_session()
        for c in result["cookies"]:
            if "amazon." in c["domain"]:
                self.session.cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))
        self._last_request = time.monotonic()
        if self.city:
            # The browser saw the right pincode; check the HTTP session sees it too.
            self._wait()
            html = self.session.get(f"{self.s.base_url}/s?k=electronics", timeout=self.s.timeout).text
            loc = page_location(html)
            if not loc or pincode not in loc:
                self._browser_failed_at = time.monotonic()
                self.location = None
                raise LocationError(f"Browser cookies did not carry the location over (page shows '{loc}')")
            self.location = {"pincode": pincode, "city": loc.replace(pincode, "").strip(), "state": None,
                             "via": "browser"}
            log.info("Delivery location set to %s via browser", loc)

    def _prime_cookies(self) -> None:
        # The home page normally answers 202; we only want its cookies.
        self.session.get(self.s.base_url + "/", timeout=self.s.timeout)
        self._last_request = time.monotonic()

    def set_location(self, pincode: str) -> dict:
        """Set the delivery pincode for this session, the way the 'Deliver to' popup does.

        1. A search page carries a token for opening the location popup.
        2. The popup HTML carries a second token for the address-change call.
        3. POST the pincode; Amazon answers with the resolved city/state.
        """
        base = self.s.base_url
        self._wait()
        page = html_lib.unescape(self.session.get(f"{base}/s?k=electronics", timeout=self.s.timeout).text)
        m = GLOW_MODAL_TOKEN_RE.search(page)
        if not m:
            raise LocationError("Location popup token not found (blocked or page layout changed)")
        ajax = {"X-Requested-With": "XMLHttpRequest"}

        self._wait()
        modal = self.session.get(
            f"{base}/portal-migration/hz/glow/get-rendered-address-selections"
            "?deviceType=desktop&pageType=Search&storeContext=NoStoreName&actionSource=desktop-modal",
            headers={**ajax, "anti-csrftoken-a2z": m.group(1)},
            timeout=self.s.timeout,
        )
        m2 = ADDRESS_TOKEN_RE.search(modal.text)
        if not m2:
            raise LocationError("Address-change token not found in location popup")

        self._wait()
        resp = self.session.post(
            f"{base}/portal-migration/hz/glow/address-change?actionSource=glow",
            json={
                "locationType": "LOCATION_INPUT",
                "zipCode": pincode,
                "deviceType": "web",
                "storeContext": "generic",
                "pageType": "Search",
                "actionSource": "glow",
            },
            headers={**ajax, "anti-csrftoken-a2z": m2.group(1)},
            timeout=self.s.timeout,
        )
        try:
            data = resp.json()
        except ValueError as exc:
            raise LocationError(f"Unexpected address-change response: HTTP {resp.status_code}") from exc
        if not data.get("successful") or not data.get("isAddressUpdated"):
            raise LocationError(f"Amazon rejected pincode {pincode}: {data}")

        addr = data.get("address") or {}
        self.location = {"pincode": addr.get("zipCode"), "city": addr.get("city"), "state": addr.get("state")}
        log.info("Delivery location set to %s, %s %s", self.location["city"], self.location["state"], pincode)
        return self.location

    def _reset_session(self, prefer_browser: bool = False) -> None:
        """Fresh cookies often clear a soft block; re-apply the city afterwards.

        With prefer_browser (repeated blocks) or when HTTP can't set the pincode,
        a browser session is used instead.
        """
        self.stats["location_resets"] += 1
        use_browser = self.s.browser_fallback == "always" or (
            self.s.browser_fallback == "auto" and prefer_browser
            and time.monotonic() - self._browser_failed_at >= BROWSER_COOLDOWN
        )
        if not use_browser:
            self._new_session()
            try:
                self._prime_cookies()
                if self.city:
                    self.set_location(self.city["pincode"])
                return
            except Exception as exc:  # noqa: BLE001 - retried below or on the next attempt
                log.warning("Could not restore location after reset: %s", exc)
                self.location = None
                if self.s.browser_fallback == "off":
                    return
        try:
            self.browser_session()
        except LocationError as exc:
            log.warning("Browser fallback failed: %s", exc)

    # ---------------------------------------------------------------- fetching

    def allowed(self, url: str) -> bool:
        return self.robots.can_fetch("*", url)

    def _wait(self) -> None:
        delay = random.uniform(self.s.min_delay, self.s.max_delay)
        elapsed = time.monotonic() - self._last_request
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request = time.monotonic()

    def _location_ok(self, html: str) -> bool:
        """True if the page is delivered to our pincode (or we don't care about location)."""
        if not self.city:
            return True
        loc = page_location(html)
        # Pages without the header (rare) are accepted rather than refetched forever.
        return loc is None or self.city["pincode"] in loc

    def get(self, path_or_url: str) -> str | None:
        """Return the page HTML, or None if the URL is disallowed or keeps failing."""
        url = urljoin(self.s.base_url, path_or_url)
        if not self.allowed(url):
            log.warning("robots.txt disallows %s, skipping", url)
            return None

        for attempt in range(1, self.s.max_retries + 1):
            if self.city and self.location is None:
                self._reset_session(prefer_browser=attempt >= 2)
            self._wait()
            self.stats["requests"] += 1
            try:
                resp = self.session.get(url, timeout=self.s.timeout)
            except Exception as exc:  # noqa: BLE001 - network errors, timeouts
                log.warning("GET %s failed (attempt %d): %s", url, attempt, exc)
                self._backoff(attempt)
                continue

            if resp.status_code == 404:
                log.info("404 for %s", url)
                return None

            if resp.status_code == 200 and not any(m in resp.text for m in CAPTCHA_MARKERS):
                if self._location_ok(resp.text):
                    return resp.text
                log.warning("Page %s is for '%s', not %s; resetting location",
                            url, page_location(resp.text), self.city["pincode"])
                self._reset_session(prefer_browser=attempt >= 2)
                continue

            self.stats["blocked"] += 1
            log.warning("Blocked or error on %s: HTTP %s (attempt %d)", url, resp.status_code, attempt)
            self._backoff(attempt)
            # A second block in a row means fresh HTTP cookies aren't enough; use the browser.
            self._reset_session(prefer_browser=attempt >= 2)

        self.stats["failures"] += 1
        log.error("Giving up on %s after %d attempts", url, self.s.max_retries)
        return None

    @staticmethod
    def _backoff(attempt: int) -> None:
        time.sleep(min(60, 5 * 2 ** (attempt - 1)) + random.uniform(0, 2))
