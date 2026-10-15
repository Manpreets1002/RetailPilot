"""Crawler settings, read from environment variables (or a .env file).

The list of cities to crawl lives in cities.json (path overridable with CITIES_FILE).
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

CITIES_FILE = Path(os.getenv("CITIES_FILE", Path(__file__).with_name("cities.json")))


def load_cities(path: Path = CITIES_FILE) -> list[dict]:
    """Return the cities array from the config file: [{slug, name, state, pincode}, ...]."""
    cities = json.loads(Path(path).read_text())["cities"]
    for c in cities:
        missing = {"slug", "name", "pincode"} - c.keys()
        if missing:
            raise ValueError(f"City entry {c} in {path} is missing {sorted(missing)}")
        c["pincode"] = str(c["pincode"])
    return cities


def select_cities(wanted: list[str] | None, cities: list[dict]) -> list[dict]:
    """Pick cities by slug, name or pincode (case-insensitive). None/empty or 'all' means every city."""
    if not wanted or [w.lower() for w in wanted] == ["all"]:
        return cities
    picked = []
    for w in wanted:
        key = w.strip().lower()
        match = next(
            (c for c in cities if key in (c["slug"].lower(), c["name"].lower(), c["pincode"])), None
        )
        if match is None:
            known = ", ".join(c["slug"] for c in cities)
            raise SystemExit(f"Unknown city '{w}'. Known cities: {known}")
        if match not in picked:
            picked.append(match)
    return picked


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    base_url: str = os.getenv("AMAZON_BASE_URL", "https://www.amazon.in")
    # Search index and root browse node for Electronics on amazon.in.
    search_index: str = os.getenv("AMAZON_SEARCH_INDEX", "electronics")
    root_node: str = os.getenv("AMAZON_ROOT_NODE", "976419031")

    mongo_uri: str = os.getenv("MONGO_URI", "mongodb://localhost:27018")
    mongo_db: str = os.getenv("MONGO_DB", "amazon_ecomm")

    # Politeness: random delay between requests, in seconds.
    min_delay: float = _float("CRAWL_MIN_DELAY", 2.0)
    max_delay: float = _float("CRAWL_MAX_DELAY", 5.0)
    max_retries: int = _int("CRAWL_MAX_RETRIES", 3)
    timeout: int = _int("CRAWL_TIMEOUT", 30)
    impersonate: str = os.getenv("CRAWL_IMPERSONATE", "chrome")

    # Limits that keep a POC run small. 0 means no limit.
    max_pages_per_category: int = _int("CRAWL_MAX_PAGES", 2)
    max_products_per_category: int = _int("CRAWL_MAX_PRODUCTS", 20)
    max_reviews_per_product: int = _int("CRAWL_MAX_REVIEWS", 10)

    accept_language: str = "en-IN,en;q=0.9"
    extra_headers: dict = field(default_factory=dict)


settings = Settings()
