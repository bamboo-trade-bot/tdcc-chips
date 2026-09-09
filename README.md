# 集保籌碼分析

以 TDCC 集保戶股權分散表為基礎的本機網頁工具：全市場籌碼變化排行、自訂族群的大戶籌碼彙總、個股籌碼走勢。

資料來源全部是公開資料，程式只在本機跑，資料存在 `data/tdcc.db`（SQLite）。

---

## 快速開始

雙擊 `run.bat`，或：

```bash
.venv\Scripts\python.exe app.py
```

瀏覽器開 <http://127.0.0.1:5057>。

第一次使用：

1. 把你的**族群分類 Excel** 丟進 `data\groups\`
2. 把你手上**集保過去每週的 Excel** 丟進 `data\tdcc_weekly\`
3. 開網頁 → 「資料管理」分頁 → 按「匯入 groups 族群表」和「匯入 tdcc_weekly Excel」
4. 按「抓 TDCC 最新一週（全市場）」補上本週資料

之後每週只要按一次「抓 TDCC 最新一週」就好。

---

## 分頁說明

| 分頁 | 用途 |
| --- | --- |
| **全市場排行** | 全市場依「大戶持股比例增減」等指標排序，可篩市場別、族群、股東人數門檻，可匯出 CSV |
| **族群總覽** | 每個族群一列：族群大戶持股比例、本週增減、增減家數、廣度 |
| **族群明細** | 單一族群的逐週走勢圖 + 成分股明細 |
| **個股** | 單檔的大戶／散戶比例走勢、股東人數走勢、最新一週 15 級分布 |
| **資料管理** | 抓資料、匯入 Excel、回補歷史、看匯入紀錄 |

上方四個控制項是全站共用的：

- **資料日期**：要看哪一週
- **比較基準**：跟前 1／2／4／8／13 週比
- **大戶門檻**：50／100／200／400／600／800／1000 張以上（對應集保分級 9–15 級）
- **散戶門檻**：5／10／20／50／100 張以下

指標都是即時由 17 個持股分級彙總出來的，改門檻不需要重算任何東西。

---

## 指標定義

以某一週、某一檔股票為單位：

- **大戶持股%** — 持股達門檻以上的所有分級，其「占集保庫存數比例」加總
- **大戶增減%** — 本期大戶持股% 減前期，單位是百分點
- **大戶增減張數** — 大戶合計股數的變化 ÷ 1000
- **股東人數 / 人數增減%** — 集保合計人數（第 17 級）。人數減少通常代表籌碼集中
- **平均張數** — 集保總股數 ÷ 總人數 ÷ 1000

族群層級：

- **等權平均** — 族群內每檔一票，直接平均。不會被權值股蓋過去
- **股數加權** — Σ大戶股數 ÷ Σ總股數。會被股本大的成分股主導
- **增／減家數、廣度** — 廣度 = (上升家數 − 下降家數) ÷ 有前期資料的家數 × 100

> 注意：集保分級是**股數**級距，不是市值。跨股票比較「張數」時要留意股本差異，
> 族群比較建議看等權平均與廣度。

---

## Excel 格式

### 族群分類（`data\groups\`）

三種格式都吃，會自動判斷：

**分頁格式** — 每個分頁的名稱就是族群，表內有股號欄（選股軟體匯出的分產業活頁簿通常長這樣）

| 代碼 | 商品 | 成交 | … |
| --- | --- | --- | --- |
| 2337.TW | 旺宏 | 169 | … |
| 2344.TW | 華邦電 | 218.5 | … |

代號帶 `.TW` / `.TWO` 後綴會自動去掉。分頁名稱含「全部／總表／全市場／all」的會被當成全市場清單跳過
（例如 `industry.xlsm` 的「全部產業」分頁）；想納入就把分頁改名。

**長格式** — 有「族群」和「股號」兩欄

| 族群 | 股號 | 名稱 |
| --- | --- | --- |
| CPO | 2330 | 台積電 |
| CPO | 3661 | 世芯-KY |
| 重電 | 1513 | 中興電 |

族群欄的標題必須**完全等於**這些之一：族群／類別／分類／產業／產業別／概念股／群組／主題／板塊。
用完全比對是刻意的——否則像「細產業」這種逗號串起來的標籤欄會被誤認成族群欄。
股號欄則是模糊比對：證券代號／股票代號／股號／代號／代碼都可以。

**寬格式** — 每一欄的標題是族群名稱，欄位底下整排是股號

| CPO | 重電 | 軍工 |
| --- | --- | --- |
| 2330 | 1513 | 2634 |
| 3661 | 1514 | 2645 |

多個 sheet 會全部讀進來。同一個檔案重新匯入會先清掉該檔上次匯入的內容再寫入，
所以直接覆蓋檔案再按匯入即可。

### 集保週資料（`data\tdcc_weekly\`）

跟 TDCC opendata 一樣的長格式：

| 資料日期 | 證券代號 | 持股分級 | 人數 | 股數 | 占集保庫存數比例% |
| --- | --- | --- | --- | --- | --- |
| 20260904 | 2330 | 1 | 2494369 | 291080358 | 1.12 |

- 沒有「資料日期」欄時，會從**檔名**抓 8 碼日期（例如 `20260904.xlsx`）
- 前幾列是說明文字也沒關係，程式會自己找表頭
- 重複匯入同一週會覆蓋，不會產生重複資料

---

## 命令列

不想開網頁時可以直接用 `cli.py`：

```bash
.venv\Scripts\python.exe cli.py refresh
.venv\Scripts\python.exe cli.py import
.venv\Scripts\python.exe cli.py import --what groups
.venv\Scripts\python.exe cli.py backfill --weeks 13
.venv\Scripts\python.exe cli.py status
```

---

## 資料來源

| 來源 | 用途 | 限制 |
| --- | --- | --- |
| `opendata.tdcc.com.tw/getOD.ashx?id=1-5` | 全市場最新一週股權分散表 | **只有最新一週**，約 2.3MB CSV |
| `tdcc.com.tw/portal/zh/smWeb/qryStock` | 單檔歷史，約可回溯 51 週 | 一次一檔一週，要逐筆爬 |
| `isin.twse.com.tw` | 證券代號 → 名稱／市場／產業 | — |

因為 opendata 只給最新一週，**歷史要靠兩條路**：手上的 Excel 匯入，或用「回補歷史」
逐檔爬 qryStock。回補預設只針對族群清單內的股票，並且每次請求間隔 0.8 秒，
請不要把 delay 調到 0 去打人家的站。全市場約 2000+ 檔，回補 13 週就是 26000 次請求，
會跑很久——這也是為什麼每週固定按一次「抓最新一週」把資料累積起來比較實際。

---

## 靜態站（GitHub Pages）

除了本機的 Flask 版，這個專案還能產生**單一檔案的靜態頁**發佈出去，作法跟
`taiwan-disposal` 一樣：資料先壓進 `static_data/`，`build_static.py` 把它內嵌進
`page_template.html`，產出 `_site/index.html`，由 GitHub Actions 排程重建並發佈。

靜態頁有五個分頁：族群排行、個股明細、全市場大戶增幅、個股查詢、選股策略。

### 為什麼要壓資料

完整的 17 分級明細是 13 週 × 4,000 檔 × 17 級 ≈ 90 萬筆，塞不進單檔頁面。
所以每檔每週只留 8 個數字（`static_data/weekly/YYYYMMDD.csv`）：

| 欄位 | 意思 |
| --- | --- |
| `people` / `lots` | 集保合計人數、合計張數 |
| `b4_pct` / `b4_people` / `b4_lots` | 400 張以上（分級 12~15）的比例、人數、張數 |
| `b1_pct` / `b1_people` | 1000 張以上（分級 15）的比例、人數 |
| `s10_pct` | 10 張以下（分級 1~3）的比例 |

**代價**：靜態頁的大戶門檻固定成 400 張與 1000 張，不像本機版可以 50~1000 張任意調。
要可調就得把 15 個分級全放進去，頁面會從 1.9 MB 變成十幾 MB。

合計人數低於 50 人的（特別股、受益證券之類）不收，4,051 檔會收斂到約 3,300 檔。

### 本機建置

```bash
.venv\Scripts\python.exe build_static.py --export
```

`--export` 從 SQLite 把所有週、族群、個股資料倒進 `static_data/`，然後建置頁面。
之後只改樣板的話直接 `build_static.py` 就好。要預覽：

```bash
cd _site && ..\.venv\Scripts\python.exe -m http.server 5058
```

### 建成獨立 repo 並發佈

> **⚠️ 這個資料夾要當成獨立的 repo。**不要在上層 `claude_code` 執行 `git init` 再推上去
> ——那裡有 `.env` 與 `.p12` 憑證檔，會連同金鑰一起公開。

```bash
git init && git add -A && git commit -m "集保族群大戶籌碼"
gh repo create tdcc-chips --public --source=. --push
```

接著在 GitHub repo 頁面：

1. **Settings → Pages → Source** 選 **GitHub Actions**（不是 Deploy from a branch）。
2. **Actions** 分頁 → 「抓取並發佈集保籌碼頁面」→ **Run workflow** 手動跑一次確認綠燈。
3. 網址是 `https://<你的帳號>.github.io/tdcc-chips/`，跟處置股是兩個各自獨立的站。

### 排程怎麼運作

集保的資料日期是每週最後一個營業日（通常是週五），隔週的週五／週六才發布。
workflow 排在**週六 20:00** 與**週日 09:00**（台北時間）各跑一次：

1. `build_static.py --fetch` 抓 opendata 最新一週，若這一週還沒有就寫一支新的週 CSV
2. 把新的 CSV commit 回 repo（歷史就這樣一週一週長出來）
3. 重建頁面並發佈

已經有的週不會重複寫入，所以第二次通常直接跳過。排程環境**只需要 `requests`**
——週資料是直接從 opendata 壓成 CSV，不經過 SQLite 也不用 pandas。

### 改了族群之後

族群與個股 metadata 是從 `industry.xlsm` 匯出的 JSON，Excel 本身不進版控。
所以改完族群要重新匯出再 push：

```bash
.venv\Scripts\python.exe cli.py import --what groups
.venv\Scripts\python.exe build_static.py --export
git add static_data && git commit -m "更新族群" && git push
```

### 進版控的東西

`static_data/`（約 2.2 MB：13 支週 CSV + 兩支 JSON）要進版控，`data/` 底下的
SQLite 與原始 Excel／CSV 都在 `.gitignore` 裡。

---

## 專案結構

```
app.py                 本機 Flask 服務與 API
cli.py                 命令列工具（抓取 / 匯入 / 回補）
build_static.py        產生單檔靜態頁
page_template.html     靜態頁樣板（/*__DATA__*/ 是資料注入點）

tdcc/db.py             SQLite schema、持股分級與門檻定義
tdcc/collector.py      opendata / qryStock / ISIN 抓取
tdcc/importer.py       Excel 匯入（集保週檔、族群分類、個股 metadata）
tdcc/analysis.py       指標計算：排行、族群彙總、個股歷史（本機版用）
tdcc/static_export.py  壓成靜態站資料 + 組 payload

templates/ static/     本機版網頁
data/tdcc.db           SQLite 資料庫（不進版控）
static_data/           靜態站資料（要進版控）
_site/index.html       建置產物
.github/workflows/     GitHub Actions 排程
```

兩個版本共用 `tdcc/` 底下的抓取與分級定義，差別在本機版即時查 SQLite、
門檻可調；靜態版讀預先壓好的 `static_data/`，門檻固定。
