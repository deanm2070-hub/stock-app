/* 台股籌碼看盤前端 */
const $ = (s) => document.querySelector(s);
const api = async (url, opts) => {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};
const fmt = (n, d = 2) => (n === null || n === undefined || isNaN(n) ? "-" : Number(n).toLocaleString("zh-TW", { minimumFractionDigits: d, maximumFractionDigits: d }));
const fmtInt = (n) => fmt(n, 0);
const cls = (n) => (n > 0 ? "up" : n < 0 ? "down" : "flat");
const sign = (n) => (n > 0 ? "▲" : n < 0 ? "▼" : "");
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

const state = { code: null, watch: [], quotes: {}, kline: [], names: {} };

/* ---------- 圖表 ---------- */
const LC = LightweightCharts;
const baseOpts = {
  autoSize: true,
  layout: { background: { color: css("--bg") }, textColor: css("--muted"), fontSize: 11 },
  grid: { vertLines: { color: "#1a1f27" }, horzLines: { color: "#1a1f27" } },
  rightPriceScale: { borderColor: css("--line"), minimumWidth: 70 },
  timeScale: { borderColor: css("--line"), rightOffset: 3 },
  crosshair: { mode: LC.CrosshairMode.Normal },
  localization: { locale: "zh-TW" },
};
const UP = css("--up"), DOWN = css("--down");

const mainChart = LC.createChart($("#chart-main"), baseOpts);
const candles = mainChart.addCandlestickSeries({ upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN, wickUpColor: UP, wickDownColor: DOWN });
const maDefs = [[5, "#f5c542"], [20, "#4ea1ff"], [60, "#b48cff"]];
const maSeries = maDefs.map(([n, c]) => mainChart.addLineSeries({ color: c, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }));

const subOpts = { ...baseOpts, timeScale: { ...baseOpts.timeScale, visible: false } };
const volChart = LC.createChart($("#chart-vol"), subOpts);
const volSeries = volChart.addHistogramSeries({ priceFormat: { type: "volume" }, priceLineVisible: false });

const instChart = LC.createChart($("#chart-inst"), subOpts);
const instTotal = instChart.addHistogramSeries({ priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false });
const instFx = instChart.addLineSeries({ color: css("--fx"), lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
const instTr = instChart.addLineSeries({ color: css("--tr"), lineWidth: 1, priceLineVisible: false, lastValueVisible: false });

const mgChart = LC.createChart($("#chart-margin"), subOpts);
const mgSeries = mgChart.addLineSeries({ color: css("--tr"), lineWidth: 2, priceLineVisible: false });

// 同步四張圖的捲動與縮放
const charts = [mainChart, volChart, instChart, mgChart];
let syncing = false;
charts.forEach((c) => c.timeScale().subscribeVisibleLogicalRangeChange((range) => {
  if (syncing || !range) return;
  syncing = true;
  charts.forEach((o) => o !== c && o.timeScale().setVisibleLogicalRange(range));
  syncing = false;
}));

mainChart.subscribeCrosshairMove((p) => {
  const bar = p.time ? state.kline.find((k) => k.date === p.time) : state.kline[state.kline.length - 1];
  renderLegend(bar);
});

function ma(rows, n) {
  const out = [];
  let sum = 0;
  rows.forEach((r, i) => {
    sum += r.close;
    if (i >= n) sum -= rows[i - n].close;
    if (i >= n - 1) out.push({ time: r.date, value: +(sum / n).toFixed(2) });
  });
  return out;
}

function drawKline() {
  const k = state.kline.filter((r) => r.close);
  candles.setData(k.map((r) => ({ time: r.date, open: r.open ?? r.close, high: r.high ?? r.close, low: r.low ?? r.close, close: r.close })));
  maSeries.forEach((s, i) => s.setData(ma(k, maDefs[i][0])));
  volSeries.setData(k.map((r) => ({ time: r.date, value: Math.round((r.volume || 0) / 1000), color: (r.change || 0) >= 0 ? UP + "aa" : DOWN + "aa" })));
  const ki = k.filter((r) => r.total_net !== null && r.total_net !== undefined);
  instTotal.setData(ki.map((r) => ({ time: r.date, value: Math.round(r.total_net / 1000), color: r.total_net >= 0 ? UP + "99" : DOWN + "99" })));
  instFx.setData(ki.map((r) => ({ time: r.date, value: Math.round(r.foreign_net / 1000) })));
  instTr.setData(ki.map((r) => ({ time: r.date, value: Math.round(r.trust_net / 1000) })));
  mgSeries.setData(k.filter((r) => r.margin_balance != null).map((r) => ({ time: r.date, value: r.margin_balance })));
  const n = k.length;
  if (n) mainChart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 120), to: n + 3 });
  renderLegend(k[n - 1]);
  renderInstTable(k.slice(-10).reverse());
}

function renderLegend(bar) {
  if (!bar) return ($("#legend").innerHTML = "");
  const idx = state.kline.indexOf(bar);
  const mas = maDefs.map(([n, c]) => {
    if (idx < n - 1) return "";
    const v = state.kline.slice(idx - n + 1, idx + 1).reduce((s, r) => s + r.close, 0) / n;
    return `<b style="color:${c}">MA${n} ${fmt(v)}</b>`;
  }).join("");
  $("#legend").innerHTML = `<b>${bar.date}</b><b>開 ${fmt(bar.open)}</b><b>高 ${fmt(bar.high)}</b><b>低 ${fmt(bar.low)}</b>` +
    `<b class="${cls(bar.change)}">收 ${fmt(bar.close)}</b><b>量 ${fmtInt((bar.volume || 0) / 1000)} 張</b>${mas}`;
}

function renderInstTable(rows) {
  const t = (v) => `<td class="${cls(v)}">${v == null ? "-" : fmtInt(v / 1000)}</td>`;
  $("#inst-table").innerHTML = `<tr><th>日期</th><th>外資</th><th>投信</th><th>自營</th></tr>` +
    rows.map((r) => `<tr><td>${r.date.slice(5)}</td>${t(r.foreign_net)}${t(r.trust_net)}${t(r.dealer_net)}</tr>`).join("");
}

/* ---------- 即時報價 ---------- */
function isMarketOpen() {
  const now = new Date();
  const tw = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Taipei" }));
  const m = tw.getHours() * 60 + tw.getMinutes();
  return tw.getDay() >= 1 && tw.getDay() <= 5 && m >= 8 * 60 + 30 && m <= 13 * 60 + 35;
}

async function refreshQuotes() {
  const codes = [...new Set([...state.watch, state.code].filter(Boolean))];
  if (!codes.length) return;
  try {
    const data = await api(`/api/quote?codes=${codes.join(",")}`);
    data.forEach((q) => (state.quotes[q.code] = q));
    renderWatch();
    renderQuote();
    mergeRealtimeBar();
    setStatus();
  } catch (e) {
    setStatus(`即時報價失敗：${e.message}`);
  }
}

function mergeRealtimeBar() {
  const q = state.quotes[state.code];
  if (!q || q.price == null || !q.time) return;
  const d = q.time.slice(0, 8);
  const date = `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`;
  if (!/^\d{4}-\d\d-\d\d$/.test(date)) return;
  const last = state.kline[state.kline.length - 1];
  const bar = { date, open: q.open ?? q.price, high: q.high ?? q.price, low: q.low ?? q.price, close: q.price,
    volume: (q.volume || 0) * 1000, change: q.change };
  if (last && last.date === date) Object.assign(last, bar);
  else if (!last || last.date < date) state.kline.push(bar);
  else return;
  const b = state.kline[state.kline.length - 1];
  candles.update({ time: b.date, open: b.open, high: b.high, low: b.low, close: b.close });
  volSeries.update({ time: b.date, value: Math.round(b.volume / 1000), color: (b.change || 0) >= 0 ? UP + "aa" : DOWN + "aa" });
  renderLegend(b);
}

function renderQuote() {
  const q = state.quotes[state.code] || {};
  const name = q.name || state.names[state.code] || "";
  const last = state.kline[state.kline.length - 1] || {};
  const price = q.price ?? last.close, chg = q.change ?? last.change;
  const pct = q.pct ?? (last.close && last.change != null ? (last.change / (last.close - last.change)) * 100 : null);
  $("#quote-head").innerHTML = `<span class="name">${state.code} ${name}</span>
    <span class="price ${cls(chg)}">${fmt(price)}</span>
    <span class="${cls(chg)}">${sign(chg)} ${fmt(Math.abs(chg ?? 0))} (${fmt(pct)}%)</span>
    <span class="ohlc">開 ${fmt(q.open ?? last.open)}　高 ${fmt(q.high ?? last.high)}　低 ${fmt(q.low ?? last.low)}　量 ${fmtInt(q.volume ?? (last.volume || 0) / 1000)} 張　${q.time ? "更新 " + q.time.slice(9) : "（盤後資料）"}</span>`;
  const asks = (q.asks || []).slice(0, 5).reverse(), bids = (q.bids || []).slice(0, 5);
  const ref = q.prev_close;
  $("#book").innerHTML = asks.map((a) => `<tr><td class="hint">賣</td><td class="${cls(a.price - ref)}">${fmt(a.price)}</td><td>${fmtInt(a.vol)}</td></tr>`).join("") +
    `<tr><td colspan="3" style="border-top:1px solid var(--line)"></td></tr>` +
    bids.map((b) => `<tr><td class="hint">買</td><td class="${cls(b.price - ref)}">${fmt(b.price)}</td><td>${fmtInt(b.vol)}</td></tr>`).join("") ||
    `<tr><td class="hint" style="text-align:center">盤中才有五檔</td></tr>`;
}

async function renderInfo() {
  try {
    const s = await api(`/api/stock/${state.code}`);
    state.names[s.code] = s.name;
    const f = s.fundamentals || {};
    const q = state.quotes[state.code] || {};
    $("#info").innerHTML = `
      <dt>市場</dt><dd>${s.market === "otc" ? "上櫃" : "上市"}</dd>
      <dt>本益比</dt><dd>${fmt(f.pe)}</dd>
      <dt>股價淨值比</dt><dd>${fmt(f.pb)}</dd>
      <dt>殖利率</dt><dd>${fmt(f.dividend_yield)}%</dd>
      <dt>月營收年增</dt><dd class="${cls(f.rev_yoy)}">${fmt(f.rev_yoy)}%（${f.rev_month || "-"}）</dd>
      <dt>月營收月增</dt><dd class="${cls(f.rev_mom)}">${fmt(f.rev_mom)}%</dd>
      <dt>漲停 / 跌停</dt><dd>${fmt(q.limit_up)} / ${fmt(q.limit_down)}</dd>`;
  } catch {
    $("#info").innerHTML = `<dt>資料</dt><dd>尚未收集</dd>`;
  }
}

/* ---------- 自選股 ---------- */
function renderWatch() {
  $("#watch-table tbody").innerHTML = state.watch.map((c) => {
    const q = state.quotes[c] || {};
    return `<tr data-code="${c}" class="${c === state.code ? "sel" : ""}">
      <td>${c}<div class="nm">${q.name || state.names[c] || ""}</div></td>
      <td class="num ${cls(q.change)}">${fmt(q.price)}<div>${q.pct == null ? "" : sign(q.pct) + fmt(Math.abs(q.pct)) + "%"}</div></td></tr>`;
  }).join("");
}
$("#watch-table").addEventListener("click", (e) => {
  const tr = e.target.closest("tr");
  if (tr) selectStock(tr.dataset.code);
});
$("#watch-table").addEventListener("contextmenu", async (e) => {
  const tr = e.target.closest("tr");
  if (!tr) return;
  e.preventDefault();
  if (!confirm(`從自選股移除 ${tr.dataset.code}？`)) return;
  state.watch = state.watch.filter((c) => c !== tr.dataset.code);
  await api("/api/watchlist", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ codes: state.watch }) });
  renderWatch();
});

async function addWatch(code) {
  if (state.watch.includes(code)) return;
  state.watch.push(code);
  await api("/api/watchlist", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ codes: state.watch }) });
  renderWatch();
}

async function selectStock(code) {
  state.code = code;
  showView("watch");
  renderWatch();
  try {
    state.kline = await api(`/api/kline/${code}`);
  } catch {
    state.kline = [];
  }
  drawKline();
  renderQuote();
  renderInfo();
  refreshQuotes();
}

/* ---------- 搜尋 ---------- */
let searchTimer;
$("#search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  const v = e.target.value.trim();
  if (!v) return $("#search-results").classList.add("hidden");
  searchTimer = setTimeout(async () => {
    const rows = await api(`/api/stocks?q=${encodeURIComponent(v)}`);
    const list = rows.length ? rows : /^\d{4}$/.test(v) ? [{ code: v, name: "（尚未收集，可直接看即時報價）" }] : [];
    $("#search-results").innerHTML = list.map((r) =>
      `<li data-code="${r.code}"><span>${r.code} ${r.name}</span><button data-add="${r.code}">＋自選</button></li>`).join("");
    $("#search-results").classList.toggle("hidden", !list.length);
  }, 200);
});
$("#search-results").addEventListener("click", (e) => {
  const add = e.target.dataset.add;
  if (add) { e.stopPropagation(); addWatch(add); return; }
  const li = e.target.closest("li");
  if (!li) return;
  $("#search-results").classList.add("hidden");
  $("#search").value = "";
  selectStock(li.dataset.code);
});
$("#search").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("#search-results li")?.click();
});
document.addEventListener("click", (e) => { if (!e.target.closest(".search")) $("#search-results").classList.add("hidden"); });

/* ---------- 盤後分析 ---------- */
async function loadAnalysis() {
  const m = await api("/api/market").catch(() => ({}));
  if (!m.date) {
    $("#market-card").innerHTML = `<div class="empty">還沒有盤後資料。請先在終端機執行 <code>python -m stockapp.collector backfill --days 120</code>。</div>`;
  } else {
    $("#market-card").innerHTML = `<h2>${m.date} 盤勢 <span class="tone ${m.tone}">${m.tone}</span></h2>
      <div>加權指數 <b class="${cls(m.taiex_change)}">${fmt(m.taiex)} ${sign(m.taiex_change)}${fmt(Math.abs(m.taiex_change ?? 0))}</b>
      　成交金額 ${m.total_value ? fmt(m.total_value / 1e8, 0) + " 億" : "-"}
      　外資近 5 日 ${m.foreign_amt_5d != null ? `<b class="${cls(m.foreign_amt_5d)}">${fmt(m.foreign_amt_5d / 1e8, 1)} 億</b>` : "-"}</div>
      <div class="signals">${(m.signals || []).map((s) => `<span>${s}</span>`).join("")}</div>`;
  }
  await loadPicks();
}

async function loadPicks() {
  const ms = $("#min-score").value, qo = $("#quality-only").checked;
  const r = await api(`/api/screen?min_score=${ms}&quality_only=${qo}`).catch((e) => ({ picks: [], note: e.message }));
  $("#screen-meta").textContent = r.note || `資料日 ${r.date}，符合條件 ${r.candidates} 檔，顯示前 ${r.picks.length} 檔，可作為下一個交易日的觀察名單`;
  $("#picks tbody").innerHTML = r.picks.length ? r.picks.map((p, i) => `<tr data-code="${p.code}">
    <td>${i + 1}</td><td>${p.code} ${p.name}${p.quality ? '<span class="badge">績優</span>' : ""}</td>
    <td>${fmt(p.close)}</td><td class="${cls(p.change)}">${fmt(p.pct)}</td><td><b>${p.score}</b></td>
    <td>${p.trust_streak || "-"}</td><td>${p.foreign_streak || "-"}</td><td class="${cls(p.inst5)}">${fmtInt(p.inst5)}</td>
    <td class="${cls(p.rev_yoy)}">${p.rev_yoy == null ? "-" : fmt(p.rev_yoy, 1) + "%"}</td><td>${fmt(p.pe, 1)}</td>
    <td class="reasons">${p.reasons.map((x) => `<div class="${x.pts < 0 ? "neg" : ""}">${x.pts > 0 ? "+" : ""}${x.pts} ${x.text}</div>`).join("")}</td></tr>`).join("")
    : `<tr><td colspan="11" class="empty">${r.note || "今天沒有符合條件的股票，可以降低最低分數再試"}</td></tr>`;
}
$("#picks").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-code]");
  if (tr) selectStock(tr.dataset.code);
});
$("#rescreen").addEventListener("click", loadPicks);

/* ---------- 其他 ---------- */
function showView(v) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
  $("#view-watch").classList.toggle("hidden", v !== "watch");
  $("#view-analysis").classList.toggle("hidden", v !== "analysis");
  if (v === "analysis") loadAnalysis();
}
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));

let lastStatus = {};
async function setStatus(err) {
  try { lastStatus = await api("/api/status"); } catch {}
  const s = lastStatus;
  $("#status").innerHTML = `${err ? `<span class="up">${err}</span>` : ""}
    <span>${isMarketOpen() ? "盤中・每 5 秒更新" : "收盤"}</span>
    <span>盤後資料：${s.latest_date || "無"}（${s.trading_days || 0} 天）</span>
    ${s.running ? "<span>收集中…</span>" : `<button id="collect">收集今日資料</button>`}
    ${s.error ? `<span class="up" title="${s.error}">上次收集失敗</span>` : ""}`;
  $("#collect")?.addEventListener("click", async () => { await api("/api/collect", { method: "POST" }); setTimeout(setStatus, 500); });
}

(async function init() {
  state.watch = await api("/api/watchlist");
  await setStatus();
  selectStock(state.watch[0] || "2330");
  let tick = 0;
  setInterval(() => {
    tick++;
    if (isMarketOpen() || tick % 12 === 0) refreshQuotes();
  }, 5000);
})();
