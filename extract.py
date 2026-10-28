import json
import re

from bs4 import BeautifulSoup

# a group of three digits after a space is a thousands separator ("1 299,00 €")
NUMBER_RE = re.compile(r"\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d+)?|\d[\d.,]*")
CODES = ["INR", "USD", "GBP", "EUR", "AUD", "CAD", "AED", "SGD"]
SYMBOLS = {"₹": "INR", "£": "GBP", "€": "EUR", "$": "USD"}
IN_STOCK = {"instock", "limitedavailability", "onlineonly", "instoreonly"}


def parse_price(value):
    if isinstance(value, (int, float)):
        return float(value)
    m = NUMBER_RE.search(str(value or ""))
    if not m:
        return None

    num = re.sub(r"\s", "", m.group()).rstrip(".,")
    if "," in num and "." in num:
        # whichever separator comes last is the decimal point: 1,299.00 vs 1.299,00
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        whole, _, frac = num.rpartition(",")
        # 1,299 and 1,00,000 use commas for thousands, 51,77 is a decimal comma
        num = num.replace(",", "") if len(frac) == 3 else whole.replace(",", "") + "." + frac
    elif num.count(".") > 1:
        num = num.replace(".", "")

    try:
        return float(num)
    except ValueError:
        return None


def detect_currency(text):
    text = str(text or "")
    for code in CODES:
        if re.search(rf"\b{code}\b", text):
            return code
    if re.search(r"\bRs\.?\s*\d", text, re.IGNORECASE):
        return "INR"
    for symbol, code in SYMBOLS.items():
        if symbol in text:
            return code
    return None


def parse_stock(value):
    if not value:
        return None
    # schema.org URLs, bare names and og values: "https://schema.org/InStock", "in stock", "oos"
    word = str(value).rstrip("/").rsplit("/", 1)[-1].replace(" ", "").replace("_", "").lower()
    return word in IN_STOCK


def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def types(node):
    return set(as_list(node.get("@type")))


def jsonld_nodes(soup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            # strict=False accepts raw newlines inside strings, which some shop themes emit
            data = json.loads(tag.get_text(), strict=False)
        except ValueError:
            continue
        for node in as_list(data):
            if isinstance(node, dict):
                yield node
                yield from (n for n in as_list(node.get("@graph")) if isinstance(n, dict))


def from_jsonld(soup):
    nodes = list(jsonld_nodes(soup))
    offers = [o for n in nodes if "Product" in types(n) for o in as_list(n.get("offers"))]
    offers += [n for n in nodes if types(n) & {"Offer", "AggregateOffer"}]

    for offer in offers:
        if not isinstance(offer, dict):
            continue
        price = offer.get("lowPrice") if "AggregateOffer" in types(offer) else None
        if price is None:
            price = offer.get("price")
        currency = offer.get("priceCurrency")
        spec = next(iter(as_list(offer.get("priceSpecification"))), None)
        if price is None and isinstance(spec, dict):
            price, currency = spec.get("price"), currency or spec.get("priceCurrency")

        price = parse_price(price)
        if price is None and offer.get("availability") is None:
            continue
        return {
            "price": price,
            "currency": currency or detect_currency(offer.get("price")),
            "in_stock": parse_stock(offer.get("availability")),
            "source": "json-ld",
        }
    return None


def meta_content(soup, *properties):
    tag = soup.find("meta", attrs={"property": list(properties)})
    return tag.get("content") if tag else None


def from_meta(soup):
    amount = meta_content(soup, "product:price:amount", "og:price:amount")
    if amount:
        return {
            "price": parse_price(amount),
            "currency": meta_content(soup, "product:price:currency", "og:price:currency") or detect_currency(amount),
            "in_stock": parse_stock(meta_content(soup, "product:availability", "og:availability")),
            "source": "meta",
        }

    el = soup.find(attrs={"itemprop": "price"})
    if el is None:
        return None
    text = el.get("content") or el.get_text(" ", strip=True)
    cur = soup.find(attrs={"itemprop": "priceCurrency"})
    stock = soup.find(attrs={"itemprop": "availability"})
    return {
        "price": parse_price(text),
        "currency": (cur and (cur.get("content") or cur.get_text(strip=True))) or detect_currency(el.get_text()),
        "in_stock": parse_stock(stock and (stock.get("href") or stock.get("content") or stock.get_text(strip=True))),
        "source": "itemprop",
    }


def from_selector(soup, selector):
    if not selector:
        return None
    el = soup.select_one(selector)
    if el is None:
        return None
    text = el.get_text(" ", strip=True)
    return {"price": parse_price(text), "currency": detect_currency(text), "in_stock": None, "source": "css"}


def extract_price(html, selector=None):
    soup = BeautifulSoup(html, "html.parser")
    found = [r for r in (from_jsonld(soup), from_meta(soup), from_selector(soup, selector)) if r]

    for r in found:
        if r["price"] is not None:
            return r
    # an out-of-stock page often hides the price but still says it's out of stock
    for r in found:
        if r["in_stock"] is not None:
            return r
    return None
