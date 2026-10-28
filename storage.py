import sqlite3
from pathlib import Path

COLUMNS = ["name", "url", "price", "currency", "in_stock", "checked_at"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    price REAL,
    currency TEXT,
    in_stock INTEGER,
    checked_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS checks_url ON checks (url);
"""


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def to_bool(value):
    return None if value is None else bool(value)


def previous(conn, url):
    last = conn.execute(
        "SELECT in_stock FROM checks WHERE url = ? ORDER BY id DESC LIMIT 1", (url,)
    ).fetchone()
    # compare against the last known price, not a check where the price was missing
    priced = conn.execute(
        "SELECT price FROM checks WHERE url = ? AND price IS NOT NULL ORDER BY id DESC LIMIT 1", (url,)
    ).fetchone()
    return {
        "price": priced["price"] if priced else None,
        "in_stock": to_bool(last["in_stock"]) if last else None,
    }


def add_check(conn, name, url, result, checked_at):
    with conn:
        conn.execute(
            "INSERT INTO checks (name, url, price, currency, in_stock, checked_at) VALUES (?, ?, ?, ?, ?, ?)",
            (name, url, result["price"], result["currency"], result["in_stock"], checked_at),
        )


def history(conn, name, limit=20):
    rows = conn.execute(
        "SELECT * FROM checks WHERE name = ? COLLATE NOCASE ORDER BY id DESC LIMIT ?", (name, limit)
    ).fetchall()
    return rows[::-1]


def names(conn):
    return [r["name"] for r in conn.execute("SELECT DISTINCT name FROM checks ORDER BY name")]


def all_checks(conn):
    return conn.execute(f"SELECT {', '.join(COLUMNS)} FROM checks ORDER BY id").fetchall()
