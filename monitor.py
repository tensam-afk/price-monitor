import argparse
import csv
import logging
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import notify
import storage
from extract import extract_price

# paths are relative to this file because Task Scheduler and cron don't start in the project folder
HERE = Path(__file__).parent
DEFAULT_DB = HERE / "data" / "prices.db"
DEFAULT_PRODUCTS = HERE / "products.toml"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
SYMBOLS = {"INR": "₹", "GBP": "£", "USD": "$", "EUR": "€"}

log = logging.getLogger("monitor")


def make_session():
    s = requests.Session()
    # a lot of shops answer 403 or serve a stripped page to clients that don't look like a browser
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"})
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s


def load_products(path):
    with open(path, "rb") as f:
        products = tomllib.load(f).get("product", [])
    for i, p in enumerate(products, 1):
        if not p.get("name") or not p.get("url"):
            raise ValueError(f"product #{i} in {path} needs a name and a url")
    return products


def format_price(price, currency):
    if price is None:
        return "-"
    if currency in SYMBOLS:
        return f"{SYMBOLS[currency]}{price:,.2f}"
    return f"{price:,.2f} {currency or ''}".strip()


def alert_reasons(product, prev, result):
    price, old, currency = result["price"], prev["price"], result["currency"]
    reasons = []

    target = product.get("target_price")
    # fire when the price first reaches the target or falls further, not on every check while it stays there
    if target is not None and price is not None and price <= target and (old is None or price < old):
        reasons.append(f"at or below your target of {format_price(target, currency)}")

    drop = product.get("drop_percent")
    if drop and price is not None and old:
        pct = (old - price) / old * 100
        if pct >= drop:
            reasons.append(f"down {pct:.1f}% since the last check")

    if prev["in_stock"] is False and result["in_stock"]:
        reasons.append("back in stock")
    return reasons


def record(conn, product, result, send):
    name, url = product["name"], product["url"]
    prev = storage.previous(conn, url)
    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    storage.add_check(conn, name, url, result, checked_at)

    price = format_price(result["price"], result["currency"])
    stock = {True: "", False: ", out of stock"}.get(result["in_stock"], "")
    log.info("%s: %s%s (via %s)", name, price, stock, result["source"])

    reasons = alert_reasons(product, prev, result)
    if reasons:
        line = f"Now {price}"
        if prev["price"] is not None and prev["price"] != result["price"]:
            line += f", was {format_price(prev['price'], result['currency'])}"
        body = "\n".join([line] + [f"- {r}" for r in reasons] + ["", url])
        sent = send(f"Price alert: {name}", body)
        log.info("%s: alert (%s) sent via %s", name, ", ".join(reasons), ", ".join(sent) or "console")
    return reasons


def check(products, conn, session, send=notify.send_all, delay=1.0):
    failed = 0
    for i, product in enumerate(products):
        if i and delay:
            time.sleep(delay)
        try:
            resp = session.get(product["url"], timeout=20)
            resp.raise_for_status()
            # pass bytes so bs4 can find the charset itself, many shops don't send one in the headers
            result = extract_price(resp.content, product.get("selector"))
        except Exception as e:
            log.warning("%s: skipped, %s", product["name"], e)
            failed += 1
            continue

        if result is None:
            log.warning("%s: no price found on %s", product["name"], product["url"])
            failed += 1
            continue
        record(conn, product, result, send)
    return failed


def show_history(conn, name, limit):
    rows = storage.history(conn, name, limit)
    if not rows:
        known = storage.names(conn)
        print(f'No checks recorded for "{name}".')
        if known:
            print("Known products: " + ", ".join(known))
        return 1

    print(rows[0]["name"])
    print(rows[0]["url"])
    print()
    print(f"{'checked (UTC)':<17}  {'price':>10}  {'change':>8}  stock")
    last = None
    for r in rows:
        change = ""
        if last is not None and r["price"] is not None and r["price"] != last:
            change = f"{r['price'] - last:+,.2f}"
        stock = {1: "yes", 0: "no"}.get(r["in_stock"], "-")
        print(f"{r['checked_at'][:16]:<17}  {format_price(r['price'], r['currency']):>10}  {change:>8}  {stock}")
        if r["price"] is not None:
            last = r["price"]
    return 0


def export(conn, path):
    rows = storage.all_checks(conn)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig so Excel shows the currency symbols properly
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(storage.COLUMNS)
        for r in rows:
            w.writerow([r["name"], r["url"], r["price"], r["currency"],
                        {1: "yes", 0: "no"}.get(r["in_stock"], ""), r["checked_at"]])
    return len(rows)


def main(argv=None):
    p = argparse.ArgumentParser(description="Watch product prices and get an alert when they drop.")
    p.add_argument("--db", default=DEFAULT_DB, help="SQLite file (default: data/prices.db)")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("check", help="check every product once and send any alerts")
    c.add_argument("--products", default=DEFAULT_PRODUCTS, help="TOML file (default: products.toml)")
    c.add_argument("--delay", type=float, default=1.0, help="seconds to wait between requests")

    h = sub.add_parser("history", help="show recorded prices for one product")
    h.add_argument("name")
    h.add_argument("-n", "--limit", type=int, default=20, help="last N checks (default: 20)")

    e = sub.add_parser("export", help="write every check to a CSV file")
    e.add_argument("-o", "--out", default="prices.csv")
    args = p.parse_args(argv)

    load_dotenv(HERE / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    conn = storage.connect(args.db)

    try:
        if args.command == "check":
            products = load_products(args.products)
            failed = check(products, conn, make_session(), delay=args.delay)
            log.info("checked %d products, %d failed", len(products), failed)
            # only fail the run if nothing worked, which usually means a network or config problem
            return 1 if products and failed == len(products) else 0
        if args.command == "history":
            return show_history(conn, args.name, args.limit)
        n = export(conn, args.out)
        print(f"wrote {n} rows to {args.out}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    # redirected output on Windows uses the ANSI code page, which has no ₹ sign
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    sys.exit(main())
