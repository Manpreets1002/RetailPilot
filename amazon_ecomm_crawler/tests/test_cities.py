import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import parsers
from http_client import page_location


def test_cities_file_has_ten_unique_cities():
    cities = config.load_cities()
    assert len(cities) == 10
    assert len({c["slug"] for c in cities}) == 10
    assert len({c["pincode"] for c in cities}) == 10
    assert all(len(c["pincode"]) == 6 and c["pincode"].isdigit() for c in cities)


def test_load_cities_rejects_missing_fields(tmp_path):
    f = tmp_path / "cities.json"
    f.write_text(json.dumps({"cities": [{"slug": "x", "name": "X"}]}))
    with pytest.raises(ValueError, match="pincode"):
        config.load_cities(f)


def test_select_cities_by_slug_name_or_pincode():
    cities = config.load_cities()
    assert config.select_cities(None, cities) == cities
    assert config.select_cities(["all"], cities) == cities
    picked = config.select_cities(["mumbai", "Chennai", "560001", "mumbai"], cities)
    assert [c["slug"] for c in picked] == ["mumbai", "chennai", "bengaluru"]
    with pytest.raises(SystemExit, match="Unknown city"):
        config.select_cities(["atlantis"], cities)


def test_page_location():
    html = '<span id="glow-ingress-line2" class="nav-line-2">\n Mumbai 400001&zwnj;\n </span>'
    assert page_location(html) == "Mumbai 400001"
    assert page_location("<html></html>") is None


def test_delivery_and_city_split():
    html = """
    <span id="productTitle">Earbuds</span>
    <div id="corePriceDisplay_desktop_feature_div"><span class="priceToPay"><span class="a-offscreen">₹999</span></span></div>
    <div id="availability">In stock</div>
    <div id="mir-layout-DELIVERY_BLOCK">
      <div id="mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE">
        <span data-csa-c-delivery-time="Monday, 19 October">FREE delivery Monday, 19 October . Details</span></div>
      <div id="mir-layout-DELIVERY_BLOCK-slot-SECONDARY_DELIVERY_MESSAGE_LARGE">Or fastest delivery Tomorrow . Details</div>
    </div>
    """
    d = parsers.parse_product_page(html, "B0TEST0001")
    assert d["delivery"] == "FREE delivery Monday, 19 October"
    assert d["delivery_date"] == "Monday, 19 October"
    assert d["fastest_delivery"] == "Or fastest delivery Tomorrow"
    assert d["deliverable"] is True

    common, city = parsers.split_city_fields(d)
    assert city["price"] == 999.0 and city["delivery_date"] == "Monday, 19 October"
    assert "price" not in common and "delivery" not in common
    assert common["title"] == "Earbuds" and common["asin"] == "B0TEST0001"


def test_undeliverable():
    html = '<div id="availability">Currently unavailable.</div>'
    assert parsers.parse_product_page(html, "B0TEST0002")["deliverable"] is False
