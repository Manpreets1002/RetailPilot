"""Dashboard calculations in the frontend's analytics module: margins, peers, history, sentiment, basket, health.

The pure-calculation tests use an in-memory dataset. The load test reads the seeded test database.
"""

from datetime import timedelta

import pytest
from django.conf import settings as django_settings

import support  # noqa: F401
from support import requires_mongo
import sample_data as sd
from web import analytics


@pytest.fixture(autouse=True)
def fresh_cache():
    analytics._cache.clear()
    yield
    analytics._cache.clear()


@pytest.fixture
def data():
    return sd.dashboard_dataset()


def _row(data, asin, city):
    return next(o for o in data["offers"] if o["asin"] == asin and o["city"] == city)


# ------------------------------------------------------------------ small helpers

def test_subcategory_skips_the_electronics_root():
    assert analytics.subcategory(["Electronics", "Laptops"]) == "Laptops"
    assert analytics.subcategory(["Electronics"]) == "Other"
    assert analytics.subcategory(None) == "Other"


def test_is_stale_uses_the_configured_window():
    assert analytics.is_stale(None) is False
    assert analytics.is_stale(sd.ago(days=3)) is True
    assert analytics.is_stale(sd.utc_now()) is False


def test_observed_at_is_the_later_of_listing_and_detail():
    early, late = sd.ago(days=2), sd.ago(days=1)
    assert analytics.observed_at({"listing_scraped_at": early, "detail_scraped_at": late}) == late
    assert analytics.observed_at({}) is None


# ------------------------------------------------------------------ margins

def test_margin_scenarios_below_breakeven_are_flagged():
    rows = analytics.margin_scenarios(1000.0, 900.0, 950.0, 18.0, 0.0, [900.0])
    assert {r["risk"] for r in rows} == {"Below breakeven"}


def test_margin_scenarios_thin_margin_is_flagged():
    rows = analytics.margin_scenarios(1000.0, None, 800.0, 18.0, 0.0, [])
    hold = next(r for r in rows if r["scenario"] == "Hold price")
    assert hold["risk"] == "Thin margin (<10%)"


def test_margin_scenarios_healthy_margin_is_ok():
    rows = analytics.margin_scenarios(1000.0, 900.0, 100.0, 18.0, 0.0, [900.0, 1100.0])
    hold = next(r for r in rows if r["scenario"] == "Hold price")
    assert hold["risk"] == "OK"
    assert hold["peers_cheaper"] == 1


def test_margin_at_fees_reduce_contribution():
    m = analytics.margin_at(1180.0, 500.0, 18.0, 10.0)
    assert m["contribution"] == pytest.approx(400.0)


def test_margin_at_rejects_100_percent_fees_with_a_clear_message():
    with pytest.raises(ValueError, match="below 100%"):
        analytics.margin_at(1180.0, 500.0, 18.0, 100.0)


# ------------------------------------------------------------------ peers, lists, history

def test_products_in_filters_by_city_and_category(data):
    assert {o["asin"] for o in analytics.products_in(data, "delhi", "All categories")} == {
        sd.LAPTOP_A, sd.LAPTOP_B, sd.CASE}
    assert [o["asin"] for o in analytics.products_in(data, "mumbai", "Laptops")] == [sd.LAPTOP_A]


def test_cities_are_sorted_by_name(data):
    assert [c["slug"] for c in analytics.cities(data)] == ["chennai", "mumbai", "delhi"]


def test_categories_in_a_city(data):
    assert analytics.categories_in(data, "delhi") == ["Laptops", "Mobile Accessories"]


def test_peers_are_in_the_same_subcategory_only(data):
    sel = _row(data, sd.LAPTOP_A, "delhi")
    assert [p["asin"] for p in analytics.peers_of(data, "delhi", sel)] == [sd.LAPTOP_B]


def test_price_history_keeps_one_price_per_day_inside_the_window(data):
    points = analytics.price_history(data, sd.LAPTOP_A, "delhi", 7)
    assert [p for _, p in points] == [49500.0, 50500.0, 50000.0]
    assert [d for d, _ in points] == sorted(d for d, _ in points)


def test_price_history_window_excludes_older_points(data):
    assert len(analytics.price_history(data, sd.LAPTOP_A, "delhi", 1)) == 1


def test_gap_matrix_is_relative_to_the_median_per_product(data):
    cities, labels, grid = analytics.gap_matrix(data, "Laptops")
    assert cities == ["Chennai", "Mumbai", "New Delhi"]
    row_a = next(i for i, label in enumerate(labels) if sd.LAPTOP_A in label)
    assert grid[row_a][cities.index("New Delhi")] == 0.0
    assert grid[row_a][cities.index("Mumbai")] == 4.0
    row_b = next(i for i, label in enumerate(labels) if sd.LAPTOP_B in label)
    assert grid[row_b][cities.index("Chennai")] is None


def test_category_overview_averages_per_category(data):
    rows = {r["category"]: r for r in analytics.category_overview(data, "delhi")}
    assert rows["Laptops"]["products"] == 2
    assert rows["Laptops"]["avg_price"] == 46000
    assert rows["Mobile Accessories"]["avg_price"] == 800


# ------------------------------------------------------------------ sentiment and basket

def test_sentiment_model_separates_clear_positive_and_negative_reviews(data):
    sent = analytics._build_sentiment(data["reviews"])
    assert sent["accuracy"] is not None
    five_star = [r for r in sent["reviews"] if r["rating"] == 5]
    positive = sum(r["sentiment"] == "Positive" for r in five_star)
    assert positive / len(five_star) >= 0.8
    assert len(sent["pos_terms"]) == 12 and len(sent["neg_terms"]) == 12


def test_product_sentiment_summarises_one_product(data):
    sent = analytics._build_sentiment(data["reviews"])
    summary = analytics.product_sentiment(sent, sd.LAPTOP_A)
    assert summary["reviews"] == 5
    assert summary["avg_rating"] == 3.4
    assert summary["positive_pct"] + summary["negative_pct"] <= 100


def test_product_sentiment_is_none_without_reviews(data):
    sent = analytics._build_sentiment(data["reviews"])
    assert analytics.product_sentiment(sent, sd.CASE) is None
    assert analytics.product_sentiment(None, sd.LAPTOP_A) is None


def test_basket_rules_pair_products_reviewed_by_the_same_people(data):
    rules = analytics.basket_rules(data, min_count=2)
    rule = next(r for r in rules if r["antecedent"] == "Acer Aspire 5 Laptop 15.6 inch")
    assert rule["consequent"] == "Dell Inspiron 3520 Laptop"
    assert rule["pair_count"] == 2
    assert rule["lift"] == 1.0


def test_basket_rules_only_keep_pairs_meeting_the_minimum_count(data):
    # Only Ravi and Asha reviewed two products each, so every surviving pair has count 2.
    rules = analytics.basket_rules(data, min_count=2)
    assert rules and all(r["pair_count"] >= 2 for r in rules)
    assert analytics.basket_rules(data, min_count=3) == []


# ------------------------------------------------------------------ health

def test_health_counts_offers_stale_and_history(data):
    health = analytics.health(data)
    assert health["products"] == 3
    assert health["offers"] == 5
    assert health["stale"] == 1  # the Chennai offer is 5 days old
    assert health["history_points"] == len(data["history"])
    assert health["stale_hours"] == django_settings.STALE_HOURS


# ------------------------------------------------------------------ loading from MongoDB

@requires_mongo
@pytest.mark.integration
def test_load_reads_priced_offers_and_history_from_mongo(mongo_db):
    raw = analytics._load_raw()
    assert len(raw["offers"]) == 5  # the unpriced Dell offer in Mumbai is excluded
    assert {o["category"] for o in raw["offers"]} == {"Laptops", "Mobile Accessories"}
    assert len(raw["history"]) == len(sd.price_history())
    assert len(raw["reviews"]) == len(sd.reviews())
    assert len(raw["runs"]) == len(sd.CRAWL_RUNS)
