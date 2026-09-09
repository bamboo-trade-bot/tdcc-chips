"""靜態站的資料層。

單檔 HTML 塞不下完整的 17 分級明細（13 週 × 4000 檔 × 17 級 ≈ 90 萬筆），
所以這裡把每檔每週壓成 8 個數字，只留頁面真的會用到的指標。大戶門檻因此
固定成 400 張與 1000 張兩組（跟 Streamlit 版一致），不再能任意調。

檔案配置：
    static_data/weekly/YYYYMMDD.csv   每週一支，欄位見 WEEKLY_COLUMNS
    static_data/stocks.json           個股基本資料 + 市值／成交量／細產業
    static_data/groups.json           族群 -> 成分股

每週 CSV 各自獨立，排程只會新增一支，不會改到舊的，repo diff 乾淨。
"""
import csv
import json
import os
import re

from . import db

STATIC_DIR = os.path.join(db.BASE_DIR, "static_data")
WEEKLY_DIR = os.path.join(STATIC_DIR, "weekly")
STOCKS_JSON = os.path.join(STATIC_DIR, "stocks.json")
GROUPS_JSON = os.path.join(STATIC_DIR, "groups.json")
# 選用設定，目前只有 worker_url（族群雲端儲存的網址）。沒有這個檔就是純靜態模式。
CONFIG_JSON = os.path.join(STATIC_DIR, "config.json")

BIG400 = db.BIG_THRESHOLDS[400]      # 分級 12~15
BIG1000 = db.BIG_THRESHOLDS[1000]    # 分級 15
SMALL10 = db.SMALL_THRESHOLDS[10]    # 分級 1~3

WEEKLY_COLUMNS = ["stock_no", "people", "lots", "b4_pct", "b4_people",
                  "b4_lots", "b1_pct", "b1_people", "s10_pct"]

# 合計人數低於這個數的多半是特別股、受益證券之類，留著只是讓頁面變肥
MIN_PEOPLE = 50


def _levels(levels):
    return ",".join(str(x) for x in levels)


WEEK_SQL = """
SELECT stock_no,
       SUM(CASE WHEN level=17 THEN people ELSE 0 END) AS people,
       SUM(CASE WHEN level=17 THEN shares ELSE 0 END) AS shares,
       SUM(CASE WHEN level IN ({b4}) THEN pct    ELSE 0 END) AS b4_pct,
       SUM(CASE WHEN level IN ({b4}) THEN people ELSE 0 END) AS b4_people,
       SUM(CASE WHEN level IN ({b4}) THEN shares ELSE 0 END) AS b4_shares,
       SUM(CASE WHEN level IN ({b1}) THEN pct    ELSE 0 END) AS b1_pct,
       SUM(CASE WHEN level IN ({b1}) THEN people ELSE 0 END) AS b1_people,
       SUM(CASE WHEN level IN ({s10}) THEN pct   ELSE 0 END) AS s10_pct
FROM holdings WHERE date=? GROUP BY stock_no ORDER BY stock_no
""".format(b4=_levels(BIG400), b1=_levels(BIG1000), s10=_levels(SMALL10))


def week_rows_from_db(conn, date):
    """把資料庫裡某一週壓成 WEEKLY_COLUMNS 的列。"""
    out = []
    for r in conn.execute(WEEK_SQL, (date,)):
        people = r["people"] or 0
        if people < MIN_PEOPLE:
            continue
        out.append([
            r["stock_no"], people, round((r["shares"] or 0) / 1000),
            round(r["b4_pct"] or 0, 2), r["b4_people"] or 0,
            round((r["b4_shares"] or 0) / 1000),
            round(r["b1_pct"] or 0, 2), r["b1_people"] or 0,
            round(r["s10_pct"] or 0, 2),
        ])
    return out


def week_rows_from_holdings(rows):
    """把 collector 抓回來的原始 (date, stock, level, people, shares, pct)
    直接壓成同樣格式 —— 排程環境沒有資料庫時走這條。"""
    b4, b1, s10 = set(BIG400), set(BIG1000), set(SMALL10)
    acc = {}
    for _date, stock, level, people, shares, pct in rows:
        a = acc.setdefault(stock, [0, 0, 0.0, 0, 0, 0.0, 0, 0.0])
        if level == db.LEVEL_TOTAL:
            a[0] = people
            a[1] = shares
        if level in b4:
            a[2] += pct
            a[3] += people
            a[4] += shares
        if level in b1:
            a[5] += pct
            a[6] += people
        if level in s10:
            a[7] += pct
    out = []
    for stock in sorted(acc):
        people, shares, b4p, b4pe, b4s, b1p, b1pe, s10p = acc[stock]
        if people < MIN_PEOPLE:
            continue
        out.append([stock, people, round(shares / 1000), round(b4p, 2), b4pe,
                    round(b4s / 1000), round(b1p, 2), b1pe, round(s10p, 2)])
    return out


def write_week_csv(date, rows, folder=WEEKLY_DIR):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "%s.csv" % date)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(WEEKLY_COLUMNS)
        w.writerows(rows)
    return path


def read_week_csv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        r = csv.reader(fh)
        next(r, None)
        return [[c[0]] + [_num(x) for x in c[1:]] for c in r if c]


def _num(x):
    if x in ("", None):
        return 0
    f = float(x)
    return int(f) if f == int(f) else f


def available_weeks(folder=WEEKLY_DIR):
    if not os.path.isdir(folder):
        return []
    out = []
    for f in sorted(os.listdir(folder)):
        m = re.match(r"^(\d{8})\.csv$", f)
        if m:
            out.append((m.group(1), os.path.join(folder, f)))
    return out


# --------------------------------------------------------------------------
# 從資料庫匯出（本機用）
# --------------------------------------------------------------------------

def export_weeks(conn, folder=WEEKLY_DIR, overwrite=False):
    have = {d for d, _p in available_weeks(folder)}
    written = []
    for date in reversed(db.available_dates(conn)):
        if date in have and not overwrite:
            continue
        write_week_csv(date, week_rows_from_db(conn, date), folder)
        written.append(date)
    return written


def export_stocks(conn, path=STOCKS_JSON, weekly_dir=WEEKLY_DIR):
    """個股基本資料：名稱、市場、產業、細產業、市值、成交量。

    ISIN 那份有四萬多筆（含權證、債券），只留週資料裡真的出現過的代號，
    不然 repo 要多背兩三 MB 用不到的東西。
    """
    keep = {r[0] for _d, p in available_weeks(weekly_dir) for r in read_week_csv(p)}
    meta = {r["stock_no"]: r for r in conn.execute(
        "SELECT stock_no, close, volume, market_cap, rank, sub_industry "
        "FROM stock_meta")}
    out = {}
    for r in conn.execute("SELECT stock_no, name, market, industry FROM stocks"):
        if keep and r["stock_no"] not in keep:
            continue
        m = meta.get(r["stock_no"])
        if not r["name"] and not m:
            continue
        o = {"n": r["name"] or "", "m": r["market"] or "",
             "i": r["industry"] or ""}
        if m:
            if m["sub_industry"]:
                o["s"] = m["sub_industry"]
            if m["market_cap"]:
                o["c"] = round(m["market_cap"] / 1e8, 1)   # 億元
            if m["volume"]:
                o["v"] = m["volume"]                        # 張
            if m["close"]:
                o["p"] = m["close"]
        out[r["stock_no"]] = o
    _dump(path, out)
    return len(out)


def export_groups(conn, path=GROUPS_JSON):
    out = {}
    for r in conn.execute(
            "SELECT group_name, stock_no FROM groups ORDER BY group_name, stock_no"):
        out.setdefault(r["group_name"], []).append(r["stock_no"])
    _dump(path, out)
    return len(out)


def _dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"),
                  sort_keys=True)


def _load(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# 組出要塞進頁面的 payload
# --------------------------------------------------------------------------

def build_payload(weekly_dir=WEEKLY_DIR, stocks_json=STOCKS_JSON,
                  groups_json=GROUPS_JSON, weeks=None, generated_at=""):
    """欄導向的結構：codes 一份，每個指標一條與 codes 等長的陣列。

    比每檔一個物件省掉大量重複的 key 與括號，頁面解析也只是索引查表。
    """
    files = available_weeks(weekly_dir)
    if weeks:
        files = files[-int(weeks):]
    if not files:
        raise RuntimeError("static_data/weekly 底下沒有任何週資料")

    per_week = [(d, {r[0]: r for r in read_week_csv(p)}) for d, p in files]
    codes = sorted({c for _d, m in per_week for c in m})
    idx = {c: i for i, c in enumerate(codes)}

    keys = ["people", "lots", "b4_pct", "b4_people", "b4_lots",
            "b1_pct", "b1_people", "s10_pct"]
    series = []
    for date, m in per_week:
        cols = {k: [None] * len(codes) for k in keys}
        for code, row in m.items():
            i = idx[code]
            for k, v in zip(keys, row[1:]):
                cols[k][i] = v
        series.append({"date": date, **cols})

    stocks = _load(stocks_json)
    groups = _load(groups_json)
    config = _load(CONFIG_JSON)
    # 頁面用索引找個股資料，省掉一層字典查詢
    info = [stocks.get(c, {}) for c in codes]
    return {
        "generated_at": generated_at,
        "worker_url": (config.get("worker_url") or "").rstrip("/"),
        "codes": codes,
        "info": info,
        "series": series,
        "groups": {g: [idx[c] for c in ss if c in idx]
                   for g, ss in groups.items()},
        "big_lots": 400,
        "big1000_lots": 1000,
        "small_lots": 10,
    }
