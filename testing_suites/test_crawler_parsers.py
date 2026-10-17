"""Unit tests for the Amazon crawler's value parsers and city selection. No network access needed."""

import importlib.util

import pytest

import support  # noqa: F401
from parsers import asin_from_url, discount_pct, parse_count, parse_percent, parse_price, parse_rating
from support import CRAWLER


def _load_crawler_config():
    # The crawler's config.py shares its module name with Django's config package, so load it by path.
    spec = importlib.util.spec_from_file_location("crawler_config", CRAWLER / "config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


crawler_config = _load_crawler_config()


@pytest.mark.parametrize("raw, expected", [
    ("₹1,099.00", 1099.0),
    ("₹42,999", 42999.0),
    ("no digits here", None),
    (None, None),
])
def test_parse_price(raw, expected):
    assert parse_price(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("4.1 out of 5 stars", 4.1),
    ("4.5", 4.5),
    (None, None),
])
def test_parse_rating(raw, expected):
    assert parse_rating(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("(27,065)", 27065),
    ("1.2K", 1200),
    ("2.5M", 2500000),
    (None, None),
])
def test_parse_count(raw, expected):
    assert parse_count(raw) == expected


def test_parse_percent_reads_discounts():
    assert parse_percent("-84%") == 84
    assert parse_percent("(79% off)") == 79
    assert parse_percent("none") is None


def test_discount_only_when_mrp_is_above_price():
    assert discount_pct(900, 1000) == 10
    assert discount_pct(1000, 900) is None
    assert discount_pct(None, 1000) is None


def test_asin_is_read_from_a_product_url():
    assert asin_from_url("https://www.amazon.in/Some-Name/dp/B0CCZ26B5V/ref=sr_1") == "B0CCZ26B5V"
    assert asin_from_url("https://www.amazon.in/dp/B0CCZ26B5V") == "B0CCZ26B5V"


def test_asin_is_read_from_a_sponsored_redirect():
    url = "https://www.amazon.in/sspa/click?ie=UTF8&url=%2FName%2Fdp%2FB0ABCDEFGH%2Fref%3Dsspa"
    assert asin_from_url(url) == "B0ABCDEFGH"


def test_asin_is_none_for_other_urls():
    assert asin_from_url(None) is None
    assert asin_from_url("https://www.amazon.in/gp/help") is None


def test_select_cities_by_slug_name_or_pincode():
    cities = crawler_config.load_cities()
    assert [c["slug"] for c in crawler_config.select_cities(["delhi"], cities)] == ["delhi"]
    assert [c["slug"] for c in crawler_config.select_cities(["Mumbai"], cities)] == ["mumbai"]
    assert [c["slug"] for c in crawler_config.select_cities(["600001"], cities)] == ["chennai"]


def test_select_cities_defaults_to_all_and_rejects_unknown_names():
    cities = crawler_config.load_cities()
    assert len(crawler_config.select_cities(None, cities)) == 10
    assert len(crawler_config.select_cities(["all"], cities)) == 10
    with pytest.raises(SystemExit, match="Unknown city"):
        crawler_config.select_cities(["atlantis"], cities)


def test_load_cities_rejects_entries_missing_fields(tmp_path):
    bad = tmp_path / "cities.json"
    bad.write_text('{"cities": [{"slug": "x", "name": "X"}]}', encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        crawler_config.load_cities(bad)
