"""SQLite 儲存層：每日行情、三大法人、融資融券、基本面、大盤摘要與盤中大單。"""
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("STOCKAPP_DB", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "stock.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS stocks (
    code TEXT PRIMARY KEY,
    name TEXT,
    market TEXT            -- 'tse' 上市 / 'otc' 上櫃
);
CREATE TABLE IF NOT EXISTS daily_price (
    date TEXT, code TEXT,
    open REAL, high REAL, low REAL, close REAL,
    volume INTEGER,        -- 成交股數
    value INTEGER,         -- 成交金額
    change REAL,           -- 漲跌價差
    PRIMARY KEY (date, code)
);
CREATE TABLE IF NOT EXISTS institutional (
    date TEXT, code TEXT,
    foreign_net INTEGER, trust_net INTEGER, dealer_net INTEGER, total_net INTEGER,  -- 股數
    PRIMARY KEY (date, code)
);
CREATE TABLE IF NOT EXISTS margin (
    date TEXT, code TEXT,
    margin_balance INTEGER,  -- 融資餘額（張）
    short_balance INTEGER,   -- 融券餘額（張）
    PRIMARY KEY (date, code)
);
CREATE TABLE IF NOT EXISTS fundamentals (
    code TEXT PRIMARY KEY,
    pe REAL, pb REAL, dividend_yield REAL,
    rev_month TEXT, rev_yoy REAL, rev_mom REAL,
    updated TEXT
);
CREATE TABLE IF NOT EXISTS market_daily (
    date TEXT PRIMARY KEY,
    taiex REAL, taiex_change REAL, taiex_pct REAL,
    up_count INTEGER, down_count INTEGER,
    foreign_amt INTEGER, trust_amt INTEGER, dealer_amt INTEGER,   -- 買賣超金額（元）
    total_value INTEGER
);
CREATE TABLE IF NOT EXISTS collect_log (
    date TEXT, kind TEXT, status TEXT, at TEXT,
    PRIMARY KEY (date, kind)
);
CREATE TABLE IF NOT EXISTS big_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT, time TEXT, code TEXT, name TEXT,
    side TEXT,             -- 'buy' 外盤 / 'sell' 內盤 / 'neutral'
    price REAL, lots INTEGER, amount INTEGER,
    single INTEGER,        -- 1 = 量差等於最後一筆成交量，可能是單筆
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_big_date ON big_trades(date, id);
CREATE INDEX IF NOT EXISTS idx_price_code ON daily_price(code, date);
CREATE INDEX IF NOT EXISTS idx_inst_code ON institutional(code, date);
CREATE INDEX IF NOT EXISTS idx_margin_code ON margin(code, date);
"""


def connect(path=None):
    path = path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def session(path=None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert(conn, table, rows, keys):
    """rows: list of dict。以 INSERT OR REPLACE 寫入。"""
    if not rows:
        return 0
    cols = list(rows[0].keys())
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])
    return len(rows)


def upsert_stocks(conn, rows):
    """只新增或更新名稱，不覆蓋已知的市場別為空值。"""
    conn.executemany(
        "INSERT INTO stocks(code,name,market) VALUES(?,?,?) "
        "ON CONFLICT(code) DO UPDATE SET name=excluded.name, market=COALESCE(excluded.market, stocks.market)",
        [(r["code"], r["name"], r.get("market")) for r in rows],
    )
