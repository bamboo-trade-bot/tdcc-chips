"""SQLite schema + 共用查詢工具。"""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "tdcc.db")

# 集保持股分級：level -> (下限股數, 上限股數 or None, 顯示名稱)
LEVELS = {
    1: (1, 999, "1-999"),
    2: (1000, 5000, "1,000-5,000"),
    3: (5001, 10000, "5,001-10,000"),
    4: (10001, 15000, "10,001-15,000"),
    5: (15001, 20000, "15,001-20,000"),
    6: (20001, 30000, "20,001-30,000"),
    7: (30001, 40000, "30,001-40,000"),
    8: (40001, 50000, "40,001-50,000"),
    9: (50001, 100000, "50,001-100,000"),
    10: (100001, 200000, "100,001-200,000"),
    11: (200001, 400000, "200,001-400,000"),
    12: (400001, 600000, "400,001-600,000"),
    13: (600001, 800000, "600,001-800,000"),
    14: (800001, 1000000, "800,001-1,000,000"),
    15: (1000001, None, "1,000,001 以上"),
}
LEVEL_ADJUST = 16   # 差異數調整
LEVEL_TOTAL = 17    # 合計

# 「大戶」門檻（張）-> 納入的 level 集合
BIG_THRESHOLDS = {
    50: [9, 10, 11, 12, 13, 14, 15],
    100: [10, 11, 12, 13, 14, 15],
    200: [11, 12, 13, 14, 15],
    400: [12, 13, 14, 15],
    600: [13, 14, 15],
    800: [14, 15],
    1000: [15],
}
# 「散戶」門檻（張以下）-> 納入的 level 集合
SMALL_THRESHOLDS = {
    5: [1, 2],
    10: [1, 2, 3],
    20: [1, 2, 3, 4, 5],
    50: [1, 2, 3, 4, 5, 6, 7, 8],
    100: [1, 2, 3, 4, 5, 6, 7, 8, 9],
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS holdings (
    date     TEXT    NOT NULL,
    stock_no TEXT    NOT NULL,
    level    INTEGER NOT NULL,
    people   INTEGER NOT NULL DEFAULT 0,
    shares   INTEGER NOT NULL DEFAULT 0,
    pct      REAL    NOT NULL DEFAULT 0,
    PRIMARY KEY (date, stock_no, level)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_holdings_stock ON holdings(stock_no, date);
CREATE INDEX IF NOT EXISTS idx_holdings_date  ON holdings(date, level);

CREATE TABLE IF NOT EXISTS stocks (
    stock_no    TEXT PRIMARY KEY,
    name        TEXT,
    market      TEXT,
    industry    TEXT,
    isin        TEXT,
    listed_date TEXT
);

-- 選股用的個股 metadata：市值、成交量、細產業。來自族群 Excel 裡的欄位，
-- 是「匯出當下」的快照，不是每週更新的資料。
CREATE TABLE IF NOT EXISTS stock_meta (
    stock_no     TEXT PRIMARY KEY,
    close        REAL,
    volume       INTEGER,   -- 成交量（張）
    market_cap   REAL,      -- 總市值（元）
    rank         INTEGER,   -- 市值排行名次
    sub_industry TEXT,      -- 細產業，逗號分隔的標籤
    source       TEXT,
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS groups (
    group_name TEXT NOT NULL,
    stock_no   TEXT NOT NULL,
    source     TEXT,
    PRIMARY KEY (group_name, stock_no)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_groups_stock ON groups(stock_no);

CREATE TABLE IF NOT EXISTS import_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT,
    kind     TEXT,
    source   TEXT,
    detail   TEXT
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path=DB_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(path=DB_PATH):
    conn = connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def available_dates(conn):
    """回傳資料庫中所有資料日期（新到舊）。"""
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM holdings ORDER BY date DESC")]


def set_meta(conn, key, value):
    conn.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, str(value)))


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def log_import(conn, kind, source, detail):
    conn.execute(
        "INSERT INTO import_log(ts,kind,source,detail) "
        "VALUES(datetime('now','localtime'),?,?,?)", (kind, source, detail))
