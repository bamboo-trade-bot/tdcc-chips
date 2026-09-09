r"""產生單檔的靜態頁 `_site/index.html`，發佈到 GitHub Pages 用。

跟本機的 Flask 版共用 `tdcc/` 底下的抓取與計算，差別只在資料先被壓成
`static_data/`（每週一支 CSV + 兩支 JSON），頁面再把它們內嵌進去。

    python build_static.py --export        從本機 SQLite 匯出 static_data/
    python build_static.py --fetch         抓 TDCC 最新一週，補一支週 CSV（排程用）
    python build_static.py                 只重建頁面

排程環境（GitHub Actions）只會用到 --fetch 與重建，兩者都不需要 SQLite 或
pandas，只要 requests。`--export` 是本機把既有歷史倒出來時用的。
"""
import argparse
import datetime
import json
import os

from tdcc import static_export as se

BASE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(BASE, "page_template.html")
OUT_DIR = os.path.join(BASE, "_site")
PLACEHOLDER = "/*__DATA__*/"


def now_str():
    return datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")


def do_export():
    """本機：把 SQLite 裡的所有週、族群、個股資料倒進 static_data/。"""
    from tdcc import db
    conn = db.init_db()
    try:
        weeks = se.export_weeks(conn)
        n_stocks = se.export_stocks(conn)
        n_groups = se.export_groups(conn)
    finally:
        conn.close()
    print("匯出週資料 %d 支：%s" % (len(weeks), ", ".join(weeks) or "（已是最新）"))
    print("個股基本資料 %d 檔、族群 %d 個" % (n_stocks, n_groups))


def do_fetch():
    """排程：抓 TDCC 最新一週，若還沒有這一週就補一支 CSV。"""
    import requests

    from tdcc import collector
    resp = requests.get(collector.OPENDATA_URL, timeout=180,
                        headers={"User-Agent": collector.UA})
    resp.raise_for_status()
    rows, date = collector._parse_opendata_csv(
        resp.content.decode("utf-8-sig", errors="replace"))
    if not date:
        raise SystemExit("opendata 沒有回傳可用資料")

    have = {d for d, _p in se.available_weeks()}
    if date in have:
        print("已經有 %s 的資料，不重複寫入" % date)
        return date, False
    path = se.write_week_csv(date, se.week_rows_from_holdings(rows))
    print("新增 %s（%s）" % (date, os.path.basename(path)))
    return date, True


def do_build(weeks=None):
    with open(TEMPLATE, encoding="utf-8") as fh:
        tpl = fh.read()
    if PLACEHOLDER not in tpl:
        raise SystemExit("page_template.html 裡找不到 %s" % PLACEHOLDER)

    payload = se.build_payload(weeks=weeks, generated_at=now_str())
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    html = tpl.replace(PLACEHOLDER, blob)

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "index.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    # GitHub Pages 預設會跑 Jekyll，底線開頭的檔案會被吃掉，關掉它
    open(os.path.join(OUT_DIR, ".nojekyll"), "w").close()

    size = os.path.getsize(out) / 1048576
    print("產生 %s（%.2f MB，%d 週 × %d 檔）"
          % (out, size, len(payload["series"]), len(payload["codes"])))
    return out


def main():
    ap = argparse.ArgumentParser(description="建置集保籌碼靜態頁")
    ap.add_argument("--export", action="store_true",
                    help="從本機 SQLite 匯出 static_data/（需要 pandas 環境）")
    ap.add_argument("--fetch", action="store_true",
                    help="抓 TDCC 最新一週並補一支週 CSV")
    ap.add_argument("--weeks", type=int, default=None,
                    help="頁面只放最近 N 週（預設全部）")
    ap.add_argument("--no-build", action="store_true", help="只更新資料，不重建頁面")
    args = ap.parse_args()

    if args.export:
        do_export()
    if args.fetch:
        do_fetch()
    if not args.no_build:
        do_build(args.weeks)


if __name__ == "__main__":
    main()
