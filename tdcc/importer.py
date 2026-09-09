"""把手上的 Excel / CSV 匯進資料庫。

支援兩類檔案，格式都盡量自動辨識：

data/tdcc_weekly/  集保每週股權分散表
    * 長格式（跟 opendata 一樣）：資料日期 / 證券代號 / 持股分級 / 人數 / 股數 / 比例
    * 沒有日期欄時，會從檔名抓 8 碼日期（例如 20260904.xlsx）

data/groups/       自訂族群分類
    * 長格式：族群 | 股號 |（名稱）
    * 寬格式：每個欄位標題是族群名稱，欄位底下整排是股號
"""
import os
import re

import pandas as pd

from . import collector, db

WEEKLY_DIR = os.path.join(db.DATA_DIR, "tdcc_weekly")
GROUPS_DIR = os.path.join(db.DATA_DIR, "groups")
SUFFIXES = (".xlsx", ".xlsm", ".xls", ".csv")

DATE_COLS = ("資料日期", "日期", "date", "資料年月日", "年月日")
STOCK_COLS = ("證券代號", "股票代號", "股號", "代號", "代碼", "證券代碼", "股票代碼",
              "stock", "stock_no", "code", "商品代號")
# 以分頁名稱當族群時，這些分頁是「全市場清單」不是族群，跳過
SKIP_SHEETS = ("全部", "總表", "全市場", "universe", "all")
LEVEL_COLS = ("持股分級", "分級", "序", "持股/單位數分級", "級距", "level")
PEOPLE_COLS = ("人數", "人數(人)", "股東人數", "people")
SHARE_COLS = ("股數", "股數/單位數", "持有股數", "shares")
PCT_COLS = ("占集保庫存數比例", "占集保庫存數比例%", "比例", "占比", "pct",
            "占集保庫存數比例 (%)")
# 族群欄一律「精確」比對：否則像「細產業」這種標籤欄會被誤認成族群欄
GROUP_COLS = ("族群", "類別", "分類", "產業", "產業別", "概念股", "群組", "主題",
              "板塊", "group", "category", "sector", "theme")
NAME_COLS = ("名稱", "股票名稱", "證券名稱", "商品名稱", "name")


def _norm(s):
    return re.sub(r"[\s　\(\)（）%]", "", str(s)).strip().lower()


def _find_col(cols, candidates, strict=False):
    """在欄位標題中找出符合的欄。strict=True 只接受完全相同，不做子字串比對。"""
    normed = {_norm(c): c for c in cols}
    for cand in candidates:
        if _norm(cand) in normed:
            return normed[_norm(cand)]
    if strict:
        return None
    for cand in candidates:
        n = _norm(cand)
        for k, orig in normed.items():
            if n and n in k:
                return orig
    return None


def _read_any(path, sheet=0):
    if path.lower().endswith(".csv"):
        for enc in ("utf-8-sig", "cp950", "utf-8"):
            try:
                return {"csv": pd.read_csv(path, encoding=enc, dtype=str)}
            except UnicodeDecodeError:
                continue
        return {"csv": pd.read_csv(path, encoding="utf-8", errors="replace",
                                   dtype=str)}
    return pd.read_excel(path, sheet_name=None, dtype=str)


def _clean_code(x):
    if x is None:
        return ""
    s = str(x).strip().upper()
    if s in ("", "NAN", "NONE"):
        return ""
    s = re.sub(r"\.0$", "", s)          # Excel 把 2330 讀成 2330.0
    m = re.match(r"^([0-9A-Z]{4,6})", s)
    return m.group(1) if m else ""


def _clean_date(x, fallback=None):
    s = re.sub(r"\D", "", str(x or ""))
    if len(s) >= 8:
        return s[:8]
    return fallback


def _date_from_name(path):
    m = re.search(r"(20\d{6})", os.path.basename(path))
    return m.group(1) if m else None


# 分級下限股數 -> level，用來把文字級距（例如「1,000-5,000」）對回代號
_LOWER_TO_LEVEL = {lo: lv for lv, (lo, _hi, _label) in db.LEVELS.items()}


def _level_from(value):
    """分級欄可能是代號(1~17)，也可能是文字級距或「合　計」。回傳 level 或 None。"""
    s = str(value or "").replace("　", "").strip()
    if not s:
        return None
    if "合計" in s:
        return db.LEVEL_TOTAL
    if "差異" in s:
        return db.LEVEL_ADJUST
    if s.isdigit() and 1 <= int(s) <= 17:
        return int(s)
    nums = re.findall(r"\d[\d,]*", s)
    if nums:
        lo = int(nums[0].replace(",", ""))
        if lo in _LOWER_TO_LEVEL:
            return _LOWER_TO_LEVEL[lo]
        for lv, (l, h, _lbl) in db.LEVELS.items():   # 1 開頭的 1-999 那種
            if l <= lo and (h is None or lo <= h):
                return lv
    return None


# --------------------------------------------------------------------------
# 集保每週資料
# --------------------------------------------------------------------------

def scan_weekly_files(folder=WEEKLY_DIR):
    if not os.path.isdir(folder):
        return []
    return sorted(os.path.join(folder, f) for f in os.listdir(folder)
                  if f.lower().endswith(SUFFIXES) and not f.startswith("~$"))


def import_weekly_file(conn, path):
    """匯入一個集保週檔。回傳 {file, dates, rows, sheets, skipped}。"""
    fallback = _date_from_name(path)
    sheets = _read_any(path)
    rows, dates, notes = [], set(), []
    for sheet_name, dfr in sheets.items():
        df = _promote_header(dfr, LEVEL_COLS + STOCK_COLS)
        c_stock = _find_col(df.columns, STOCK_COLS)
        c_level = _find_col(df.columns, LEVEL_COLS)
        c_people = _find_col(df.columns, PEOPLE_COLS)
        c_shares = _find_col(df.columns, SHARE_COLS)
        c_pct = _find_col(df.columns, PCT_COLS)
        c_date = _find_col(df.columns, DATE_COLS)
        if not (c_stock and c_level and c_shares):
            notes.append("%s：欄位辨識失敗(%s)" % (sheet_name, list(df.columns)[:8]))
            continue
        # 全市場週檔一支就有 6~7 萬列，iterrows 太慢，直接拿欄位陣列並行走訪
        n = len(df)
        blank = [None] * n
        col_stock = df[c_stock].tolist()
        col_level = df[c_level].tolist()
        col_shares = df[c_shares].tolist()
        col_people = df[c_people].tolist() if c_people else blank
        col_pct = df[c_pct].tolist() if c_pct else blank
        col_date = df[c_date].tolist() if c_date else blank
        for stock, level, shares, people, pct, dv in zip(
                col_stock, col_level, col_shares, col_people, col_pct, col_date):
            code = _clean_code(stock)
            lvl = _level_from(level)
            if not code or not lvl:
                continue
            d = _clean_date(dv, fallback)
            if not d:
                continue
            dates.add(d)
            rows.append((d, code, lvl, collector._int(people),
                         collector._int(shares), collector._float(pct)))
    n = collector.upsert_holdings(conn, rows) if rows else 0
    db.log_import(conn, "excel-weekly", os.path.basename(path),
                  "%d 筆 / 日期 %s %s" % (n, sorted(dates), "; ".join(notes)))
    conn.commit()
    return {"file": os.path.basename(path), "rows": n,
            "dates": sorted(dates), "notes": notes}


def _header_score(values, want):
    """這一列像表頭的程度：命中幾個不同的候選欄名。"""
    normed = {_norm(v) for v in values}
    return sum(1 for cand in want if _norm(cand) in normed)


def _promote_header(df, want):
    """有些檔案前幾列是標題／說明，真正的表頭在下面。找出來並提升成 columns。

    取「命中最多候選欄名」的那一列，不是第一列有命中就停 —— 說明列可能剛好夾帶
    一個像欄名的字（例如「策略 / 市值」），只看有沒有命中會停在錯的列。
    """
    if _find_col(df.columns, want):
        return df
    best_i, best_score = None, 0
    for i in range(min(8, len(df))):
        score = _header_score(df.iloc[i].tolist(), want)
        if score > best_score:
            best_i, best_score = i, score
    if best_i is None:
        return df
    out = df.iloc[best_i + 1:].copy()
    out.columns = [str(v) for v in df.iloc[best_i].tolist()]
    return out.reset_index(drop=True)


def import_weekly_folder(conn, folder=WEEKLY_DIR):
    results = [import_weekly_file(conn, p) for p in scan_weekly_files(folder)]
    return {"files": results,
            "total_rows": sum(r["rows"] for r in results),
            "dates": sorted({d for r in results for d in r["dates"]})}


# --------------------------------------------------------------------------
# 族群分類
# --------------------------------------------------------------------------

def scan_group_files(folder=GROUPS_DIR):
    if not os.path.isdir(folder):
        return []
    return sorted(os.path.join(folder, f) for f in os.listdir(folder)
                  if f.lower().endswith(SUFFIXES) and not f.startswith("~$"))


def parse_group_file(path):
    """回傳 [(族群, 股號), ...]。三種格式都試：

    1. 長格式   —— 同一張表裡有「族群」和「股號」兩欄
    2. 分頁格式 —— 分頁名稱就是族群，表內有股號欄（例如選股軟體匯出的分產業活頁簿）
    3. 寬格式   —— 每個欄位標題是族群名稱，欄位底下整排是股號
    """
    sheets = _read_any(path)
    pairs, notes = [], []
    for sheet_name, dfr in sheets.items():
        df = dfr.dropna(how="all").dropna(axis=1, how="all")
        if df.empty:
            continue
        df = _promote_header(df, GROUP_COLS + STOCK_COLS)
        c_group = _find_col(df.columns, GROUP_COLS, strict=True)
        c_stock = _find_col(df.columns, STOCK_COLS)

        if c_group and c_stock:                        # 1. 長格式
            got = 0
            for _, r in df.iterrows():
                g = str(r.get(c_group) or "").strip()
                code = _clean_code(r.get(c_stock))
                if g and g.lower() != "nan" and code:
                    pairs.append((g, code))
                    got += 1
            notes.append("%s：長格式 %d 筆" % (sheet_name, got))
            continue

        if c_stock:                                    # 2. 分頁名稱即族群
            gname = str(sheet_name).strip()
            if any(k in gname.lower() for k in SKIP_SHEETS):
                notes.append("%s：看起來是全市場清單，跳過" % sheet_name)
                continue
            codes = {c for c in (_clean_code(v) for v in df[c_stock].tolist()) if c}
            pairs.extend((gname, c) for c in sorted(codes))
            notes.append("%s：分頁格式 %d 筆" % (sheet_name, len(codes)))
            continue

        got = 0                                        # 3. 寬格式
        for col in df.columns:
            gname = str(col).strip()
            if not gname or gname.lower().startswith("unnamed"):
                continue
            for v in df[col].tolist():
                code = _clean_code(v)
                if code:
                    pairs.append((gname, code))
                    got += 1
        if got:
            notes.append("%s：寬格式 %d 筆" % (sheet_name, got))
        else:
            notes.append("%s：無法辨識" % sheet_name)
    return pairs, notes


def import_group_file(conn, path, replace=True):
    pairs, notes = parse_group_file(path)
    src = os.path.basename(path)
    if replace:
        conn.execute("DELETE FROM groups WHERE source=?", (src,))
    uniq = sorted(set(pairs))
    conn.executemany(
        "INSERT INTO groups(group_name,stock_no,source) VALUES(?,?,?) "
        "ON CONFLICT(group_name,stock_no) DO UPDATE SET source=excluded.source",
        [(g, s, src) for g, s in uniq])
    db.log_import(conn, "excel-groups", src,
                  "%d 組合 / %d 族群 %s" % (
                      len(uniq), len({g for g, _ in uniq}), "; ".join(notes)))
    conn.commit()
    return {"file": src, "pairs": len(uniq),
            "groups": sorted({g for g, _ in uniq}), "notes": notes}


# --------------------------------------------------------------------------
# 個股 metadata（市值 / 成交量 / 細產業）—— 選股策略要用
# --------------------------------------------------------------------------

CAP_COLS = ("總市值(元)", "總市值", "市值", "market_cap")
VOL_COLS = ("成交量", "總量", "volume")
CLOSE_COLS = ("成交", "收盤", "收盤價", "close")
RANK_COLS = ("排行名次", "名次", "rank")
SUBIND_COLS = ("細產業", "子產業", "產業標籤", "sub_industry")


def parse_stock_meta(path):
    """從族群 Excel 的任何分頁撈個股 metadata。

    選股軟體匯出的表通常每張分頁都帶著市值／成交量／細產業，所以不挑分頁，
    掃過全部再合併（含被當成全市場清單跳過的那張，那張反而最完整）。
    """
    sheets = _read_any(path)
    meta, notes = {}, []
    for sheet_name, dfr in sheets.items():
        df = dfr.dropna(how="all").dropna(axis=1, how="all")
        if df.empty:
            continue
        df = _promote_header(df, STOCK_COLS + CAP_COLS + SUBIND_COLS)
        c_stock = _find_col(df.columns, STOCK_COLS)
        c_cap = _find_col(df.columns, CAP_COLS, strict=True)
        c_vol = _find_col(df.columns, VOL_COLS, strict=True)
        c_close = _find_col(df.columns, CLOSE_COLS, strict=True)
        c_rank = _find_col(df.columns, RANK_COLS, strict=True)
        c_sub = _find_col(df.columns, SUBIND_COLS, strict=True)
        if not c_stock or not any((c_cap, c_vol, c_sub)):
            continue

        n = len(df)
        blank = [None] * n
        cols = {k: (df[c].tolist() if c else blank) for k, c in (
            ("cap", c_cap), ("vol", c_vol), ("close", c_close),
            ("rank", c_rank), ("sub", c_sub))}
        got = 0
        for i, raw in enumerate(df[c_stock].tolist()):
            code = _clean_code(raw)
            if not code:
                continue
            sub = str(cols["sub"][i] or "").strip()
            row = {
                "close": collector._float(cols["close"][i]) or None,
                "volume": collector._int(cols["vol"][i]) or None,
                "market_cap": collector._float(cols["cap"][i]) or None,
                "rank": collector._int(cols["rank"][i]) or None,
                "sub_industry": sub if sub.lower() != "nan" else "",
            }
            # 欄位比較齊的那筆優先（「全部產業」通常最完整）
            old = meta.get(code)
            if old is None or _meta_score(row) > _meta_score(old):
                meta[code] = row
            got += 1
        if got:
            notes.append("%s：%d 筆" % (sheet_name, got))
    return meta, notes


def _meta_score(row):
    return sum(1 for v in row.values() if v)


def import_stock_meta_file(conn, path):
    meta, notes = parse_stock_meta(path)
    src = os.path.basename(path)
    conn.executemany(
        "INSERT INTO stock_meta(stock_no,close,volume,market_cap,rank,"
        "sub_industry,source,updated_at) "
        "VALUES(?,?,?,?,?,?,?,datetime('now','localtime')) "
        "ON CONFLICT(stock_no) DO UPDATE SET close=excluded.close, "
        "volume=excluded.volume, market_cap=excluded.market_cap, "
        "rank=excluded.rank, sub_industry=excluded.sub_industry, "
        "source=excluded.source, updated_at=excluded.updated_at",
        [(c, m["close"], m["volume"], m["market_cap"], m["rank"],
          m["sub_industry"], src) for c, m in sorted(meta.items())])
    db.log_import(conn, "excel-meta", src,
                  "%d 檔 metadata %s" % (len(meta), "; ".join(notes)))
    conn.commit()
    return {"file": src, "stocks": len(meta), "notes": notes}


def import_stock_meta_folder(conn, folder=GROUPS_DIR):
    results = [import_stock_meta_file(conn, p) for p in scan_group_files(folder)]
    return {"files": results, "total": sum(r["stocks"] for r in results)}


def import_groups_folder(conn, folder=GROUPS_DIR, replace=True):
    results = [import_group_file(conn, p, replace) for p in scan_group_files(folder)]
    return {"files": results,
            "total_pairs": sum(r["pairs"] for r in results),
            "groups": sorted({g for r in results for g in r["groups"]})}
