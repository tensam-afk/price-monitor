# price-monitor

![tests](https://github.com/tensam-afk/price-monitor/actions/workflows/tests.yml/badge.svg)

Checks a list of product pages on a schedule, keeps a price history in SQLite, and sends a Telegram or email alert when a price hits your target, drops sharply, or an item comes back in stock.

**The typical client request:** "I sell on Amazon and Flipkart. I want to know when my competitors change their prices, without opening 30 tabs every morning. Just ping me when something drops."

**What you get:** a plain text file listing the products to watch, and a message on your phone when one of them gets cheaper or comes back in stock. Every check is saved, so you can see how a price moved over the last weeks or open the whole history in Excel. It runs on GitHub Actions for free, so there's no server to pay for or keep alive.

| | |
|---|---|
| Products | listed in `products.toml`: a name and a URL, plus an optional selector and alert rules |
| Price detection | structured product data first, then meta tags, then a CSS selector, so most shops need no custom code |
| Alerts | Telegram and/or email; printed to the console when neither is set up |
| History | SQLite file, viewable with `history` or exported to CSV |
| Schedule | GitHub Actions every 6 hours, or Task Scheduler / cron on your own machine |
| Failures | retries with backoff on 429 and 5xx; one broken page doesn't stop the run |

## How it works

For each product, `monitor.py check` downloads the page and looks for the price in this order:

1. **JSON-LD.** Most shops embed a `<script type="application/ld+json">` block with schema.org `Product` and `Offer` data for Google Shopping. This gives the price, the currency and the stock status in a stable format that doesn't change when the site is redesigned. Single objects, lists, `@graph`, lists of offers and `AggregateOffer` (the lowest price) are all handled.
2. **Meta tags.** `og:price:amount` / `product:price:amount`, or microdata `itemprop="price"`.
3. **Your CSS selector**, if the product has one in `products.toml`.

Price text is parsed in the common formats: `₹1,299.00`, `Rs. 1,299`, `₹1,00,000`, `$1,099`, `£51.77`, and European `1.299,00 €`.

The result is saved as a row in `data/prices.db`, then compared with the previous check for that product. An alert goes out when:

- the price is at or below `target_price` (once when it gets there, and again only if it falls further)
- the price fell by at least `drop_percent` since the last check
- the product was out of stock last time and is in stock now

Running the check again with nothing changed sends nothing.

## Setup

```
pip install -r requirements.txt
cp .env.example .env      # optional (Windows: copy .env.example .env)
```

| Channel  | Variables | Notes |
|----------|-----------|-------|
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Create a bot with @BotFather, send it a message, then get your chat ID from `https://api.telegram.org/bot<token>/getUpdates`. |
| Email    | `SMTP_USER`, `SMTP_PASSWORD`, `NOTIFY_EMAIL` | For Gmail, create an App Password. `SMTP_HOST` and `SMTP_PORT` default to Gmail. |

A channel turns on once all its variables are set. With none set, alerts are printed to the console.

Then list your products in `products.toml`:

```toml
[[product]]
name = "Wireless earbuds"
url = "https://shop.example.com/products/wireless-earbuds"
target_price = 999
drop_percent = 5

[[product]]
name = "Competitor kettle"
url = "https://www.example.com/kettle"
selector = ".product-price .amount"   # only if the page has no structured data
drop_percent = 10
```

The default file watches four books on [books.toscrape.com](https://books.toscrape.com), a site built for scraping practice. Those pages have no structured data, so they use the `.price_color` selector, and stock status isn't detected for them. On real shops the JSON-LD path usually works without any selector.

Check a site's terms of service and robots.txt before monitoring it. A few checks a day is far gentler than what a person browsing does, but some sites forbid automated access outright.

## Usage

```
# check every product once, save the prices, send any alerts
python monitor.py check

# price history for one product (name is case-insensitive)
python monitor.py history "Sharp Objects"

# every check ever recorded, as a CSV that opens cleanly in Excel
python monitor.py export -o prices.csv
```

On the first run with the default products, "Sharp Objects" (£47.82, target £50) triggers an alert:

```
15:13:46 Soumission: £50.10 (via css)
15:13:47 Sharp Objects: £47.82 (via css)

Price alert: Sharp Objects
Now £47.82
- at or below your target of £50.00

https://books.toscrape.com/catalogue/sharp-objects_997/index.html
```

Run it again and the prices are recorded, but no alert is sent because nothing changed.

```
$ python monitor.py history "Sharp Objects"
Sharp Objects
https://books.toscrape.com/catalogue/sharp-objects_997/index.html

checked (UTC)           price    change  stock
2026-10-27 09:43       £47.82            -
2026-10-27 09:43       £47.82            -
```

Use `--db path/to/file.db` before the command to keep a separate database, and `check --products other.toml` for a different product list. `python monitor.py -h` shows all options.

## Scheduling

### GitHub Actions (free, no server)

`.github/workflows/monitor.yml` runs `check` and commits the updated `data/prices.db` back to the repository, so the history survives between runs. In this demo repo the schedule is switched off and the workflow only runs when started by hand, so the repo doesn't fill up with automatic commits.

1. Push the repo to GitHub.
2. In **Settings > Secrets and variables > Actions**, add the alert variables from the table above as repository secrets. Leave out the ones you don't use.
3. Open the **Actions** tab, pick **monitor** and press **Run workflow** to test it once.
4. To run it every 6 hours, uncomment the `schedule` lines at the top of `monitor.yml`.

`data/` is in `.gitignore` so local test runs don't get committed by accident; the workflow adds the database with `git add -f`. Commits pushed by the workflow don't trigger the tests workflow, so the two don't run into each other. If every product fails (usually a network or config problem), the run is marked as failed and GitHub emails you.

To change the schedule, edit the `cron` line. It's in UTC, so `30 3 * * *` is 9:00 AM IST.

### Windows Task Scheduler

```
schtasks /create /tn price-monitor /sc hourly /mo 6 /tr "C:\path\to\python.exe C:\path\to\price-monitor\monitor.py check"
```

### cron (Linux, macOS, a Raspberry Pi)

```
0 */6 * * * /usr/bin/python3 /path/to/price-monitor/monitor.py check >> /path/to/price-monitor/monitor.log 2>&1
```

File paths are resolved relative to `monitor.py`, so these work from any working directory. Pick one place to run it: a local database and the one in the repo won't know about each other.

## Tests

The tests use small HTML snippets for each JSON-LD and meta tag variant plus a saved books.toscrape.com page in `tests/fixtures`, and a temporary database with a fake notifier for the alert rules. Nothing touches the network.

```
python -m pytest
```

## Limits

- Pages that only render the price with JavaScript aren't supported. Most shops still ship JSON-LD in the initial HTML, but for the rest you'd need Playwright.
- Large marketplaces (Amazon in particular) block automated requests quickly and change their markup often. For those, an official API or a paid scraping API is the reliable route.
- Stock status comes from structured data only. With a plain CSS selector the price is tracked but stock shows as unknown.
- GitHub's scheduled runs can start late when Actions is busy, and GitHub pauses schedules in repositories with no activity for 60 days.
- Each scheduled run adds a small commit. If that history bothers you, run it locally or push the database to a separate branch.
- If an alert fails to send (wrong SMTP password, Telegram down), the error is logged, and that particular alert isn't retried.

## License

MIT
