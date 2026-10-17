"""Browser fallback for when plain HTTP gets stuck.

Some sessions never get past Amazon's bot check, or the 'Deliver to' API refuses
to set the pincode (this happens on and off for Mumbai and other cities). In that
case we open a real Chromium with Playwright and let it do what Amazon asks:

  1. Load the home page and get past the bot check: click "Continue shopping",
     or, in a visible window (BROWSER_HEADLESS=false), wait for a person to solve
     the image captcha.
  2. Set the delivery pincode through the real "Deliver to" popup.
  3. Return the browser's cookies and user agent, which the curl_cffi session then
     reuses, so scraping carries on over fast HTTP with the location already set.

From some IPs (cloud servers especially) Amazon's firewall keeps blocking plain HTTP
even with the browser's cookies. Then BrowserFetcher keeps one Chromium open per
worker process, with one tab per city, and fetches the pages through it instead.
"""

import contextlib
import logging
import re
import tempfile
import time
from pathlib import Path

from filelock import FileLock, Timeout

import proxies
from config import Settings

log = logging.getLogger(__name__)

BOT_CHECK_MARKERS = (
    "/errors/validateCaptcha",
    "Enter the characters you see below",
    "api-services-support@amazon.com",
)


def chrome_identity(impersonate: str) -> dict:
    """One browser identity shared by curl_cffi and Chromium.

    Amazon's firewall token is earned in the browser and tied to the identity it saw, so
    the HTTP client must present the same one: curl_cffi's Chrome TLS/HTTP2 fingerprint
    (e.g. chrome150) plus the matching User-Agent and client hints. The platform says
    Linux because that is what the browser really runs on, and its JavaScript shows it.
    """
    from curl_cffi.requests.impersonate import DEFAULT_CHROME

    target = DEFAULT_CHROME if impersonate == "chrome" else impersonate
    major = re.search(r"\d+", target)
    version = major.group(0) if major else "150"
    return {
        "target": target,
        "user_agent": f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      f"Chrome/{version}.0.0.0 Safari/537.36",
        "client_hints": {
            "sec-ch-ua": f'"Not;A=Brand";v="8", "Chromium";v="{version}", "Google Chrome";v="{version}"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
        },
    }


def _goto(page, url: str, s: Settings):
    """page.goto that tolerates Amazon reloading the page by itself (after a pincode change or a
    firewall challenge), which aborts our navigation with net::ERR_ABORTED."""
    for attempt in range(1, 4):
        try:
            return page.goto(url, wait_until="domcontentloaded", timeout=s.browser_timeout * 1000)
        except Exception as exc:
            if "ERR_ABORTED" not in str(exc) or attempt == 3:
                raise
            with contextlib.suppress(Exception):
                page.wait_for_load_state("domcontentloaded", timeout=10_000)
            time.sleep(2)
    return None


class BrowserError(Exception):
    """The browser could not get a usable session (Playwright missing, still blocked, pincode refused)."""


def _is_blocked(html: str) -> bool:
    return any(m in html for m in BOT_CHECK_MARKERS)


def _location_text(page) -> str | None:
    el = page.query_selector("#glow-ingress-line2")
    if not el:
        return None
    return re.sub(r"[‌\s]+", " ", el.inner_text()).strip() or None


def _pass_bot_check(page, s: Settings) -> None:
    deadline = time.monotonic() + s.browser_manual_wait
    asked_human = False
    for _ in range(10):
        if not _is_blocked(page.content()):
            return
        # The common soft check is a page with a single "Continue shopping" button.
        button = page.query_selector("form[action*='validateCaptcha'] button, form[action*='validateCaptcha'] input[type=submit]")
        has_image = page.query_selector("form[action*='validateCaptcha'] img") is not None
        if button and not has_image:
            log.info("Browser: clicking through the bot check")
            button.click()
            page.wait_for_load_state("domcontentloaded")
            time.sleep(2)
            continue
        break

    if not _is_blocked(page.content()):
        return
    if s.browser_headless:
        raise BrowserError("Image captcha shown; run with BROWSER_HEADLESS=false to solve it by hand")

    # Visible window: let a person solve the captcha, then carry on by ourselves.
    while time.monotonic() < deadline:
        if not asked_human:
            log.warning("Browser: solve the captcha in the browser window (waiting up to %ds)", s.browser_manual_wait)
            asked_human = True
        time.sleep(2)
        try:
            if not _is_blocked(page.content()):
                log.info("Browser: captcha solved")
                return
        except Exception:  # noqa: BLE001, S112 - page is navigating
            continue
    raise BrowserError("Captcha was not solved in time")


def _open_location_popup(page, s: Settings) -> None:
    """Click 'Deliver to' until the pincode box shows. The first click often lands before the
    page's scripts are ready (right after the AWS WAF challenge) and does nothing."""
    for attempt in range(1, 5):
        try:
            page.wait_for_load_state("load", timeout=s.browser_timeout * 1000)
        except Exception:  # noqa: BLE001, S110 - slow ads/trackers; the header is usually ready anyway
            pass
        try:
            page.click("#nav-global-location-popover-link", timeout=10_000)
            page.wait_for_selector("#GLUXZipUpdateInput", state="visible", timeout=8_000)
            return
        except Exception as exc:  # noqa: BLE001 - retried below
            log.info("Browser: location popup did not open (attempt %d): %s", attempt, str(exc).splitlines()[0])
            page.reload(wait_until="domcontentloaded", timeout=s.browser_timeout * 1000)
            _pass_bot_check(page, s)
    raise BrowserError("The 'Deliver to' popup never opened")


def _set_pincode(page, pincode: str, s: Settings) -> str | None:
    timeout_ms = s.browser_timeout * 1000
    for attempt in range(1, 4):
        _open_location_popup(page, s)
        page.fill("#GLUXZipUpdateInput", pincode)
        # Wait for Amazon's own address-change call instead of guessing with sleeps.
        try:
            with page.expect_response(lambda r: "/glow/address-change" in r.url, timeout=timeout_ms) as info:
                page.click("#GLUXZipUpdate", timeout=timeout_ms)
            data = info.value.json()
        except Exception as exc:  # noqa: BLE001 - no response or not JSON; retried below
            log.warning("Browser: no address-change response (attempt %d): %s", attempt, str(exc).splitlines()[0])
            page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
            continue
        if not data.get("isAddressUpdated"):
            log.warning("Browser: Amazon did not accept pincode %s: %s", pincode, data)
            page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
            continue
        # Let the page finish its follow-up calls that store the new location.
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:  # noqa: BLE001, S110 - ads keep the network busy; the address is already saved
            pass
        _goto(page, s.base_url + "/s?k=electronics", s)
        _pass_bot_check(page, s)
        loc = _location_text(page)
        if loc and pincode in loc:
            return loc
        log.warning("Browser: location shows '%s' after attempt %d, wanted %s", loc, attempt, pincode)
    raise BrowserError(f"Browser could not set pincode {pincode}")


@contextlib.contextmanager
def _browser_slot(limit: int):
    """Hold one of `limit` lock files, so at most `limit` browsers run at once across all
    worker processes on this machine/container. Waits until a slot is free."""
    lock_dir = Path(tempfile.gettempdir()) / "crawler-browser-slots"
    lock_dir.mkdir(exist_ok=True)
    waited = False
    while True:
        for i in range(max(1, limit)):
            lock = FileLock(lock_dir / f"slot-{i}.lock")
            try:
                lock.acquire(timeout=0)
            except Timeout:
                continue
            try:
                yield
            finally:
                lock.release()
            return
        if not waited:
            log.info("Browser: %d browsers already running, waiting for a free slot", limit)
            waited = True
        time.sleep(2)


_playwright = None


def _get_playwright():
    """One Playwright per process: the sync API can't run two at once in the same thread."""
    global _playwright
    if _playwright is None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserError("playwright is not installed (pip install playwright && playwright install chromium)") from exc
        _playwright = sync_playwright().start()
    return _playwright


def _launch(s: Settings):
    """Start Chromium; returns (browser, the shared identity's user agent)."""
    browser = _get_playwright().chromium.launch(
        headless=s.browser_headless,
        executable_path=s.browser_executable or None,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
    )
    return browser, chrome_identity(s.impersonate)["user_agent"]


SKIP_RESOURCES = {"image", "media", "font"}


def _skip_heavy(route) -> None:
    if route.request.resource_type in SKIP_RESOURCES:
        route.abort()
    else:
        route.continue_()


def _open_city_page(browser, ua: str, pincode: str | None, s: Settings, lean: bool = False,
                    proxy: str | None = None):
    """New tab with its own cookies, past the bot check and delivering to `pincode`. Returns (page, location).

    lean=True skips images, video and fonts: pages load much faster and the HTML (with all
    image URLs) is the same. Scripts stay on, since the firewall challenge needs them.
    """
    hints = chrome_identity(s.impersonate)["client_hints"]
    ctx = browser.new_context(user_agent=ua, locale="en-IN", viewport={"width": 1366, "height": 900},
                              extra_http_headers={"Accept-Language": s.accept_language, **hints},
                              proxy=proxies.playwright_proxy(proxy))
    if lean:
        ctx.route("**/*", _skip_heavy)
    page = ctx.new_page()
    _goto(page, s.base_url + "/", s)
    _pass_bot_check(page, s)
    location = _set_pincode(page, pincode, s) if pincode else _location_text(page)
    if location is None:  # no 'Deliver to' header at all: a firewall page, not Amazon
        ctx.close()
        raise BrowserError("Page is blocked (no Amazon header)")
    return page, location


def bootstrap(s: Settings, pincode: str | None, proxy: str | None = None) -> dict:
    """Open a browser, pass the bot check, set the pincode and return {cookies, user_agent, location}.

    `proxy` must be the same route the HTTP client uses, or the cookies won't carry over.
    """
    _get_playwright()  # fail fast if Playwright is missing, before waiting for a slot
    with _browser_slot(s.browser_max_concurrent):
        log.info("Browser: opening %s Chromium%s via %s", "headless" if s.browser_headless else "visible",
                 f" for pincode {pincode}" if pincode else "", proxies.label(proxy))
        browser, ua = _launch(s)
        try:
            page, location = _open_city_page(browser, ua, pincode, s, proxy=proxy)
            cookies = page.context.cookies()
        except BrowserError:
            raise
        except Exception as exc:
            raise BrowserError(f"Browser session failed: {exc}") from exc
        finally:
            browser.close()

    log.info("Browser: got %d cookies, location '%s'", len(cookies), location)
    return {"cookies": cookies, "user_agent": ua, "location": location}


class BrowserFetcher:
    """Fetches pages through a Chromium kept open for the whole worker process.

    Used when plain HTTP stays blocked even with browser cookies. One browser per
    process, one tab (with its own cookies, pincode and proxy) per city and route.
    """

    _instance: "BrowserFetcher | None" = None

    def __init__(self, s: Settings):
        self.s = s
        self.browser = None
        self.ua = ""
        self.pages: dict[tuple, object] = {}

    @classmethod
    def shared(cls, s: Settings) -> "BrowserFetcher":
        if cls._instance is None:
            cls._instance = cls(s)
        return cls._instance

    def _page(self, pincode: str | None, proxy: str | None = None):
        key = (pincode or "-", proxy)
        page = self.pages.get(key)
        if page is not None and not page.is_closed():
            return page
        if self.browser is None or not self.browser.is_connected():
            log.info("Browser: starting a kept-open Chromium for page fetching")
            self.browser, self.ua = _launch(self.s)
            self.pages.clear()
        try:
            page, location = _open_city_page(self.browser, self.ua, pincode, self.s, lean=True, proxy=proxy)
        except BrowserError:
            raise
        except Exception as exc:
            raise BrowserError(f"Browser tab for {key[0]} via {proxies.label(proxy)} failed: {exc}") from exc
        log.info("Browser: tab ready for pincode %s via %s ('%s')", key[0], proxies.label(proxy), location)
        self.pages[key] = page
        return page

    def reset(self, pincode: str | None, proxy: str | None = None) -> None:
        """Drop the city's tab on this route; the next fetch opens a fresh one and sets the pincode again."""
        page = self.pages.pop((pincode or "-", proxy), None)
        if page is not None:
            with contextlib.suppress(Exception):
                page.context.close()

    def fetch_text(self, url: str, pincode: str | None, proxy: str | None = None) -> str:
        """Plain text of a page, e.g. robots.txt (which the browser shows wrapped in HTML)."""
        page = self._page(pincode, proxy)
        try:
            _goto(page, url, self.s)
            return page.inner_text("body")
        except Exception as exc:
            raise BrowserError(f"Browser fetch failed for {url}: {str(exc).splitlines()[0]}") from exc

    def fetch(self, url: str, pincode: str | None, proxy: str | None = None) -> tuple[int, str]:
        """Load `url` in the city's tab for this route; returns (HTTP status, HTML)."""
        page = self._page(pincode, proxy)
        try:
            resp = _goto(page, url, self.s)
            # A firewall challenge (HTTP 202) solves itself and reloads; wait for the real page header.
            with contextlib.suppress(Exception):
                page.wait_for_selector("#glow-ingress-line2", timeout=15_000)
            _pass_bot_check(page, self.s)
            return (resp.status if resp else 0), page.content()
        except BrowserError:
            raise
        except Exception as exc:
            self.reset(pincode, proxy)
            raise BrowserError(f"Browser fetch failed for {url}: {str(exc).splitlines()[0]}") from exc
