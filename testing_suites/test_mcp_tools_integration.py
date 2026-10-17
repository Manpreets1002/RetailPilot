"""Integration tests for every MCP tool, run against the seeded test database.

Skipped automatically when MongoDB is not reachable.
"""

import pytest

import support  # noqa: F401
import mcp_server as ms
import sample_data as sd
from support import requires_mongo

pytestmark = [requires_mongo, pytest.mark.integration]


@pytest.fixture(autouse=True)
def seeded(mongo_db):
    return mongo_db


# ------------------------------------------------------------------ discovery

def test_list_categories_counts_products(run):
    counts = {r["name"]: r["products"] for r in run(ms.list_categories())}
    assert counts == {"Laptops": 2, "Mobile Accessories": 1}


def test_resolve_city_by_slug_pincode_and_unknown(run):
    assert run(ms.resolve_city("delhi"))["covered"] is True
    assert run(ms.resolve_city("Mumbai"))["slug"] == "mumbai"
    assert run(ms.resolve_city("110001"))["slug"] == "delhi"
    assert run(ms.resolve_city("Atlantis"))["slug"] is None


def test_list_cities_reports_which_have_data(run):
    rows = {c["slug"]: c for c in run(ms.list_cities())}
    assert len(rows) == 10
    assert rows["delhi"]["covered"] and rows["mumbai"]["covered"] and rows["chennai"]["covered"]
    assert rows["pune"]["covered"] is False


# ------------------------------------------------------------------ customer tools

def test_search_reads_the_last_crawl_and_ranks_by_rating(run):
    res = run(ms.search_products(city="delhi", category="laptop"))
    assert [r["asin"] for r in res["results"]] == [sd.LAPTOP_A, sd.LAPTOP_B]
    assert res["matched"] == 2


def test_search_query_requires_every_word(run):
    res = run(ms.search_products(city="delhi", category="laptop", query="battery"))
    assert [r["asin"] for r in res["results"]] == [sd.LAPTOP_A]
    assert run(ms.search_products(city="delhi", category="laptop", query="battery silver"))["matched"] == 0


def test_search_price_filter_and_sort(run):
    res = run(ms.search_products(city="delhi", max_price=45000, sort_by="price_asc"))
    assert [r["asin"] for r in res["results"]] == [sd.CASE, sd.LAPTOP_B]


def test_search_includes_offers_from_an_older_crawl(run):
    # Chennai's only offer was seen days ago; it is still the last crawl, so it is returned.
    res = run(ms.search_products(city="chennai", category="laptop"))
    assert res["matched"] == 1
    assert res["results"][0]["observed_at"] is not None


def test_get_product_returns_the_city_price(run):
    res = run(ms.get_product(sd.LAPTOP_A, "delhi"))
    assert res["offer"]["price"] == 50000.0
    assert res["note"] is None


def test_get_product_in_uncrawled_city_has_a_note(run):
    res = run(ms.get_product(sd.LAPTOP_A, "pune"))
    assert res["offer"] is None
    assert "Not crawled" in res["note"]


def test_get_product_unknown_asin_is_an_error(run):
    assert "error" in run(ms.get_product("B0NOTREAL00", "delhi"))


def test_product_insights_split_positive_and_negative_reviews(run):
    res = run(ms.customer_product_insights(sd.LAPTOP_A, "delhi"))
    assert res["reviews_stored"] == 5
    assert all(r["rating"] >= 4 for r in res["positive_reviews"])
    assert len(res["negative_reviews"]) == 2
    assert all(r["rating"] <= 2 for r in res["negative_reviews"])


def test_compare_lists_specs_shared_by_two_products(run):
    res = run(ms.customer_compare_products([sd.LAPTOP_A, sd.LAPTOP_B, "B0NOTFOUND0"], "delhi"))
    assert "Colour" in res["shared_spec_names"]
    assert any(p.get("error") == "not found" for p in res["products"])


def test_get_reviews_filters_by_rating_and_keyword(run):
    high = run(ms.get_reviews(sd.LAPTOP_A, min_rating=4))
    assert len(high["reviews"]) == 3
    assert all(r["rating"] >= 4 for r in high["reviews"])
    heat = run(ms.get_reviews(sd.LAPTOP_A, keyword="heat"))
    assert len(heat["reviews"]) == 1


def test_search_reviews_across_products_by_keyword(run):
    assert run(ms.search_reviews("battery"))["matched"] == 3
    assert run(ms.search_reviews("zebra"))["matched"] == 0


def test_price_history_is_newest_first(run):
    observations = run(ms.get_price_history(sd.LAPTOP_A, "delhi"))["observations"]
    assert len(observations) == 3
    assert observations[0]["price"] == 50000.0


def test_customer_best_returns_only_priced_products_from_the_last_crawl(run):
    res = run(ms.customer_best_today(category="Laptops", city="delhi", limit=5))
    assert sorted(i["asin"] for i in res["items"]) == sorted([sd.LAPTOP_A, sd.LAPTOP_B])


def test_customer_best_honours_exclude_asins(run):
    res = run(ms.customer_best_today(category="Laptops", city="delhi", exclude_asins=[sd.LAPTOP_A]))
    assert [i["asin"] for i in res["items"]] == [sd.LAPTOP_B]


def test_customer_best_in_city_without_data_gives_a_note(run):
    res = run(ms.customer_best_today(category="Laptops", city="pune"))
    assert res["items"] == []
    assert "note" in res


def test_customer_best_returns_an_older_crawl_instead_of_nothing(run):
    res = run(ms.customer_best_today(category="Laptops", city="chennai"))
    assert [i["asin"] for i in res["items"]] == [sd.LAPTOP_A]


# ------------------------------------------------------------------ retailer tools

def test_price_position_against_the_nearest_competitor(run):
    res = run(ms.retailer_price_position(sd.LAPTOP_A, "delhi"))
    assert res["your_price"] == 50000.0
    assert res["competitors_compared"] == 1
    assert res["lowest_competitor_price"] == 42000.0
    assert res["gap_vs_lowest_pct"] == pytest.approx(19.05, abs=0.01)
    assert res["position"] == "priciest"


def test_price_position_for_an_unpriced_city_is_an_error(run):
    assert "error" in run(ms.retailer_price_position(sd.LAPTOP_A, "pune"))


def test_competitor_prices_list_the_gap_to_you(run):
    res = run(ms.retailer_competitor_prices(sd.LAPTOP_A, "delhi"))
    assert [c["asin"] for c in res["competitors"]] == [sd.LAPTOP_B]
    assert res["competitors"][0]["gap_vs_you_pct"] == -16.0


def test_price_trend_keeps_the_last_price_per_day(run):
    res = run(ms.retailer_price_trend(sd.LAPTOP_A, "delhi", days=7))
    assert sorted(res["daily_last_price"].values()) == [49500.0, 50000.0, 50500.0]


def test_gap_heatmap_shows_each_city_against_the_median(run):
    res = run(ms.retailer_price_gap_heatmap("Laptops"))
    rows = {r["asin"]: r for r in res["products"]}
    assert set(rows) == {sd.LAPTOP_A}  # Dell is priced in one city only, so it is left out
    assert rows[sd.LAPTOP_A]["gap_pct_by_city"]["delhi"] == 0.0
    assert rows[sd.LAPTOP_A]["gap_pct_by_city"]["mumbai"] == 4.0


def test_margin_scenarios_with_a_cost(run):
    res = run(ms.retailer_margin_scenarios(sd.LAPTOP_A, "delhi", unit_cost=30000))
    names = [s["scenario"] for s in res["scenarios"]]
    assert names == ["match_lowest_competitor", "hold", "raise_5_pct"]
    match = res["scenarios"][0]
    assert match["price"] == 42000.0
    assert match["contribution"] == pytest.approx(5593.22, abs=0.01)
    assert match["risk"] == "ok"


def test_margin_scenarios_flag_prices_below_breakeven(run):
    res = run(ms.retailer_margin_scenarios(sd.LAPTOP_A, "delhi", unit_cost=45000))
    assert res["scenarios"][0]["risk"] == "below_breakeven"


def test_margin_scenarios_reject_100_percent_fees(run):
    res = run(ms.retailer_margin_scenarios(sd.LAPTOP_A, "delhi", unit_cost=30000, fees_pct=100))
    assert "below 100%" in res["error"]


def test_review_sentiment_shares_and_average(run):
    res = run(ms.retailer_review_sentiment(sd.LAPTOP_A))
    assert res["reviews"] == 5
    assert res["positive_pct"] == 60.0
    assert res["negative_pct"] == 40.0
    assert res["mixed_pct"] == 0.0
    assert res["average_rating"] == 3.4


def test_review_sentiment_without_reviews(run):
    res = run(ms.retailer_review_sentiment(sd.CASE))
    assert res["reviews"] == 0


def test_market_basket_finds_products_reviewed_by_the_same_people(run):
    res = run(ms.retailer_market_basket(min_count=2))
    assert res["baskets"] == 2
    rule = next(r for r in res["rules"] if r["if_reviewed"] == sd.LAPTOP_A and r["also_reviewed"] == sd.LAPTOP_B)
    assert rule["pair_count"] == 2
    assert rule["lift"] == 1.0


def test_category_overview_for_one_city(run):
    res = run(ms.retailer_category_overview("Laptops", "delhi"))
    assert res["products"] == 2
    assert res["lowest_price"] == 42000.0
    assert res["highest_price"] == 50000.0
    assert res["average_price"] == 46000.0


def test_data_health_counts_stale_and_unpriced_offers(run):
    res = run(ms.retailer_data_health())
    assert res["products"] == 3
    assert res["city_offers"] == 6
    assert res["stale_offers"] == 1
    assert res["unpriced_offers"] == 1
    assert res["reviews"] == len(sd.reviews())
    assert res["recent_crawl_runs"]
