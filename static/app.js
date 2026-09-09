/* 集保籌碼分析 前端 */
"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const charts = {};
const state = { overview: null, groups: [], currentGroup: null };

/* ---------- 共用小工具 ---------- */

const fmt = (v, d = 0) =>
  v === null || v === undefined || v === "" ? "—"
    : Number(v).toLocaleString("zh-TW", { minimumFractionDigits: d, maximumFractionDigits: d });

function delta(v, d = 2, suffix = "") {
  if (v === null || v === undefined) return '<span class="na">—</span>';
  const n = Number(v);
  const cls = n > 0 ? "up" : n < 0 ? "down" : "zero";
  const sign = n > 0 ? "+" : "";
  return `<span class="${cls}">${sign}${fmt(n, d)}${suffix}</span>`;
}

function bar(pct) {
  const w = Math.max(0, Math.min(100, Number(pct) || 0));
  return `<span class="bar"><span>${fmt(pct, 2)}</span>
    <span class="track"><span class="fill" style="width:${w}%"></span></span></span>`;
}

const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const roc = (d) => (d && d.length === 8 ? `${d.slice(0, 4)}/${d.slice(4, 6)}/${d.slice(6)}` : d || "—");

async function api(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).error || msg; } catch (e) { /* noop */ }
    throw new Error(msg);
  }
  return r.json();
}

function params(extra) {
  const p = new URLSearchParams({
    date: $("#c-date").value || "",
    span: $("#c-span").value,
    big: $("#c-big").value,
    small: $("#c-small").value,
  });
  Object.entries(extra || {}).forEach(([k, v]) => {
    if (v !== "" && v !== null && v !== undefined) p.set(k, v);
  });
  return p;
}

function drawChart(id, config) {
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart($("#" + id), config);
}

const PALETTE = ["#1f4f8b", "#d0342c", "#128a52", "#b5651d", "#6a4c93",
  "#0e7c8a", "#a3423c", "#5a7d2a", "#8a5a00", "#3d5a80"];

/* ---------- 分頁 ---------- */

$$("nav button").forEach((b) => b.addEventListener("click", () => {
  $$("nav button").forEach((x) => x.classList.toggle("active", x === b));
  $$(".page").forEach((p) => p.classList.toggle("active", p.id === "page-" + b.dataset.page));
  const p = b.dataset.page;
  if (p === "groups") loadGroups();
  if (p === "group") loadGroupPage();
  if (p === "data") { loadData(); }
}));

/* ---------- 啟動 ---------- */

async function boot() {
  const o = await api("/api/overview");
  state.overview = o;

  $("#c-date").innerHTML = o.dates.map((d) => `<option value="${d}">${roc(d)}</option>`).join("")
    || '<option value="">尚無資料</option>';
  $("#c-big").innerHTML = o.big_options.map(
    (v) => `<option value="${v}"${v === 400 ? " selected" : ""}>${v} 張以上</option>`).join("");
  $("#c-small").innerHTML = o.small_options.map(
    (v) => `<option value="${v}"${v === 10 ? " selected" : ""}>${v} 張以下</option>`).join("");
  $("#r-sort").innerHTML = o.sort_keys.map(
    (s) => `<option value="${s.key}">${s.label}</option>`).join("");

  $("#coverage").textContent =
    `${o.weeks} 週資料 ｜ ${fmt(o.stocks)} 檔證券 ｜ ${o.groups} 個族群 ｜ 最新 ${roc(o.dates[0])}`;

  await loadGroupOptions();
  if (o.weeks) loadRanking();
  else {
    $("#r-msg").innerHTML = '資料庫是空的。請切到 <b>資料管理</b> 分頁，'
      + '先按「抓 TDCC 最新一週」或匯入你的 Excel。';
    $$("nav button").forEach((x) => x.classList.toggle("active", x.dataset.page === "data"));
    $$(".page").forEach((p) => p.classList.toggle("active", p.id === "page-data"));
    loadData();
  }
}

async function loadGroupOptions() {
  const g = await api("/api/groups?" + params());
  state.groups = g.groups || [];
  const opts = ['<option value="">全部</option>'].concat(
    state.groups.map((x) => `<option value="${esc(x.group)}">${esc(x.group)} (${x.count})</option>`));
  $("#r-group").innerHTML = opts.join("");
}

/* ---------- 全市場排行 ---------- */

async function loadRanking() {
  $("#r-msg").textContent = "查詢中…";
  const p = params({
    sort: $("#r-sort").value,
    order: $("#r-order").value,
    market: $("#r-market").value,
    group: $("#r-group").value,
    min_people: $("#r-minpeople").value || 0,
    limit: $("#r-limit").value,
    q: $("#r-q").value,
    etf: $("#r-etf").checked ? "1" : "0",
  });
  let d;
  try { d = await api("/api/ranking?" + p); }
  catch (e) { $("#r-msg").innerHTML = `<span class="error">查詢失敗：${esc(e.message)}</span>`; return; }

  $("#c-compare").textContent = d.prev ? `本期 ${roc(d.date)} ／ 比較 ${roc(d.prev)}` : "（無前期可比較）";
  $("#r-title").textContent =
    `${roc(d.date)} 籌碼變化排行（大戶 ${d.big_lots} 張以上、散戶 ${d.small_lots} 張以下）`;
  $("#r-msg").textContent = `符合條件 ${fmt(d.total)} 檔，顯示 ${d.rows.length} 檔`;

  renderStockTable("#r-table", d.rows, true);
}

function renderStockTable(sel, rows, withGroups) {
  const head = `<thead><tr>
    <th class="l nosort">代號</th><th class="l nosort">名稱</th>
    <th class="l nosort">市場</th>
    ${withGroups ? '<th class="l nosort">族群</th>' : ""}
    <th class="nosort">大戶持股%</th><th class="nosort">大戶增減%</th>
    <th class="nosort">大戶增減張數</th><th class="nosort">大戶人數</th>
    <th class="nosort">散戶持股%</th><th class="nosort">散戶增減%</th>
    <th class="nosort">股東人數</th><th class="nosort">人數增減%</th>
    <th class="nosort">平均張數</th><th class="nosort">平均增減</th>
  </tr></thead>`;
  const body = rows.map((r) => `<tr>
    <td class="l code"><a class="link" data-stock="${esc(r.stock_no)}">${esc(r.stock_no)}</a></td>
    <td class="l name" title="${esc(r.name)}">${esc(r.name)}</td>
    <td class="l">${esc(r.market)}</td>
    ${withGroups ? `<td class="l">${(r.groups || []).map(
      (g) => `<span class="tag" data-group="${esc(g)}">${esc(g)}</span>`).join("")}</td>` : ""}
    <td>${bar(r.big_pct)}</td>
    <td>${delta(r.d_big_pct)}</td>
    <td>${delta(r.d_big_lots, 0)}</td>
    <td>${fmt(r.big_people)}</td>
    <td>${fmt(r.small_pct, 2)}</td>
    <td>${delta(r.d_small_pct)}</td>
    <td>${fmt(r.total_people)}</td>
    <td>${delta(r.d_total_people_pct, 2, "%")}</td>
    <td>${fmt(r.avg_lots, 2)}</td>
    <td>${delta(r.d_avg_lots)}</td>
  </tr>`).join("");
  $(sel).innerHTML = head + `<tbody>${body || '<tr><td colspan="14">無資料</td></tr>'}</tbody>`;
}

/* 表格內點代號 -> 個股頁；點族群 tag -> 族群明細 */
document.addEventListener("click", (e) => {
  const s = e.target.closest("[data-stock]");
  if (s) { gotoStock(s.dataset.stock); return; }
  const g = e.target.closest("[data-group]");
  if (g) { gotoGroup(g.dataset.group); }
});

/* ---------- 族群總覽 ---------- */

async function loadGroups() {
  $("#g-msg").textContent = "計算中…";
  let d;
  try { d = await api("/api/groups?" + params()); }
  catch (e) { $("#g-msg").innerHTML = `<span class="error">${esc(e.message)}</span>`; return; }
  state.groups = d.groups || [];

  if (!state.groups.length) {
    $("#g-msg").innerHTML = "還沒有族群資料。把族群分類 Excel 放進 <code>data\\groups\\</code> "
      + "後到「資料管理」按匯入。";
    $("#g-table").innerHTML = "";
    return;
  }
  $("#g-msg").textContent =
    `${roc(d.date)} vs ${roc(d.prev)} ｜ 大戶 ${d.big_lots} 張以上、散戶 ${d.small_lots} 張以下`;

  const head = `<thead><tr>
    <th class="l nosort">族群</th><th class="nosort">檔數</th>
    <th class="nosort">大戶持股%<br><span class="sub">等權平均</span></th>
    <th class="nosort">大戶增減%<br><span class="sub">等權平均</span></th>
    <th class="nosort">大戶持股%<br><span class="sub">股數加權</span></th>
    <th class="nosort">大戶增減張數<br><span class="sub">族群合計</span></th>
    <th class="nosort">散戶持股%</th><th class="nosort">散戶增減%</th>
    <th class="nosort">人數增減%</th>
    <th class="nosort">增／減家數</th><th class="nosort">廣度</th>
  </tr></thead>`;
  const body = state.groups.map((g) => `<tr>
    <td class="l"><a class="link" data-group="${esc(g.group)}">${esc(g.group)}</a></td>
    <td>${g.count}${g.missing.length ? `<span class="na" title="無集保資料：${esc(g.missing.join(","))}"> (-${g.missing.length})</span>` : ""}</td>
    <td>${bar(g.big_pct_avg)}</td>
    <td>${delta(g.d_big_pct_avg)}</td>
    <td>${fmt(g.big_pct_weighted, 2)}</td>
    <td>${delta(g.d_big_lots_sum, 0)}</td>
    <td>${fmt(g.small_pct_avg, 2)}</td>
    <td>${delta(g.d_small_pct_avg)}</td>
    <td>${delta(g.d_total_people_pct, 2, "%")}</td>
    <td><span class="up">${g.up}</span> / <span class="down">${g.down}</span></td>
    <td>${delta(g.breadth, 1, "%")}</td>
  </tr>`).join("");
  $("#g-table").innerHTML = head + `<tbody>${body}</tbody>`;

  const top = state.groups.filter((g) => g.d_big_pct_avg !== null).slice(0, 25);
  drawChart("g-chart", {
    type: "bar",
    data: {
      labels: top.map((g) => g.group),
      datasets: [{
        label: "大戶持股比例增減（百分點，等權平均）",
        data: top.map((g) => g.d_big_pct_avg),
        backgroundColor: top.map((g) => (g.d_big_pct_avg >= 0 ? "#d0342c" : "#128a52")),
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { x: { ticks: { autoSkip: false, maxRotation: 60, minRotation: 45 } } },
    },
  });
}

/* ---------- 族群明細 ---------- */

function gotoGroup(name) {
  state.currentGroup = name;
  $$("nav button").forEach((x) => x.classList.toggle("active", x.dataset.page === "group"));
  $$(".page").forEach((p) => p.classList.toggle("active", p.id === "page-group"));
  loadGroupPage();
}

async function loadGroupPage() {
  if (!state.groups.length) {
    try { state.groups = (await api("/api/groups?" + params())).groups || []; }
    catch (e) { /* noop */ }
  }
  if (!state.groups.length) {
    $("#gd-msg").innerHTML = "還沒有族群資料。";
    return;
  }
  if (!state.currentGroup) state.currentGroup = state.groups[0].group;

  $("#gd-pick").innerHTML = state.groups.map((g) =>
    `<button data-pick="${esc(g.group)}"${g.group === state.currentGroup ? ' class="active"' : ""}>${esc(g.group)}</button>`).join("");
  $$("#gd-pick button").forEach((b) => b.addEventListener("click", () => {
    state.currentGroup = b.dataset.pick;
    loadGroupPage();
  }));

  $("#gd-msg").textContent = "載入中…";
  let d;
  try { d = await api("/api/group/" + encodeURIComponent(state.currentGroup) + "?" + params()); }
  catch (e) { $("#gd-msg").innerHTML = `<span class="error">${esc(e.message)}</span>`; return; }

  const g = state.groups.find((x) => x.group === state.currentGroup) || {};
  $("#gd-title").textContent = `${state.currentGroup} — 大戶籌碼走勢`;
  $("#gd-cards").innerHTML = [
    ["成分股", fmt(d.rows.length) + " 檔"],
    ["大戶持股%（等權）", fmt(g.big_pct_avg, 2)],
    ["本期增減", (g.d_big_pct_avg === null || g.d_big_pct_avg === undefined) ? "—"
      : (g.d_big_pct_avg > 0 ? "+" : "") + fmt(g.d_big_pct_avg, 2) + " pp"],
    ["增／減家數", `${g.up || 0} / ${g.down || 0}`],
    ["族群大戶增減張數", fmt(g.d_big_lots_sum, 0)],
    ["股東人數合計", fmt(g.total_people)],
  ].map(([k, v]) => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");

  $("#gd-msg").textContent = `${roc(d.date)} vs ${roc(d.prev)}`;
  renderStockTable("#gd-table", d.rows, false);

  const pts = d.trend || [];
  drawChart("gd-chart", {
    type: "line",
    data: {
      labels: pts.map((p) => roc(p.date)),
      datasets: [
        { label: "大戶持股%（等權平均）", data: pts.map((p) => p.big_pct_avg),
          borderColor: "#d0342c", backgroundColor: "#d0342c", tension: .25, yAxisID: "y" },
        { label: "大戶持股%（股數加權）", data: pts.map((p) => p.big_pct_weighted),
          borderColor: "#b5651d", backgroundColor: "#b5651d", borderDash: [5, 4], tension: .25, yAxisID: "y" },
        { label: "散戶持股%（等權平均）", data: pts.map((p) => p.small_pct_avg),
          borderColor: "#128a52", backgroundColor: "#128a52", tension: .25, yAxisID: "y" },
        { label: "族群股東人數合計", data: pts.map((p) => p.total_people),
          borderColor: "#1f4f8b", backgroundColor: "#1f4f8b", tension: .25, yAxisID: "y1" },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      scales: {
        y: { position: "left", title: { display: true, text: "持股比例 %" } },
        y1: { position: "right", grid: { drawOnChartArea: false }, title: { display: true, text: "人數" } },
      },
    },
  });
}

/* ---------- 個股 ---------- */

function gotoStock(no) {
  $("#s-no").value = no;
  $$("nav button").forEach((x) => x.classList.toggle("active", x.dataset.page === "stock"));
  $$(".page").forEach((p) => p.classList.toggle("active", p.id === "page-stock"));
  loadStock();
}

async function loadStock() {
  const no = ($("#s-no").value || "").trim().toUpperCase();
  if (!no) return;
  $("#s-info").textContent = "載入中…";
  let d;
  try { d = await api("/api/stock/" + encodeURIComponent(no) + "?" + params()); }
  catch (e) { $("#s-info").textContent = "查詢失敗：" + e.message; return; }

  if (!d.points.length) {
    $("#s-info").innerHTML = `<span class="error">${esc(no)} 沒有集保資料</span>`;
    return;
  }
  const last = d.points[d.points.length - 1];
  $("#s-info").innerHTML = `${esc(d.name)}（${esc(d.market)} ${esc(d.industry)}）`
    + `　族群：${d.groups.length ? d.groups.map((g) => `<span class="tag" data-group="${esc(g)}">${esc(g)}</span>`).join("") : "—"}`
    + `　最新 ${roc(last.date)}：大戶 ${fmt(last.big_pct, 2)}%、股東 ${fmt(last.total_people)} 人`;

  drawChart("s-chart", {
    type: "line",
    data: {
      labels: d.points.map((p) => roc(p.date)),
      datasets: [
        { label: `大戶 ${d.big_lots} 張以上 %`, data: d.points.map((p) => p.big_pct),
          borderColor: "#d0342c", backgroundColor: "#d0342c", tension: .25 },
        { label: `散戶 ${d.small_lots} 張以下 %`, data: d.points.map((p) => p.small_pct),
          borderColor: "#128a52", backgroundColor: "#128a52", tension: .25 },
      ],
    },
    options: { responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false } },
  });

  drawChart("s-chart2", {
    type: "line",
    data: {
      labels: d.points.map((p) => roc(p.date)),
      datasets: [
        { label: "股東總人數", data: d.points.map((p) => p.total_people),
          borderColor: "#1f4f8b", backgroundColor: "#1f4f8b", tension: .25, yAxisID: "y" },
        { label: "平均每人持股（張）", data: d.points.map((p) => p.avg_lots),
          borderColor: "#6a4c93", backgroundColor: "#6a4c93", tension: .25, yAxisID: "y1" },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      scales: { y: { position: "left" }, y1: { position: "right", grid: { drawOnChartArea: false } } },
    },
  });

  $("#s-dist-title").textContent = `${roc(last.date)} 持股分級分布`;
  $("#s-table").innerHTML = `<thead><tr>
      <th class="nosort">序</th><th class="l nosort">持股分級（股）</th>
      <th class="nosort">人數</th><th class="nosort">股數</th>
      <th class="nosort">張數</th><th class="nosort">占集保庫存 %</th>
    </tr></thead><tbody>` + d.distribution.map((x) => `<tr>
      <td>${x.level}</td><td class="l">${esc(x.label)}</td>
      <td>${fmt(x.people)}</td><td>${fmt(x.shares)}</td>
      <td>${fmt(Math.round(x.shares / 1000))}</td><td>${fmt(x.pct, 2)}</td>
    </tr>`).join("") + "</tbody>";
}

/* ---------- 資料管理 ---------- */

async function loadData() {
  const o = await api("/api/overview");
  state.overview = o;
  $("#d-cards").innerHTML = [
    ["資料週數", fmt(o.weeks)],
    ["最新日期", roc(o.dates[0])],
    ["最舊日期", roc(o.dates[o.dates.length - 1])],
    ["證券檔數", fmt(o.stocks)],
    ["名稱對照", fmt(o.named)],
    ["族群數", fmt(o.groups)],
  ].map(([k, v]) => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
  $("#d-log").textContent = o.logs.length
    ? o.logs.map((l) => `[${l.ts}] ${l.kind} ${l.source}\n    ${l.detail}`).join("\n")
    : "尚無紀錄";

  const f = await api("/api/files");
  $("#d-files-weekly").textContent = f.weekly.length ? f.weekly.join("\n") : "（空）";
  $("#d-files-groups").textContent = f.groups.length ? f.groups.join("\n") : "（空）";
}

let poll = null;

function startPoll() {
  $("#d-progress").style.display = "block";
  if (poll) clearInterval(poll);
  poll = setInterval(async () => {
    const j = await api("/api/job");
    $("#d-msg").innerHTML = j.error
      ? `<span class="error">${esc(j.error)}</span>`
      : esc(j.message || "");
    const pct = j.total ? (j.done / j.total) * 100 : (j.running ? 100 : 0);
    $("#d-progress .fill").style.width = pct + "%";
    if (!j.running) {
      clearInterval(poll); poll = null;
      $("#d-progress").style.display = "none";
      if (j.stats) $("#d-msg").innerHTML += "<br>" + esc(JSON.stringify(j.stats));
      await loadData();
      await boot();
    }
  }, 1200);
}

async function post(url, body) {
  $("#d-msg").textContent = "送出中…";
  try {
    await api(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    startPoll();
  } catch (e) {
    $("#d-msg").innerHTML = `<span class="error">${esc(e.message)}</span>`;
  }
}

/* ---------- 事件綁定 ---------- */

$("#c-apply").addEventListener("click", () => {
  const active = $("nav button.active").dataset.page;
  loadGroupOptions();
  if (active === "rank") loadRanking();
  else if (active === "groups") loadGroups();
  else if (active === "group") loadGroupPage();
  else if (active === "stock") loadStock();
});
$("#r-go").addEventListener("click", loadRanking);
$("#r-q").addEventListener("keydown", (e) => { if (e.key === "Enter") loadRanking(); });
$("#r-csv").addEventListener("click", () => {
  const p = params({
    sort: $("#r-sort").value, order: $("#r-order").value,
    market: $("#r-market").value, group: $("#r-group").value,
    min_people: $("#r-minpeople").value || 0, q: $("#r-q").value,
    etf: $("#r-etf").checked ? "1" : "0",
  });
  window.location = "/api/export.csv?" + p;
});
$("#s-go").addEventListener("click", loadStock);
$("#s-no").addEventListener("keydown", (e) => { if (e.key === "Enter") loadStock(); });
$("#d-refresh").addEventListener("click", () => post("/api/refresh"));
$("#d-import-weekly").addEventListener("click", () => post("/api/import?what=weekly"));
$("#d-import-groups").addEventListener("click", () => post("/api/import?what=groups"));
$("#d-backfill").addEventListener("click", () => post("/api/backfill", {
  weeks: Number($("#d-weeks").value), scope: $("#d-scope").value,
}));

boot().catch((e) => { $("#coverage").textContent = "啟動失敗：" + e.message; });
