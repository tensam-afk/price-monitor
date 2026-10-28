import csv

import pytest
import requests

import monitor
import storage

URL = "https://shop.example.com/earbuds"


@pytest.fixture
def conn(tmp_path):
    c = storage.connect(tmp_path / "prices.db")
    yield c
    c.close()


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def __call__(self, subject, body):
        self.messages.append((subject, body))
        return ["fake"]


def result(price, in_stock=True):
    return {"price": price, "currency": "INR", "in_stock": in_stock, "source": "json-ld"}


def run(conn, product, *prices_or_results):
    send = FakeNotifier()
    for r in prices_or_results:
        monitor.record(conn, product, r if isinstance(r, dict) else result(r), send)
    return send.messages


def test_target_alert_on_first_check(conn):
    messages = run(conn, {"name": "Earbuds", "url": URL, "target_price": 1500}, 1299)

    assert len(messages) == 1
    subject, body = messages[0]
    assert subject == "Price alert: Earbuds"
    assert "Now ₹1,299.00" in body
    assert "at or below your target of ₹1,500.00" in body
    assert URL in body


def test_no_alert_above_target(conn):
    assert run(conn, {"name": "Earbuds", "url": URL, "target_price": 1000}, 1299) == []


def test_target_alert_not_repeated_for_same_price(conn):
    product = {"name": "Earbuds", "url": URL, "target_price": 1500}
    assert len(run(conn, product, 1299, 1299, 1299)) == 1


def test_target_alert_again_when_price_falls_further(conn):
    product = {"name": "Earbuds", "url": URL, "target_price": 1500}
    messages = run(conn, product, 1299, 1299, 1199, 1250)

    assert len(messages) == 2
    assert "Now ₹1,199.00, was ₹1,299.00" in messages[1][1]


def test_target_alert_when_price_crosses_target(conn):
    product = {"name": "Earbuds", "url": URL, "target_price": 1000}
    messages = run(conn, product, 1299, 1099, 999)
    assert len(messages) == 1


def test_percent_drop(conn):
    product = {"name": "Earbuds", "url": URL, "drop_percent": 10}
    messages = run(conn, product, 2000, 1900, 1700, 1700)

    # 2000 -> 1900 is only 5%, 1900 -> 1700 is 10.5%, then no change
    assert len(messages) == 1
    assert "down 10.5% since the last check" in messages[0][1]


def test_back_in_stock(conn):
    product = {"name": "Earbuds", "url": URL}
    messages = run(conn, product, result(1299, True), result(1299, False), result(1299, True), result(1299, True))

    assert len(messages) == 1
    assert "back in stock" in messages[0][1]


def test_no_back_in_stock_alert_when_stock_was_unknown(conn):
    product = {"name": "Earbuds", "url": URL}
    assert run(conn, product, result(1299, None), result(1299, True)) == []


def test_missing_price_uses_last_known_price(conn):
    product = {"name": "Earbuds", "url": URL, "drop_percent": 10}
    messages = run(conn, product, 2000, result(None, False), result(1500, True))

    assert len(messages) == 1
    assert "down 25.0%" in messages[0][1]
    assert "back in stock" in messages[0][1]


def test_checks_are_saved(conn):
    run(conn, {"name": "Earbuds", "url": URL}, 1299, 1199)
    rows = storage.history(conn, "earbuds")

    assert [r["price"] for r in rows] == [1299.0, 1199.0]
    assert rows[0]["currency"] == "INR"
    assert rows[0]["in_stock"] == 1


class FakeResponse:
    def __init__(self, html):
        self.content = html.encode()

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, timeout):
        if url not in self.pages:
            raise requests.ConnectionError(f"could not reach {url}")
        return FakeResponse(self.pages[url])


def test_one_failing_product_does_not_stop_the_run(conn):
    products = [
        {"name": "Down", "url": "https://down.example.com/p"},
        {"name": "No price", "url": "https://shop.example.com/empty"},
        {"name": "Earbuds", "url": URL, "selector": ".price", "target_price": 1500},
    ]
    session = FakeSession({
        "https://shop.example.com/empty": "<p>nothing</p>",
        URL: '<span class="price">₹1,299</span>',
    })
    send = FakeNotifier()

    failed = monitor.check(products, conn, session, send=send, delay=0)

    assert failed == 2
    assert [r["name"] for r in storage.all_checks(conn)] == ["Earbuds"]
    assert len(send.messages) == 1


def test_load_products(tmp_path):
    path = tmp_path / "products.toml"
    path.write_text('[[product]]\nname = "A"\nurl = "https://example.com"\ntarget_price = 10\n')
    assert monitor.load_products(path) == [{"name": "A", "url": "https://example.com", "target_price": 10}]

    path.write_text('[[product]]\nname = "No url"\n')
    with pytest.raises(ValueError, match="needs a name and a url"):
        monitor.load_products(path)


def test_default_products_file():
    products = monitor.load_products(monitor.DEFAULT_PRODUCTS)
    assert products
    assert any("target_price" in p for p in products)


def test_history_and_export(conn, tmp_path, capsys):
    run(conn, {"name": "Earbuds", "url": URL}, 1299, result(1199, False))

    assert monitor.show_history(conn, "Earbuds", 20) == 0
    out = capsys.readouterr().out
    assert "₹1,199.00" in out
    assert "-100.00" in out

    assert monitor.show_history(conn, "Nope", 20) == 1
    assert "Known products: Earbuds" in capsys.readouterr().out

    out_file = tmp_path / "prices.csv"
    assert monitor.export(conn, out_file) == 2
    with open(out_file, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert rows[1]["price"] == "1199.0"
    assert rows[1]["in_stock"] == "no"
