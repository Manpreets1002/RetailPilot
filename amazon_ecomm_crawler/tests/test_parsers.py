import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import parsers

SEARCH_HTML = """
<html><body>
<div data-component-type="s-search-result" data-asin="B0TEST0001" class="s-result-item AdHolder">
  <h2><span>Test Earbuds Pro</span></h2>
  <span class="a-icon-alt">4.1 out of 5 stars</span>
  <span class="s-underline-text">(1,234)</span>
  <span class="a-price"><span class="a-offscreen">₹599</span></span>
  <span class="a-price a-text-price"><span class="a-offscreen">₹2,799</span></span>
  <span>(79% off)</span>
  <img class="s-image" src="https://img/x.jpg">
</div>
<div data-component-type="s-search-result" data-asin="B0TEST0002" class="s-result-item">
  <h2><span>Plain Cable</span></h2>
  <span class="a-price"><span class="a-offscreen">₹129</span></span>
</div>
<span class="s-pagination-item">1</span><span class="s-pagination-item">7</span>
<a class="s-pagination-next" href="/s?page=2">Next</a>
</body></html>
"""

PRODUCT_HTML = """
<html><body>
<span id="productTitle"> Bose QC Headphones </span>
<a id="bylineInfo">Visit the Bose Store</a>
<div id="corePriceDisplay_desktop_feature_div">
  <span class="priceToPay"><span class="a-offscreen">₹15,490.00</span></span>
  <span class="savingsPercentage">-44%</span>
  <span class="basisPrice"><span class="a-offscreen">₹27,900</span></span>
</div>
<span id="acrPopover"><span class="a-icon-alt">4.4 out of 5 stars</span></span>
<span id="acrCustomerReviewText">(21,605)</span>
<div id="histogramTable"><a aria-label="71 percent of reviews have 5 stars"></a>
  <a aria-label="7 percent of reviews have 1 stars"></a></div>
<div id="availability">In stock</div>
<div id="dealBadge_feature_div">Great Indian Festival NO_OF_HOURS hours Great Indian Festival</div>
<div id="wayfinding-breadcrumbs_feature_div"><a>Electronics</a><a>Headphones</a></div>
<div id="feature-bullets"><ul><li><span class="a-list-item">Noise cancelling</span></li></ul></div>
<div id="prodDetails"><table>
  <tr><th>Model Number</th><td>&lrm;QC-1</td></tr>
  <tr><th>Customer Reviews</th><td>4.4</td></tr>
</table></div>
<script>var x = {"hiRes":"https://img/a.jpg"},{"hiRes":"https://img/a.jpg"},{"hiRes":"https://img/b.jpg"};</script>
<div data-hook="review" id="R1">
  <span class="a-profile-name">Asha</span>
  <i data-hook="review-star-rating"><span class="a-icon-alt">5.0 out of 5 stars</span></i>
  <a data-hook="review-title"><span>5.0 out of 5 stars</span><span>Great sound</span></a>
  <span data-hook="review-date">Reviewed in India on 12 October 2026</span>
  <span data-hook="avp-badge">Verified Purchase</span>
  <span data-hook="review-body"><span>Loved it.</span></span>
  <span data-hook="helpful-vote-statement">12 people found this helpful</span>
</div>
</body></html>
"""


def test_value_helpers():
    assert parsers.parse_price("₹1,099.00") == 1099.0
    assert parsers.parse_price(None) is None
    assert parsers.parse_rating("4.1 out of 5 stars") == 4.1
    assert parsers.parse_count("(27,065)") == 27065
    assert parsers.parse_count("1.2K") == 1200
    assert parsers.parse_percent("-84%") == 84
    assert parsers.discount_pct(500, 1000) == 50
    assert parsers.discount_pct(500, None) is None


def test_asin_from_sponsored_url():
    url = "/sspa/click?ie=UTF8&url=%2FFoo-Bar%2Fdp%2FB0GTLSM4KD%2Fref%3Dsr_1_1"
    assert parsers.asin_from_url(url) == "B0GTLSM4KD"
    assert parsers.asin_from_url("/Foo/dp/B0CCZ26B5V?th=1") == "B0CCZ26B5V"


def test_search_page():
    r = parsers.parse_search_page(SEARCH_HTML)
    assert r["total_pages"] == 7
    assert r["next_page"] == "/s?page=2"
    a, b = r["products"]
    assert a["asin"] == "B0TEST0001"
    assert a["title"] == "Test Earbuds Pro"
    assert (a["price"], a["mrp"], a["discount_pct"]) == (599.0, 2799.0, 79)
    assert (a["rating"], a["rating_count"]) == (4.1, 1234)
    assert a["is_sponsored"] is True
    assert b["is_sponsored"] is False
    assert b["mrp"] is None and b["discount_pct"] is None


def test_product_page():
    d = parsers.parse_product_page(PRODUCT_HTML, "B0CCZ26B5V")
    assert d["title"] == "Bose QC Headphones"
    assert d["brand"] == "Bose"
    assert (d["price"], d["mrp"], d["discount_pct"], d["currency"]) == (15490.0, 27900.0, 44, "INR")
    assert (d["rating"], d["rating_count"]) == (4.4, 21605)
    assert d["rating_histogram"] == {"5": 71, "1": 7}
    assert d["deal_badge"] == "Great Indian Festival"
    assert d["breadcrumbs"] == ["Electronics", "Headphones"]
    assert d["specifications"] == {"Model Number": "QC-1"}
    assert d["images"] == ["https://img/a.jpg", "https://img/b.jpg"]
    [rev] = d["reviews"]
    assert rev["author"] == "Asha"
    assert rev["rating"] == 5.0
    assert rev["title"] == "Great sound"
    assert (rev["country"], rev["date"]) == ("India", "12 October 2026")
    assert rev["verified_purchase"] is True
    assert rev["helpful_votes"] == 12
    assert rev["body"] == "Loved it."
