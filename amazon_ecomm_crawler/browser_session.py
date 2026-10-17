"""Browser fallback for when plain HTTP gets stuck.

Some sessions never get past Amazon's bot check, or the 'Deliver to' API refuses
to set the pincode (this happens on and off for Mumbai and other cities). In that
case we open a real Chromium with Playwright and let it do what Amazon asks:

  1. Load the home page and get past the bot check: click "Continue shopping",
     or, in a visible window (BROWSER_HEADLESS=false), wait for a person to solve
     the image captcha.
  2. Set the delivery pincode through the real "Deliver to" popup.
  3. Return the browser's cookies, which the curl_cffi session then reuses, so
     scraping carries on over fast HTTP with the location already set.
"""

import contextlib
import fcntl
import logging
import re
import tempfile
import time
from pathlib import Path

from config import Settings

log = logging.getLogger(__name__)

BOT_CHECK_MARKERS = (
    "/errors/validateCaptcha",
    "Enter the characters you see below",
    "api-services-support@amazon.com",
)


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
        page.goto(s.base_url + "/s?k=electronics", wait_until="domcontentloaded", timeout=timeout_ms)
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
            f = open(lock_dir / f"slot-{i}.lock", "w")  # noqa: SIM115 - held open for the lock's lifetime
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                f.close()
                continue
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
                f.close()
            return
        if not waited:
            log.info("Browser: %d browsers already running, waiting for a free slot", limit)
            waited = True
        time.sleep(2)


def bootstrap(s: Settings, pincode: str | None) -> dict:
    """Open a browser, pass the bot check, set the pincode and return {cookies, location}."""
    try:
        import playwright.sync_api  # noqa: F401 - fail fast before waiting for a slot
    except ImportError as exc:
        raise BrowserError("playwright is not installed (pip install playwright && playwright install chromium)") from exc
    with _browser_slot(s.browser_max_concurrent):
        return _bootstrap(s, pincode)


def _bootstrap(s: Settings, pincode: str | None) -> dict:
    from playwright.sync_api import sync_playwright

    log.info("Browser: opening %s Chromium%s", "headless" if s.browser_headless else "visible",
             f" for pincode {pincode}" if pincode else "")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=s.browser_headless,
            executable_path=s.browser_executable or None,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            # Headless Chromium says "HeadlessChrome" in its user agent; drop that.
            ua = browser.new_page().evaluate("navigator.userAgent").replace("HeadlessChrome", "Chrome")
            ctx = browser.new_context(user_agent=ua, locale="en-IN", viewport={"width": 1366, "height": 900},
                                      extra_http_headers={"Accept-Language": s.accept_language})
            page = ctx.new_page()
            page.goto(s.base_url + "/", wait_until="domcontentloaded", timeout=s.browser_timeout * 1000)
            _pass_bot_check(page, s)
            location = _set_pincode(page, pincode, s) if pincode else _location_text(page)
            cookies = ctx.cookies()
        except BrowserError:
            raise
        except Exception as exc:
            raise BrowserError(f"Browser session failed: {exc}") from exc
        finally:
            browser.close()

    log.info("Browser: got %d cookies, location '%s'", len(cookies), location)
    return {"cookies": cookies, "location": location}
