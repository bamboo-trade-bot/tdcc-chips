"""從 TDCC / TWSE 抓資料進 SQLite。

三個來源：
  1. opendata 1-5  : 全市場最新一週股權分散表（一次一個檔案，約 2.3MB CSV）
  2. qryStock 表單 : 單一個股、可回溯約 51 週（逐檔逐週爬，用來補歷史）
  3. TWSE ISIN     : 證券代號 -> 名稱 / 市場別 / 產業別
"""
import csv
import io
import os
import re
import time

import requests

from . import db

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
OPENDATA_URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"
QRY_URL = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"
ISIN_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"

RAW_DIR = os.path.join(db.DATA_DIR, "raw")


def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    return s


# --------------------------------------------------------------------------
# 1. opendata：全市場最新一週
# --------------------------------------------------------------------------

def fetch_opendata(conn, save_raw=True, timeout=120):
    """抓全市場最新一週快照，寫入 holdings。回傳 (date, 匯入筆數)。"""
    s = _session()
    resp = s.get(OPENDATA_URL, timeout=timeout)
    resp.raise_for_status()
    text = resp.content.decode("utf-8-sig", errors="replace")

    rows, date = _parse_opendata_csv(text)
    if not rows:
        raise RuntimeError("opendata 回傳沒有可用資料")

    if save_raw and date:
        os.makedirs(RAW_DIR, exist_ok=True)
        with open(os.path.join(RAW_DIR, "tdcc_%s.csv" % date), "w",
                  encoding="utf-8", newline="") as fh:
            fh.write(text)

    n = upsert_holdings(conn, rows)
    db.log_import(conn, "opendata", OPENDATA_URL, "%s 共 %d 筆" % (date, n))
    conn.commit()
    return date, n


def _parse_opendata_csv(text):
    reader = csv.reader(io.StringIO(text))
    next(reader, None)  # header
    rows, date = [], None
    for r in reader:
        if len(r) < 6:
            continue
        d, stock, lvl, people, shares, pct = (x.strip() for x in r[:6])
        if not d.isdigit() or not lvl.isdigit():
            continue
        date = date or d
        rows.append((d, stock, int(lvl), _int(people), _int(shares), _float(pct)))
    return rows, date


def _int(x):
    x = str(x).replace(",", "").strip()
    if x in ("", "-"):
        return 0
    try:
        return int(float(x))
    except ValueError:
        return 0


def _float(x):
    x = str(x).replace(",", "").replace("%", "").strip()
    if x in ("", "-"):
        return 0.0
    try:
        return float(x)
    except ValueError:
        return 0.0


def upsert_holdings(conn, rows):
    """rows: iterable of (date, stock_no, level, people, shares, pct)"""
    rows = list(rows)
    conn.executemany(
        "INSERT INTO holdings(date,stock_no,level,people,shares,pct) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(date,stock_no,level) DO UPDATE SET "
        "people=excluded.people, shares=excluded.shares, pct=excluded.pct",
        rows)
    return len(rows)


# --------------------------------------------------------------------------
# 2. qryStock：單一個股歷史（可回溯約 51 週）
# --------------------------------------------------------------------------

class QryStockScraper:
    """維持一個 session，重複查詢多檔多週。"""

    def __init__(self, delay=0.8):
        self.s = _session()
        self.delay = delay
        self.token = None
        self.dates = []
        self.fir_date = None

    def refresh(self):
        """取得 SYNCHRONIZER_TOKEN 與可查詢日期清單。"""
        html = self.s.get(QRY_URL, timeout=60).text
        m = re.search(r'name="SYNCHRONIZER_TOKEN"\s+value="([^"]+)"', html)
        if not m:
            raise RuntimeError("找不到 SYNCHRONIZER_TOKEN，網站結構可能改了")
        self.token = m.group(1)
        self.dates = re.findall(r'<option value="(\d{8})"', html)
        m2 = re.search(r'name="firDate"\s+value="(\d{8})"', html)
        self.fir_date = m2.group(1) if m2 else (self.dates[0] if self.dates else "")
        return self.dates

    def available_dates(self):
        if not self.dates:
            self.refresh()
        return self.dates

    def fetch(self, stock_no, date, _retry=True):
        """回傳 [(date, stock_no, level, people, shares, pct), ...]，查無資料回 []。

        SYNCHRONIZER_TOKEN 是一次性的：用過就失效，再用會被當成「查無此資料」。
        每次回應裡都夾帶下一個新 token，抓下來給下一筆用就不必每次重新 GET 首頁。
        """
        if not self.token:
            self.refresh()
        payload = {
            "SYNCHRONIZER_TOKEN": self.token,
            "SYNCHRONIZER_URI": "/portal/zh/smWeb/qryStock",
            "method": "submit",
            "firDate": self.fir_date,
            "scaDate": date,
            "sqlMethod": "StockNo",
            "stockNo": str(stock_no).strip().upper(),
            "stockName": "",
        }
        r = self.s.post(QRY_URL, data=payload, timeout=60,
                        headers={"Referer": QRY_URL})
        r.raise_for_status()
        self.token = None                      # 這顆已經用掉了
        time.sleep(self.delay)

        m = re.search(r'name="SYNCHRONIZER_TOKEN"\s+value="([^"]+)"', r.text)
        if m:
            self.token = m.group(1)

        rows = _parse_qry_html(r.text, stock_no, date)
        if not rows and _retry:
            # 分不出是真的沒資料還是 token 失效，重新拿一顆再試一次。
            self.refresh()
            return self.fetch(stock_no, date, _retry=False)
        return rows


def _parse_qry_html(html, stock_no, date):
    """解析查詢頁表格。

    注意：TDCC 在「差異數調整」為零時會把整列省掉，此時「合計」的序號會變成 16，
    所以序號欄不可信。改用分級文字判斷：合計 -> 17、差異數調整 -> 16，
    其餘依出現順序就是 1..15。
    """
    m = re.search(r'<table class="table">(.*?)</table>', html, re.S)
    if not m or "查無此資料" in m.group(1):
        return []
    out = []
    seq = 0
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S):
        cells = [re.sub(r"<[^>]+>", "", c).replace("　", "").strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) < 5 or not cells[0].isdigit():
            continue
        label = cells[1]
        if "合計" in label:
            level = db.LEVEL_TOTAL
        elif "差異數調整" in label or "差異" in label:
            level = db.LEVEL_ADJUST
        else:
            seq += 1
            if seq > 15:                       # 版面變了就不硬猜
                continue
            level = seq
        out.append((date, str(stock_no), level,
                    _int(cells[2]), _int(cells[3]), _float(cells[4])))
    return out


def backfill_stocks(conn, stock_nos, dates=None, weeks=None, progress=None,
                    skip_existing=True, delay=0.8):
    """逐檔逐週補歷史。回傳統計 dict。"""
    sc = QryStockScraper(delay=delay)
    all_dates = sc.available_dates()
    if dates:
        want = set(dates)
        target_dates = [d for d in all_dates if d in want]
    else:
        target_dates = all_dates[:weeks] if weeks else all_dates

    have = set()
    if skip_existing:
        have = {(r[0], r[1]) for r in conn.execute(
            "SELECT date, stock_no FROM holdings WHERE level=17")}

    stats = {"ok": 0, "empty": 0, "skip": 0, "error": 0, "rows": 0,
             "total": len(stock_nos) * len(target_dates), "errors": []}
    done = 0
    for stock in stock_nos:
        for d in target_dates:
            done += 1
            if skip_existing and (d, str(stock)) in have:
                stats["skip"] += 1
            else:
                try:
                    rows = sc.fetch(stock, d)
                    if rows:
                        stats["rows"] += upsert_holdings(conn, rows)
                        stats["ok"] += 1
                    else:
                        stats["empty"] += 1
                except Exception as exc:            # 單筆失敗不中斷整批
                    stats["error"] += 1
                    stats["errors"].append("%s/%s: %s" % (stock, d, exc))
            if progress:
                progress(done, stats["total"], stock, d, stats)
        conn.commit()
    db.log_import(conn, "qrystock",
                  "%d 檔 x %d 週" % (len(stock_nos), len(target_dates)),
                  str({k: v for k, v in stats.items() if k != "errors"}))
    conn.commit()
    return stats


# --------------------------------------------------------------------------
# 3. TWSE ISIN：證券名稱對照
# --------------------------------------------------------------------------

def fetch_stock_names(conn, timeout=120):
    """抓上市(2)/上櫃(4)證券基本資料。"""
    s = _session()
    total = 0
    for mode, market_default in ((2, "上市"), (4, "上櫃")):
        raw = s.get(ISIN_URL.format(mode=mode), timeout=timeout).content
        text = _decode_big5(raw)
        rows = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S):
            cells = [re.sub(r"<[^>]+>", "", c).replace("　", " ").strip()
                     for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if len(cells) < 6:
                continue
            m = re.match(r"^(\S+)\s+(.*)$", cells[0])
            if not m:
                continue
            code, name = m.group(1), m.group(2).strip()
            if not re.match(r"^[0-9A-Z]{4,6}$", code):
                continue
            rows.append((code, name, cells[3] or market_default,
                         cells[4], cells[1], cells[2]))
        conn.executemany(
            "INSERT INTO stocks(stock_no,name,market,industry,isin,listed_date) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(stock_no) DO UPDATE SET "
            "name=excluded.name, market=excluded.market, "
            "industry=excluded.industry, isin=excluded.isin, "
            "listed_date=excluded.listed_date", rows)
        total += len(rows)
    db.log_import(conn, "isin", "twse", "%d 檔證券基本資料" % total)
    conn.commit()
    return total


def _decode_big5(raw):
    for enc in ("cp950", "big5hkscs", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp950", errors="replace")
