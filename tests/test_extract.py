import json
from pathlib import Path

import pytest

from extract import detect_currency, extract_price, parse_price, parse_stock

FIXTURES = Path(__file__).parent / "fixtures"


def page(*jsonld, head="", body=""):
    scripts = "".join(
        f'<script type="application/ld+json">{d if isinstance(d, str) else json.dumps(d)}</script>'
        for d in jsonld
    )
    return f"<html><head>{scripts}{head}</head><body>{body}</body></html>"


PRODUCT = {
    "@context": "https://schema.org",
    "@type": "Product",
    "name": "Wireless Earbuds",
    "offers": {
        "@type": "Offer",
        "price": "1299.00",
        "priceCurrency": "INR",
        "availability": "https://schema.org/InStock",
    },
}


def test_jsonld_single_product():
    assert extract_price(page(PRODUCT)) == {
        "price": 1299.0, "currency": "INR", "in_stock": True, "source": "json-ld",
    }


def test_jsonld_list():
    crumbs = {"@type": "BreadcrumbList", "itemListElement": []}
    assert extract_price(page([crumbs, PRODUCT]))["price"] == 1299.0


def test_jsonld_graph():
    data = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "WebPage", "name": "Earbuds"},
            {**PRODUCT, "@type": ["Product", "Thing"]},
        ],
    }
    assert extract_price(page(data))["price"] == 1299.0


def test_jsonld_offers_list():
    data = {**PRODUCT, "offers": [
        {"@type": "Offer", "price": 54.99, "priceCurrency": "USD", "availability": "http://schema.org/OutOfStock"},
        {"@type": "Offer", "price": 59.99, "priceCurrency": "USD"},
    ]}
    result = extract_price(page(data))
    assert result["price"] == 54.99
    assert result["currency"] == "USD"
    assert result["in_stock"] is False


def test_jsonld_aggregate_offer():
    data = {**PRODUCT, "offers": {
        "@type": "AggregateOffer", "lowPrice": "899", "highPrice": "1499", "priceCurrency": "INR",
    }}
    result = extract_price(page(data))
    assert result["price"] == 899.0
    assert result["in_stock"] is None


def test_jsonld_price_specification():
    data = {**PRODUCT, "offers": {
        "@type": "Offer",
        "priceSpecification": {"@type": "UnitPriceSpecification", "price": 24.5, "priceCurrency": "EUR"},
    }}
    result = extract_price(page(data))
    assert (result["price"], result["currency"]) == (24.5, "EUR")


def test_out_of_stock_without_price():
    data = {**PRODUCT, "offers": {"@type": "Offer", "availability": "https://schema.org/SoldOut"}}
    result = extract_price(page(data))
    assert result["price"] is None
    assert result["in_stock"] is False


def test_malformed_jsonld_does_not_hide_the_good_one():
    html = page('{"@type": "Organization", "name": "Shop",}', PRODUCT)
    assert extract_price(html)["price"] == 1299.0


def test_raw_newline_inside_jsonld_string():
    html = page('{"@type": "Product", "description": "line one\nline two", "offers": {"price": "10"}}')
    assert extract_price(html)["price"] == 10.0


def test_og_meta_tags():
    head = """
    <meta property="og:price:amount" content="2,499.00">
    <meta property="og:price:currency" content="INR">
    <meta property="product:availability" content="in stock">
    """
    assert extract_price(page(head=head)) == {
        "price": 2499.0, "currency": "INR", "in_stock": True, "source": "meta",
    }


def test_product_meta_tags_out_of_stock():
    head = """
    <meta property="product:price:amount" content="19.99">
    <meta property="product:price:currency" content="GBP">
    <meta property="product:availability" content="oos">
    """
    result = extract_price(page(head=head))
    assert (result["price"], result["in_stock"]) == (19.99, False)


def test_itemprop_content_attribute():
    body = """
    <div itemscope itemtype="https://schema.org/Product">
      <span itemprop="price" content="349.00">₹349</span>
      <meta itemprop="priceCurrency" content="INR">
      <link itemprop="availability" href="https://schema.org/InStock">
    </div>
    """
    assert extract_price(page(body=body)) == {
        "price": 349.0, "currency": "INR", "in_stock": True, "source": "itemprop",
    }


def test_itemprop_text():
    result = extract_price(page(body='<span itemprop="price">$1,099</span>'))
    assert (result["price"], result["currency"]) == (1099.0, "USD")


def test_css_selector_fallback():
    body = '<div class="pdp"><span class="sale">Rs. 1,299</span></div>'
    result = extract_price(page(body=body), selector=".pdp .sale")
    assert result == {"price": 1299.0, "currency": "INR", "in_stock": None, "source": "css"}


def test_jsonld_wins_over_selector():
    html = page(PRODUCT, body='<span class="price">₹999</span>')
    assert extract_price(html, selector=".price")["price"] == 1299.0


def test_nothing_found():
    assert extract_price(page(body="<p>No price here</p>"), selector=".price") is None


def test_books_toscrape_page():
    result = extract_price((FIXTURES / "book.html").read_bytes(), selector=".price_color")
    assert result == {"price": 51.77, "currency": "GBP", "in_stock": None, "source": "css"}


@pytest.mark.parametrize("text,expected", [
    ("₹1,299.00", 1299.0),
    ("₹ 1,299", 1299.0),
    ("Rs. 1,299", 1299.0),
    ("Rs.1299", 1299.0),
    ("₹1,00,000", 100000.0),
    ("£51.77", 51.77),
    ("Â£9.00", 9.0),
    ("$1,099", 1099.0),
    ("1.299,00 €", 1299.0),
    ("1.299,00€", 1299.0),
    ("1 299,00 €", 1299.0),
    ("1\xa0299,00\xa0€", 1299.0),
    ("51,77 €", 51.77),
    ("EUR 1.234.567", 1234567.0),
    ("1299", 1299.0),
    (1299, 1299.0),
    (49.5, 49.5),
    ("Free", None),
    ("", None),
    (None, None),
])
def test_parse_price(text, expected):
    assert parse_price(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("₹1,299", "INR"),
    ("Rs. 499", "INR"),
    ("£51.77", "GBP"),
    ("1.299,00 €", "EUR"),
    ("$20", "USD"),
    ("USD 20", "USD"),
    ("1299", None),
])
def test_detect_currency(text, expected):
    assert detect_currency(text) == expected


@pytest.mark.parametrize("value,expected", [
    ("https://schema.org/InStock", True),
    ("http://schema.org/LimitedAvailability", True),
    ("InStock", True),
    ("in stock", True),
    ("https://schema.org/OutOfStock", False),
    ("oos", False),
    ("", None),
    (None, None),
])
def test_parse_stock(value, expected):
    assert parse_stock(value) == expected
