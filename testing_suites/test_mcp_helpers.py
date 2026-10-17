"""Unit tests for the MCP server's helper functions. No database needed."""

from datetime import UTC, datetime, timedelta

import pytest

import support  # noqa: F401
import mcp_server as ms
from retailpilot.config.config import settings
from sample_data import ago, utc_now


def test_observed_at_uses_the_latest_timestamp():
    early, late = datetime(2026, 10, 1), datetime(2026, 10, 2)
    assert ms._observed_at({"listing_scraped_at": early, "detail_scraped_at": late}) == late
    assert ms._observed_at({}) is None
    assert ms._observed_at(None) is None


def test_offer_summary_reports_when_the_offer_was_observed_and_no_day_flag():
    seen = ago(days=3)
    summary = ms._offer_summary({"price": 100, "listing_scraped_at": seen})
    assert summary["observed_at"] == seen.isoformat()
    assert "observed_today" not in summary


@pytest.mark.parametrize(
    "specs, bullets, expected",
    [
        ({"Battery Life": "Up to 10 hours"}, [], 10.0),
        ({}, ["Lasts 8 hours on battery"], 8.0),
        ({"Battery Life": "Up to 6.5 hr"}, [], 6.5),
        ({"Colour": "Black"}, ["Full HD display"], None),
    ],
)
def test_battery_hours_are_read_from_specs_or_bullets(specs, bullets, expected):
    assert ms._battery_hours({"specifications": specs, "feature_bullets": bullets}) == expected


def test_spec_filters_must_all_match():
    product = {"specifications": {"Colour": "Black", "Weight": "1.8 kg"}}
    assert ms._spec_matches(product, {"colour": "black"}) is True
    assert ms._spec_matches(product, {"colour": "white"}) is False
    assert ms._spec_matches(product, {"colour": "black", "weight": "2 kg"}) is False


def test_searchable_text_covers_title_bullets_and_specs_in_lower_case():
    text = ms._searchable_text({"title": "Acer Laptop", "brand": "Acer", "feature_bullets": ["Long Battery"],
                                "specifications": {"Colour": "Black"}})
    assert "acer laptop" in text and "long battery" in text and "colour black" in text
    assert text == text.lower()


def test_card_has_the_fields_the_customer_ui_needs():
    offer = {"asin": "B0TESTA001", "price": 50000.0, "mrp": 60000.0, "discount_pct": 17,
             "availability": "In stock", "delivery_date": "Tomorrow", "seller": "Acer Store",
             "listing_scraped_at": utc_now()}
    product = {"title": "Acer", "brand": "Acer", "rating": 4.5, "rating_count": 10,
               "feature_bullets": ["a", "b", "c", "d"], "specifications": {"Battery Life": "10 hours"},
               "images": ["http://insecure.example/image.jpg", "https://images.example/product.jpg"]}
    card = ms._card(offer, product)
    assert card["url"] == f"{settings.base_url}/dp/B0TESTA001"
    assert card["price"] == 50000.0
    assert card["battery_hours"] == 10.0
    assert len(card["key_features"]) == 3
    assert card["image_url"] == "https://images.example/product.jpg"
    assert card["observed_at"] is not None


def test_review_view_truncates_long_bodies():
    view = ms._review_view({"asin": "x", "rating": 5, "body": "w" * 1000})
    assert len(view["body"]) == ms.SNIPPET_CHARS * 2


def test_offer_summary_none_passes_through():
    assert ms._offer_summary(None) is None


def test_margin_at_matches_the_definition():
    m = ms._margin_at(price=1180.0, landed=500.0, gst_pct=18.0, fees_pct=0.0)
    assert m["net_revenue"] == 1000.0
    assert m["contribution"] == 500.0
    assert m["margin_pct"] == 50.0
    assert m["breakeven_price"] == 590.0


def test_marketplace_fees_reduce_contribution():
    m = ms._margin_at(price=1180.0, landed=500.0, gst_pct=18.0, fees_pct=10.0)
    assert m["contribution"] == 400.0


def test_subcategory_skips_the_electronics_root():
    assert ms._subcategory({"category_names": ["Electronics", "Laptops"]}) == "Laptops"
    assert ms._subcategory({"category_names": ["Electronics"]}) == "Other"
    assert ms._subcategory({}) == "Other"
