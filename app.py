r"""集保籌碼分析 — 本機網頁服務。

啟動：  .venv\Scripts\python.exe app.py     然後開 http://127.0.0.1:5057
"""
import csv
import io
import threading
import traceback

from flask import Flask, Response, jsonify, request, render_template

from tdcc import analysis, collector, db, importer

app = Flask(__name__)
app.json.sort_keys = False   # 保持 sort_keys 等排序選項的原始順序
_lock = threading.Lock()

# 背景抓取工作的狀態（一次只跑一個）
JOB = {"running": False, "kind": None, "done": 0, "total": 0,
       "message": "", "stats": None, "error": None}


def conn():
    return db.init_db()


def _args():
    """共用的查詢參數。"""
    g = request.args.get
    return {
        "date": g("date") or None,
        "prev": g("prev") or None,
        "span": int(g("span") or 1),
        "big_lots": int(g("big") or 400),
        "small_lots": int(g("small") or 10),
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/overview")
def api_overview():
    c = conn()
    try:
        return jsonify(analysis.overview(c))
    finally:
        c.close()


@app.route("/api/ranking")
def api_ranking():
    g = request.args.get
    c = conn()
    try:
        return jsonify(analysis.ranking(
            c, market=g("market") or None, group=g("group") or None,
            exclude_etf=(g("etf") != "1"),
            min_people=int(g("min_people") or 0),
            min_shares=int(g("min_shares") or 0),
            sort=g("sort") or "d_big_pct", order=g("order") or "desc",
            limit=int(g("limit") or 100), query=g("q") or None, **_args()))
    finally:
        c.close()


@app.route("/api/groups")
def api_groups():
    c = conn()
    try:
        return jsonify(analysis.group_summary(c, **_args()))
    finally:
        c.close()


@app.route("/api/group/<path:name>")
def api_group(name):
    c = conn()
    try:
        data = analysis.group_detail(c, name, **_args())
        data["trend"] = analysis.group_trend(
            c, name, big_lots=_args()["big_lots"],
            small_lots=_args()["small_lots"])["points"]
        data["group"] = name
        return jsonify(data)
    finally:
        c.close()


@app.route("/api/stock/<stock_no>")
def api_stock(stock_no):
    a = _args()
    c = conn()
    try:
        return jsonify(analysis.stock_history(
            c, stock_no, big_lots=a["big_lots"], small_lots=a["small_lots"]))
    finally:
        c.close()


@app.route("/api/export.csv")
def api_export():
    g = request.args.get
    c = conn()
    try:
        data = analysis.ranking(
            c, market=g("market") or None, group=g("group") or None,
            exclude_etf=(g("etf") != "1"),
            min_people=int(g("min_people") or 0),
            min_shares=int(g("min_shares") or 0),
            sort=g("sort") or "d_big_pct", order=g("order") or "desc",
            limit=0, query=g("q") or None, **_args())
    finally:
        c.close()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["證券代號", "名稱", "市場", "產業", "族群",
                "大戶持股%", "大戶增減%", "大戶增減張數", "大戶人數",
                "散戶持股%", "散戶增減%",
                "股東人數", "人數增減", "人數增減%", "平均張數", "平均張數增減"])
    for r in data["rows"]:
        w.writerow([r["stock_no"], r["name"], r["market"], r["industry"],
                    "/".join(r["groups"]),
                    r["big_pct"], r["d_big_pct"], r["d_big_lots"], r["big_people"],
                    r["small_pct"], r["d_small_pct"],
                    r["total_people"], r["d_total_people"],
                    r["d_total_people_pct"], r["avg_lots"], r["d_avg_lots"]])
    fn = "tdcc_%s_vs_%s.csv" % (data["date"], data["prev"])
    return Response(buf.getvalue().encode("utf-8-sig"), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=" + fn})


# --------------------------------------------------------------------------
# 資料更新
# --------------------------------------------------------------------------

def _run_job(kind, fn):
    if JOB["running"]:
        return False
    JOB.update({"running": True, "kind": kind, "done": 0, "total": 0,
                "message": "開始…", "stats": None, "error": None})

    def worker():
        c = db.init_db()
        try:
            fn(c)
            JOB["message"] = "完成"
        except Exception as exc:
            JOB["error"] = "%s: %s" % (type(exc).__name__, exc)
            JOB["message"] = "失敗"
            traceback.print_exc()
        finally:
            c.close()
            JOB["running"] = False

    threading.Thread(target=worker, daemon=True).start()
    return True


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    def job(c):
        JOB["message"] = "下載 TDCC 最新一週全市場資料…"
        date, n = collector.fetch_opendata(c)
        JOB["message"] = "已匯入 %s 共 %d 筆，更新證券名稱…" % (date, n)
        m = collector.fetch_stock_names(c)
        JOB["stats"] = {"date": date, "rows": n, "names": m}
        JOB["message"] = "完成：%s（%d 筆），名稱 %d 檔" % (date, n, m)

    if not _run_job("refresh", job):
        return jsonify({"ok": False, "error": "已有工作進行中"}), 409
    return jsonify({"ok": True})


@app.route("/api/import", methods=["POST"])
def api_import():
    what = request.args.get("what", "all")

    def job(c):
        stats = {}
        if what in ("all", "weekly"):
            JOB["message"] = "匯入 data/tdcc_weekly 的集保週檔…"
            stats["weekly"] = importer.import_weekly_folder(c)
        if what in ("all", "groups"):
            JOB["message"] = "匯入 data/groups 的族群分類…"
            stats["groups"] = importer.import_groups_folder(c)
            JOB["message"] = "讀取個股 metadata（市值／成交量／細產業）…"
            stats["meta"] = importer.import_stock_meta_folder(c)
        JOB["stats"] = stats
        JOB["message"] = "匯入完成"

    if not _run_job("import", job):
        return jsonify({"ok": False, "error": "已有工作進行中"}), 409
    return jsonify({"ok": True})


@app.route("/api/backfill", methods=["POST"])
def api_backfill():
    body = request.get_json(silent=True) or {}
    weeks = int(body.get("weeks") or 13)
    scope = body.get("scope") or "groups"
    stocks = body.get("stocks") or []

    def job(c):
        nonlocal stocks
        if not stocks:
            if scope == "groups":
                stocks = [r[0] for r in c.execute(
                    "SELECT DISTINCT stock_no FROM groups ORDER BY stock_no")]
            else:
                stocks = [r[0] for r in c.execute(
                    "SELECT DISTINCT stock_no FROM holdings ORDER BY stock_no")]
        if not stocks:
            raise RuntimeError("沒有可回補的股票（族群清單是空的？）")

        def progress(done, total, stock, date, st):
            JOB.update({"done": done, "total": total,
                        "message": "回補 %s / %s（%d/%d）" % (stock, date, done, total),
                        "stats": {k: v for k, v in st.items() if k != "errors"}})

        st = collector.backfill_stocks(c, stocks, weeks=weeks, progress=progress)
        JOB["stats"] = {k: v for k, v in st.items() if k != "errors"}
        JOB["message"] = "回補完成：新增 %d 筆" % st["rows"]

    if not _run_job("backfill", job):
        return jsonify({"ok": False, "error": "已有工作進行中"}), 409
    return jsonify({"ok": True})


@app.route("/api/job")
def api_job():
    return jsonify(JOB)


@app.route("/api/files")
def api_files():
    import os
    return jsonify({
        "weekly": [os.path.basename(p) for p in importer.scan_weekly_files()],
        "groups": [os.path.basename(p) for p in importer.scan_group_files()],
        "weekly_dir": importer.WEEKLY_DIR,
        "groups_dir": importer.GROUPS_DIR,
    })


if __name__ == "__main__":
    db.init_db().close()
    print("集保籌碼分析：http://127.0.0.1:5057")
    app.run(host="127.0.0.1", port=5057, debug=False, threaded=True)
