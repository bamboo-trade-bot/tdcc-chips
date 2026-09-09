"""命令列工具：不開網頁也能更新資料。

  python cli.py refresh              抓 TDCC 最新一週全市場 + 證券名稱
  python cli.py import               匯入 data/tdcc_weekly 與 data/groups
  python cli.py import --what groups 只匯入族群表
  python cli.py backfill --weeks 13  回補族群清單內股票的近 13 週
  python cli.py status               看資料庫涵蓋範圍
"""
import argparse
import json

from tdcc import analysis, collector, db, importer


def main():
    ap = argparse.ArgumentParser(description="集保籌碼分析 資料工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("refresh", help="抓 TDCC 最新一週 + 證券名稱")

    p_imp = sub.add_parser("import", help="匯入 data/ 底下的 Excel")
    p_imp.add_argument("--what", choices=["all", "weekly", "groups"], default="all")

    p_bf = sub.add_parser("backfill", help="爬 TDCC 查詢頁回補歷史")
    p_bf.add_argument("--weeks", type=int, default=13)
    p_bf.add_argument("--scope", choices=["groups", "all"], default="groups")
    p_bf.add_argument("--stocks", nargs="*", help="指定股號，蓋過 --scope")
    p_bf.add_argument("--delay", type=float, default=0.8, help="每次請求間隔秒數")

    sub.add_parser("status", help="顯示資料涵蓋範圍")

    args = ap.parse_args()
    conn = db.init_db()
    try:
        if args.cmd == "refresh":
            date, n = collector.fetch_opendata(conn)
            print("已匯入 %s 共 %d 筆" % (date, n))
            print("證券名稱 %d 檔" % collector.fetch_stock_names(conn))

        elif args.cmd == "import":
            if args.what in ("all", "weekly"):
                r = importer.import_weekly_folder(conn)
                print("集保週檔：%d 筆，日期 %s" % (r["total_rows"], r["dates"]))
                for f in r["files"]:
                    print("  - %s：%d 筆 %s %s" % (f["file"], f["rows"], f["dates"],
                                                  "; ".join(f["notes"])))
            if args.what in ("all", "groups"):
                r = importer.import_groups_folder(conn)
                print("族群：%d 組合，%d 個族群" % (r["total_pairs"], len(r["groups"])))
                for f in r["files"]:
                    print("  - %s：%d 筆 %s" % (f["file"], f["pairs"], "; ".join(f["notes"])))
                print("  族群清單：%s" % ", ".join(r["groups"]))
                m = importer.import_stock_meta_folder(conn)
                print("個股 metadata（市值／成交量／細產業）：%d 檔" % m["total"])

        elif args.cmd == "backfill":
            stocks = args.stocks
            if not stocks:
                q = ("SELECT DISTINCT stock_no FROM groups ORDER BY stock_no"
                     if args.scope == "groups" else
                     "SELECT DISTINCT stock_no FROM holdings ORDER BY stock_no")
                stocks = [r[0] for r in conn.execute(q)]
            print("回補 %d 檔 x 近 %d 週…" % (len(stocks), args.weeks))

            def prog(done, total, stock, date, st):
                if done % 20 == 0 or done == total:
                    print("  %d/%d  %s/%s  ok=%d skip=%d empty=%d err=%d"
                          % (done, total, stock, date, st["ok"], st["skip"],
                             st["empty"], st["error"]), flush=True)

            st = collector.backfill_stocks(conn, stocks, weeks=args.weeks,
                                           progress=prog, delay=args.delay)
            print(json.dumps({k: v for k, v in st.items() if k != "errors"},
                             ensure_ascii=False))
            for e in st["errors"][:10]:
                print("  ERR", e)

        elif args.cmd == "status":
            o = analysis.overview(conn)
            print("週數 %d  證券 %d  族群 %d  名稱 %d"
                  % (o["weeks"], o["stocks"], o["groups"], o["named"]))
            print("日期：%s" % ", ".join(o["dates"]))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
