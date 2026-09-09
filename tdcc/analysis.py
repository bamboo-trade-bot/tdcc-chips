"""集保籌碼指標計算。

所有指標都由 holdings 的 17 個持股分級即時彙總，因此大戶／散戶門檻可以隨時改，
不需要重算任何快取表。
"""
from . import db


def _levels_sql(levels):
    return ",".join(str(int(x)) for x in levels)


def resolve_levels(big_lots=400, small_lots=10):
    big = db.BIG_THRESHOLDS.get(int(big_lots))
    small = db.SMALL_THRESHOLDS.get(int(small_lots))
    if big is None:
        raise ValueError("大戶門檻只支援 %s 張" % sorted(db.BIG_THRESHOLDS))
    if small is None:
        raise ValueError("散戶門檻只支援 %s 張" % sorted(db.SMALL_THRESHOLDS))
    return big, small


def pick_dates(conn, date=None, prev=None, span=1):
    """決定本期 / 前期日期。span=N 表示跟 N 週前比。"""
    dates = db.available_dates(conn)
    if not dates:
        return None, None, []
    cur = date if date in dates else dates[0]
    if prev and prev in dates:
        return cur, prev, dates
    i = dates.index(cur)
    j = min(i + max(1, int(span)), len(dates) - 1)
    return cur, (dates[j] if j > i else None), dates


AGG_TEMPLATE = """
SELECT date, stock_no,
       SUM(CASE WHEN level=17 THEN shares ELSE 0 END) AS total_shares,
       SUM(CASE WHEN level=17 THEN people ELSE 0 END) AS total_people,
       SUM(CASE WHEN level IN ({big}) THEN shares ELSE 0 END) AS big_shares,
       SUM(CASE WHEN level IN ({big}) THEN people ELSE 0 END) AS big_people,
       SUM(CASE WHEN level IN ({big}) THEN pct    ELSE 0 END) AS big_pct,
       SUM(CASE WHEN level IN ({small}) THEN shares ELSE 0 END) AS small_shares,
       SUM(CASE WHEN level IN ({small}) THEN people ELSE 0 END) AS small_people,
       SUM(CASE WHEN level IN ({small}) THEN pct    ELSE 0 END) AS small_pct
FROM holdings
WHERE date IN (?, ?)
GROUP BY date, stock_no
"""


def _row_metrics(c, p):
    """把本期 / 前期兩筆彙總換算成一列指標。"""
    def f(row, key):
        return (row[key] or 0) if row else 0

    total_shares = f(c, "total_shares")
    total_people = f(c, "total_people")
    avg_lots = (total_shares / total_people / 1000) if total_people else 0.0

    p_total_shares = f(p, "total_shares")
    p_total_people = f(p, "total_people")
    p_avg_lots = (p_total_shares / p_total_people / 1000) if p_total_people else 0.0

    big_pct = f(c, "big_pct")
    small_pct = f(c, "small_pct")
    out = {
        "total_shares": total_shares,
        "total_people": total_people,
        "big_pct": round(big_pct, 2),
        "big_shares": f(c, "big_shares"),
        "big_people": f(c, "big_people"),
        "small_pct": round(small_pct, 2),
        "small_people": f(c, "small_people"),
        "avg_lots": round(avg_lots, 2),
        "has_prev": p is not None,
    }
    if p is None:
        out.update({"d_big_pct": None, "d_small_pct": None, "d_big_shares": None,
                    "d_big_people": None, "d_total_people": None,
                    "d_total_people_pct": None, "d_avg_lots": None,
                    "d_big_lots": None})
        return out

    d_people = total_people - p_total_people
    out.update({
        "d_big_pct": round(big_pct - f(p, "big_pct"), 2),
        "d_small_pct": round(small_pct - f(p, "small_pct"), 2),
        "d_big_shares": f(c, "big_shares") - f(p, "big_shares"),
        "d_big_lots": round((f(c, "big_shares") - f(p, "big_shares")) / 1000, 0),
        "d_big_people": f(c, "big_people") - f(p, "big_people"),
        "d_total_people": d_people,
        "d_total_people_pct": round(d_people / p_total_people * 100, 2)
        if p_total_people else None,
        "d_avg_lots": round(avg_lots - p_avg_lots, 2),
    })
    return out


SORT_KEYS = {
    "d_big_pct": "大戶持股比例增減",
    "d_small_pct": "散戶持股比例增減",
    "d_big_lots": "大戶增減張數",
    "d_total_people_pct": "股東人數增減%",
    "d_avg_lots": "平均每人持股增減",
    "big_pct": "大戶持股比例",
    "small_pct": "散戶持股比例",
    "avg_lots": "平均每人持股張數",
    "total_people": "股東人數",
    "total_shares": "集保總股數",
}


def ranking(conn, date=None, prev=None, span=1, big_lots=400, small_lots=10,
            market=None, group=None, exclude_etf=True, min_people=0,
            min_shares=0, sort="d_big_pct", order="desc", limit=100,
            query=None):
    """全市場（或指定族群 / 市場）籌碼變化排行。"""
    big, small = resolve_levels(big_lots, small_lots)
    cur, prv, dates = pick_dates(conn, date, prev, span)
    if cur is None:
        return {"date": None, "prev": None, "dates": [], "rows": [], "total": 0}

    sql = AGG_TEMPLATE.format(big=_levels_sql(big), small=_levels_sql(small))
    agg = {}
    for r in conn.execute(sql, (cur, prv or cur)):
        agg.setdefault(r["date"], {})[r["stock_no"]] = r

    cur_rows = agg.get(cur, {})
    prev_rows = agg.get(prv, {}) if prv and prv != cur else {}

    meta = {r["stock_no"]: r for r in conn.execute(
        "SELECT stock_no,name,market,industry FROM stocks")}
    gmap = {}
    for r in conn.execute("SELECT group_name, stock_no FROM groups"):
        gmap.setdefault(r["stock_no"], []).append(r["group_name"])

    group_filter = None
    if group:
        group_filter = {r[0] for r in conn.execute(
            "SELECT stock_no FROM groups WHERE group_name=?", (group,))}

    q = (query or "").strip().upper()
    rows = []
    for stock_no, c in cur_rows.items():
        if group_filter is not None and stock_no not in group_filter:
            continue
        m = meta.get(stock_no)
        name = m["name"] if m else ""
        mk = m["market"] if m else ""
        if market and mk != market:
            continue
        if exclude_etf and _is_etf(stock_no, name):
            continue
        if q and q not in stock_no.upper() and q not in name.upper():
            continue
        if (c["total_people"] or 0) < min_people:
            continue
        if (c["total_shares"] or 0) < min_shares:
            continue
        row = {"stock_no": stock_no, "name": name, "market": mk,
               "industry": (m["industry"] if m else ""),
               "groups": gmap.get(stock_no, [])}
        row.update(_row_metrics(c, prev_rows.get(stock_no)))
        rows.append(row)

    key = sort if sort in SORT_KEYS else "d_big_pct"
    # 沒有前期可比的股票（值是 None）一律排在最後面，不管升冪還是降冪
    have = [r for r in rows if r.get(key) is not None]
    lack = [r for r in rows if r.get(key) is None]
    have.sort(key=lambda r: r[key], reverse=(order != "asc"))
    rows = have + lack
    total = len(rows)
    if limit:
        rows = rows[:int(limit)]
    return {"date": cur, "prev": prv, "dates": dates, "rows": rows,
            "total": total, "sort": key, "order": order,
            "big_lots": big_lots, "small_lots": small_lots}


def _is_etf(stock_no, name=""):
    """ETF / ETN / 受益證券：代號 00 開頭或 6 碼純數字。"""
    if stock_no.startswith("00"):
        return True
    if len(stock_no) == 6 and stock_no.isdigit():
        return True
    return False


def group_summary(conn, date=None, prev=None, span=1, big_lots=400,
                  small_lots=10, exclude_etf=False):
    """每個族群一列：族群整體大戶籌碼與週變化。"""
    big, small = resolve_levels(big_lots, small_lots)
    cur, prv, dates = pick_dates(conn, date, prev, span)
    if cur is None:
        return {"date": None, "prev": None, "dates": [], "groups": []}

    sql = AGG_TEMPLATE.format(big=_levels_sql(big), small=_levels_sql(small))
    agg = {}
    for r in conn.execute(sql, (cur, prv or cur)):
        agg.setdefault(r["date"], {})[r["stock_no"]] = r
    cur_rows = agg.get(cur, {})
    prev_rows = agg.get(prv, {}) if prv and prv != cur else {}

    meta = {r["stock_no"]: r for r in conn.execute(
        "SELECT stock_no,name,market FROM stocks")}

    members = {}
    for r in conn.execute("SELECT group_name, stock_no FROM groups "
                          "ORDER BY group_name, stock_no"):
        members.setdefault(r["group_name"], []).append(r["stock_no"])

    out = []
    for gname, stocks in members.items():
        detail, missing = [], []
        for sn in stocks:
            c = cur_rows.get(sn)
            if c is None:
                missing.append(sn)
                continue
            m = meta.get(sn)
            name = m["name"] if m else ""
            if exclude_etf and _is_etf(sn, name):
                continue
            row = {"stock_no": sn, "name": name,
                   "market": (m["market"] if m else "")}
            row.update(_row_metrics(c, prev_rows.get(sn)))
            detail.append(row)
        out.append(_aggregate_group(gname, detail, missing))

    out.sort(key=lambda g: (g["d_big_pct_avg"] is None, g["d_big_pct_avg"] or 0),
             reverse=True)
    return {"date": cur, "prev": prv, "dates": dates, "groups": out,
            "big_lots": big_lots, "small_lots": small_lots}


def _aggregate_group(gname, detail, missing):
    n = len(detail)
    with_prev = [d for d in detail if d["d_big_pct"] is not None]
    tot_shares = sum(d["total_shares"] for d in detail)
    tot_big = sum(d["big_shares"] for d in detail)
    tot_people = sum(d["total_people"] for d in detail)

    def avg(key, src):
        vals = [d[key] for d in src if d.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    up = sum(1 for d in with_prev if d["d_big_pct"] > 0)
    down = sum(1 for d in with_prev if d["d_big_pct"] < 0)
    return {
        "group": gname,
        "count": n,
        "missing": missing,
        "big_pct_avg": avg("big_pct", detail),
        "big_pct_weighted": round(tot_big / tot_shares * 100, 2) if tot_shares else None,
        "small_pct_avg": avg("small_pct", detail),
        "avg_lots": avg("avg_lots", detail),
        "total_people": tot_people,
        "d_big_pct_avg": avg("d_big_pct", with_prev),
        "d_small_pct_avg": avg("d_small_pct", with_prev),
        "d_big_lots_sum": sum(d["d_big_lots"] or 0 for d in with_prev) or None,
        "d_total_people": sum(d["d_total_people"] or 0 for d in with_prev) or None,
        "d_total_people_pct": avg("d_total_people_pct", with_prev),
        "up": up, "down": down, "flat": len(with_prev) - up - down,
        "breadth": round((up - down) / len(with_prev) * 100, 1) if with_prev else None,
        "detail": detail,
    }


def group_detail(conn, group, **kw):
    """單一族群的成分股明細（沿用 ranking，帶 group 過濾）。"""
    kw.setdefault("limit", 0)
    kw.setdefault("exclude_etf", False)
    kw.setdefault("sort", "d_big_pct")
    return ranking(conn, group=group, **kw)


def group_trend(conn, group, big_lots=400, small_lots=10, weeks=52):
    """族群大戶比例的逐週走勢（等權平均）。"""
    big, small = resolve_levels(big_lots, small_lots)
    stocks = [r[0] for r in conn.execute(
        "SELECT stock_no FROM groups WHERE group_name=?", (group,))]
    if not stocks:
        return {"group": group, "points": [], "stocks": []}
    marks = ",".join("?" * len(stocks))
    sql = """
        SELECT date, stock_no,
               SUM(CASE WHEN level IN ({big}) THEN pct ELSE 0 END)   AS big_pct,
               SUM(CASE WHEN level IN ({small}) THEN pct ELSE 0 END) AS small_pct,
               SUM(CASE WHEN level=17 THEN people ELSE 0 END)        AS total_people,
               SUM(CASE WHEN level=17 THEN shares ELSE 0 END)        AS total_shares
        FROM holdings WHERE stock_no IN ({marks})
        GROUP BY date, stock_no ORDER BY date
    """.format(big=_levels_sql(big), small=_levels_sql(small), marks=marks)

    per_date = {}
    for r in conn.execute(sql, stocks):
        per_date.setdefault(r["date"], []).append(r)

    points = []
    for d in sorted(per_date)[-weeks:]:
        rs = per_date[d]
        tot_shares = sum(r["total_shares"] or 0 for r in rs)
        big_w = sum((r["big_pct"] or 0) * (r["total_shares"] or 0) for r in rs)
        points.append({
            "date": d,
            "count": len(rs),
            "big_pct_avg": round(sum(r["big_pct"] or 0 for r in rs) / len(rs), 2),
            "big_pct_weighted": round(big_w / tot_shares, 2) if tot_shares else None,
            "small_pct_avg": round(sum(r["small_pct"] or 0 for r in rs) / len(rs), 2),
            "total_people": sum(r["total_people"] or 0 for r in rs),
        })
    return {"group": group, "points": points, "stocks": stocks,
            "big_lots": big_lots, "small_lots": small_lots}


def stock_history(conn, stock_no, big_lots=400, small_lots=10, weeks=60):
    """單檔個股：逐週大戶／散戶比例 + 最新一週的完整 15 級分布。"""
    big, small = resolve_levels(big_lots, small_lots)
    sql = """
        SELECT date,
               SUM(CASE WHEN level IN ({big}) THEN pct ELSE 0 END)    AS big_pct,
               SUM(CASE WHEN level IN ({big}) THEN shares ELSE 0 END) AS big_shares,
               SUM(CASE WHEN level IN ({small}) THEN pct ELSE 0 END)  AS small_pct,
               SUM(CASE WHEN level=17 THEN people ELSE 0 END)         AS total_people,
               SUM(CASE WHEN level=17 THEN shares ELSE 0 END)         AS total_shares
        FROM holdings WHERE stock_no=? GROUP BY date ORDER BY date
    """.format(big=_levels_sql(big), small=_levels_sql(small))
    points = []
    for r in conn.execute(sql, (stock_no,)):
        tp = r["total_people"] or 0
        points.append({
            "date": r["date"],
            "big_pct": round(r["big_pct"] or 0, 2),
            "big_lots": round((r["big_shares"] or 0) / 1000),
            "small_pct": round(r["small_pct"] or 0, 2),
            "total_people": tp,
            "avg_lots": round((r["total_shares"] or 0) / tp / 1000, 2) if tp else None,
        })
    points = points[-weeks:]

    dist = []
    if points:
        last = points[-1]["date"]
        for r in conn.execute(
                "SELECT level,people,shares,pct FROM holdings "
                "WHERE stock_no=? AND date=? ORDER BY level", (stock_no, last)):
            label = db.LEVELS.get(r["level"], (None, None, {
                16: "差異數調整", 17: "合計"}.get(r["level"], str(r["level"]))))[2]
            dist.append({"level": r["level"], "label": label,
                         "people": r["people"], "shares": r["shares"],
                         "pct": r["pct"]})

    m = conn.execute("SELECT name,market,industry FROM stocks WHERE stock_no=?",
                     (stock_no,)).fetchone()
    gs = [r[0] for r in conn.execute(
        "SELECT group_name FROM groups WHERE stock_no=? ORDER BY group_name",
        (stock_no,))]
    return {"stock_no": stock_no,
            "name": m["name"] if m else "",
            "market": m["market"] if m else "",
            "industry": m["industry"] if m else "",
            "groups": gs, "points": points, "distribution": dist,
            "big_lots": big_lots, "small_lots": small_lots}


def overview(conn):
    """首頁小卡：資料涵蓋範圍。"""
    dates = db.available_dates(conn)
    n_stocks = conn.execute(
        "SELECT COUNT(DISTINCT stock_no) FROM holdings").fetchone()[0]
    n_groups = conn.execute(
        "SELECT COUNT(DISTINCT group_name) FROM groups").fetchone()[0]
    n_named = conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
    logs = [dict(r) for r in conn.execute(
        "SELECT ts,kind,source,detail FROM import_log ORDER BY id DESC LIMIT 15")]
    return {"dates": dates, "weeks": len(dates), "stocks": n_stocks,
            "groups": n_groups, "named": n_named, "logs": logs,
            "sort_keys": [{"key": k, "label": v} for k, v in SORT_KEYS.items()],
            "big_options": sorted(db.BIG_THRESHOLDS),
            "small_options": sorted(db.SMALL_THRESHOLDS)}
