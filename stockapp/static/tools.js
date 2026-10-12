/* 新世紀類看盤工具：週月 K、還原、技術指標副圖、自設公式、模擬、疊圖、排行、類股、條件選股、
   個股財務、除權息行事曆、自設快速鍵。
   在 app.js 之前載入，只定義函式；app.js 的 init() 會呼叫 Tools.init()，之後才用到 app.js 的全域變數。 */
const Tools = (() => {
  const PALETTE = ["#f5c542", "#4ea1ff", "#b48cff", "#ff8a65", "#4dd0e1", "#aed581"];
  const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const LS = {
    get(k, d) { try { const v = localStorage.getItem("tools." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("tools." + k, JSON.stringify(v)); } catch {} },
  };
  const opt = {
    period: "D", adjusted: LS.get("adjusted", false), overlay: LS.get("overlay", "MA"),
    ind1: LS.get("ind1", "KD"), ind2: LS.get("ind2", "MACD"), expr: LS.get("expr", ""),
    sim: "", taiex: false, cmp: "",
  };
  let meta = { indicators: [], presets: [], formula_help: [] };
  let chartData = null;
  const panes = {};
  let overlaySeries = [], cmpSeries = [];

  /* ---------- 看盤圖 ---------- */
  function qs(code) {
    const names = [opt.overlay, opt.ind1, opt.ind2].filter((x) => x && x !== "FORMULA");
    const p = new URLSearchParams({ period: opt.period, adjusted: opt.adjusted, ind: names.join(",") });
    if (opt.expr && (opt.ind1 === "FORMULA" || opt.ind2 === "FORMULA")) p.set("expr", opt.expr);
    if (opt.sim) p.set("sim", opt.sim);
    return `/api/chart/${code}?${p}`;
  }

  let loadedCode = null;
  async function loadChart(code) {
    if (code !== loadedCode) {  // 換股票時清掉上一檔的模擬價
      loadedCode = code;
      opt.sim = "";
      $("#sim").value = "";
    }
    try {
      chartData = await api(qs(code));
      $("#sim").classList.remove("bad");
    } catch (e) {
      if (opt.sim) { $("#sim").classList.add("bad"); $("#sim").title = e.message; }
      chartData = { bars: [], indicators: {} };
    }
    return chartData.bars;
  }

  async function reload() {
    if (!state.code) return;
    state.kline = await loadChart(state.code);
    drawKline();
    draw();
  }

  const whitespace = (bars, vals, map = (v) => v) => bars.map((b, i) =>
    vals[i] == null ? { time: b.date } : { time: b.date, value: map(vals[i]) });

  function makePane(id) {
    const c = LC.createChart($(`#chart-${id}`), subOpts);
    charts.push(c);
    c.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (syncing || !range) return;
      syncing = true;
      charts.forEach((o) => o !== c && o.timeScale().setVisibleLogicalRange(range));
      syncing = false;
    });
    c.subscribeCrosshairMove((p) => paneLegend(id, p.time));
    panes[id] = { chart: c, series: [], lines: {} };
  }

  function clearSeries(chart, list) {
    list.forEach((s) => chart.removeSeries(s));
    list.length = 0;
  }

  function drawPane(id, key) {
    const pane = panes[id];
    clearSeries(pane.chart, pane.series);
    pane.lines = {};
    const bars = chartData?.bars || [];
    let lines = {};
    if (key === "FORMULA") {
      const f = chartData?.formula || {};
      $("#expr-msg").textContent = f.error ? `公式錯誤：${f.error}` : opt.expr ? "" : "請在上方輸入公式";
      lines = f.lines || {};
    } else {
      lines = chartData?.indicators?.[key]?.lines || {};
    }
    Object.entries(lines).forEach(([name, vals], i) => {
      const hist = name === "OSC" || name === "量";
      const color = PALETTE[i % PALETTE.length];
      const s = hist
        ? pane.chart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false, priceFormat: { type: "price", precision: 2, minMove: 0.01 } })
        : pane.chart.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      s.setData(hist
        ? bars.map((b, j) => vals[j] == null ? { time: b.date } : { time: b.date, value: vals[j], color: (vals[j] >= 0 ? UP : DOWN) + "aa" })
        : whitespace(bars, vals));
      pane.series.push(s);
      pane.lines[name] = { vals, color: hist ? "var(--muted)" : color };
    });
    paneLegend(id);
  }

  function paneLegend(id, time) {
    const pane = panes[id];
    const bars = chartData?.bars || [];
    let idx = time ? bars.findIndex((b) => b.date === time) : bars.length - 1;
    if (idx < 0) idx = bars.length - 1;
    $(`#${id}-legend`).innerHTML = Object.entries(pane.lines).map(([n, l]) =>
      `<b style="color:${l.color}">${esc(n)} ${fmt(l.vals[idx])}</b>`).join("");
  }

  function drawOverlay() {
    clearSeries(mainChart, overlaySeries);
    maSeries.forEach((s) => s.applyOptions({ visible: opt.overlay === "MA" }));
    if (opt.overlay !== "BOLL" && opt.overlay !== "SAR") return;
    const bars = chartData?.bars || [];
    const lines = chartData?.indicators?.[opt.overlay]?.lines || {};
    Object.entries(lines).forEach(([name, vals], i) => {
      const s = opt.overlay === "SAR"
        ? mainChart.addLineSeries({ color: PALETTE[2], lineWidth: 1, pointMarkersVisible: true, lineVisible: false, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
        : mainChart.addLineSeries({ color: PALETTE[i], lineWidth: 1, lineStyle: i === 1 ? 2 : 0, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      s.setData(whitespace(bars, vals));
      overlaySeries.push(s);
    });
  }

  function markSim() {
    const k = chartData?.bars || [];
    if (!k.some((b) => b.sim)) return;
    candles.setData(k.map((r) => {
      const bar = { time: r.date, open: r.open ?? r.close, high: r.high ?? r.close, low: r.low ?? r.close, close: r.close };
      return r.sim ? { ...bar, color: "#8b95a3", wickColor: "#8b95a3", borderColor: "#c9d1d9" } : bar;
    }));
  }

  async function drawCompare() {
    clearSeries(mainChart, cmpSeries);
    const codes = [...(opt.taiex ? ["TAIEX"] : []), ...opt.cmp.split(/[,，\s]+/).filter((c) => /^\d{4,6}$/.test(c))];
    $("#cmp-legend")?.remove();
    if (!codes.length || !state.kline.length) return;
    const first = state.kline[0].date;
    const r = await api(`/api/compare?codes=${codes.join(",")}&limit=${opt.period === "D" ? 600 : 5000}`).catch(() => ({}));
    const own = state.kline.find((b) => b.close)?.close;
    const legend = [];
    Object.entries(r).forEach(([code, d], i) => {
      const pts = d.points.filter((p) => p.date >= first && p.close);
      if (!pts.length || !own) return;
      // 換算成跟本檔同一起點的價格，方便直接在主圖比較走勢
      const base = pts[0].close;
      const color = PALETTE[(i + 3) % PALETTE.length];
      const s = mainChart.addLineSeries({ color, lineWidth: 1, lineStyle: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      const dates = new Set(state.kline.map((b) => b.date));
      s.setData(pts.filter((p) => dates.has(p.date)).map((p) => ({ time: p.date, value: +(p.close / base * own).toFixed(2) })));
      cmpSeries.push(s);
      const chg = (pts[pts.length - 1].close / base - 1) * 100;
      legend.push(`<b style="color:${color}">${esc(d.name || code)} ${chg >= 0 ? "+" : ""}${fmt(chg)}%</b>`);
    });
    if (legend.length) {
      const k = state.kline.filter((b) => b.close);
      const ownChg = (k[k.length - 1].close / k[0].close - 1) * 100;
      $("#legend").insertAdjacentHTML("afterend", `<div id="cmp-legend" class="legend">疊圖（同起點）：<b>本檔 ${ownChg >= 0 ? "+" : ""}${fmt(ownChg)}%</b>${legend.join("")}</div>`);
    }
  }

  function draw() {
    markSim();
    drawOverlay();
    drawPane("ind1", opt.ind1);
    drawPane("ind2", opt.ind2);
    drawCompare();
    // 副圖重畫後再對齊一次可視範圍（週月 K 根數少，從頭顯示）
    const n = state.kline.length;
    if (n) mainChart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 120), to: n + 3 });
  }

  // 盤中即時更新只適用於日 K、沒有模擬資料時
  const liveMergeOK = () => opt.period === "D" && !opt.sim;

  function fillSelects() {
    const sub = meta.indicators.filter((x) => !x.overlay);
    const html = sub.map((x) => `<option value="${x.key}">${esc(x.title)}${x.params ? `（${esc(x.params)}）` : ""}</option>`).join("") +
      `<option value="FORMULA">自設公式</option>`;
    ["ind1", "ind2"].forEach((id) => { $(`#${id}`).innerHTML = html; $(`#${id}`).value = opt[id]; });
    $("#overlay").value = opt.overlay;
    $("#adjusted").checked = opt.adjusted;
    $("#expr").value = opt.expr;
  }

  function setPeriod(p) {
    opt.period = p;
    document.querySelectorAll("#period button").forEach((b) => b.classList.toggle("active", b.dataset.p === p));
    reload();
  }

  function bindToolbar() {
    document.querySelectorAll("#period button").forEach((b) => b.addEventListener("click", () => setPeriod(b.dataset.p)));
    $("#adjusted").addEventListener("change", (e) => { opt.adjusted = e.target.checked; LS.set("adjusted", opt.adjusted); reload(); });
    $("#overlay").addEventListener("change", (e) => { opt.overlay = e.target.value; LS.set("overlay", opt.overlay); reload(); });
    ["ind1", "ind2"].forEach((id) => $(`#${id}`).addEventListener("change", (e) => {
      opt[id] = e.target.value; LS.set(id, opt[id]);
      if (opt[id] === "FORMULA") $("#formula-bar").classList.remove("hidden");
      reload();
    }));
    $("#cmp-taiex").addEventListener("change", (e) => { opt.taiex = e.target.checked; drawCompare(); });
    $("#cmp-codes").addEventListener("change", (e) => { opt.cmp = e.target.value; drawCompare(); });
    $("#sim").addEventListener("change", (e) => { opt.sim = e.target.value.trim(); reload(); });
    $("#formula-toggle").addEventListener("click", () => $("#formula-bar").classList.toggle("hidden"));
    const applyExpr = () => {
      opt.expr = $("#expr").value.trim();
      LS.set("expr", opt.expr);
      if (opt.ind2 !== "FORMULA" && opt.ind1 !== "FORMULA") { opt.ind2 = "FORMULA"; $("#ind2").value = "FORMULA"; }
      reload();
    };
    $("#expr-apply").addEventListener("click", applyExpr);
    $("#expr").addEventListener("keydown", (e) => { if (e.key === "Enter") applyExpr(); });
    $("#keys-btn").addEventListener("click", openKeys);
  }

  /* ---------- 排行・類股 ---------- */
  let rankKind = "gain";
  const RANK_COLS = {
    pct: "漲跌%", volume: "成交量(張)", value: "成交值(億)", amplitude: "振幅%", vol_ratio: "量比",
    foreign: "外資(張)", trust: "投信(張)", dealer: "自營(張)", inst: "法人合計(張)", margin_chg: "融資增減(張)", short_chg: "融券增減(張)",
  };
  const cell = (k, v) => k === "value" ? fmt(v / 1e8, 2) : ["pct", "amplitude", "vol_ratio"].includes(k) ? fmt(v) : fmtInt(v);

  async function loadRank() {
    const r = await api(`/api/rank?kind=${rankKind}&market=${$("#rank-market").value}`).catch(() => null);
    if (!r) return;
    $("#rank-date").textContent = r.date ? `資料日 ${r.date}` : "還沒有盤後資料";
    $("#rank-kinds").innerHTML = r.kinds.map((k) => `<button data-k="${k.key}" class="${k.key === rankKind ? "active" : ""}">${esc(k.label)}</button>`).join("");
    const cols = ["pct", "volume", "value", r.field].filter((c, i, a) => a.indexOf(c) === i);
    $("#rank-table thead").innerHTML = `<tr><th>#</th><th>股票</th><th>收盤</th>${cols.map((c) => `<th>${RANK_COLS[c]}</th>`).join("")}<th>產業</th></tr>`;
    $("#rank-table tbody").innerHTML = r.rows.length ? r.rows.map((s, i) => `<tr data-code="${s.code}">
      <td>${i + 1}</td><td>${s.code} ${esc(s.name)}</td><td>${fmt(s.close)}</td>
      ${cols.map((c) => `<td class="${["pct", "foreign", "trust", "dealer", "inst", "margin_chg", "short_chg"].includes(c) ? cls(s[c]) : ""}">${cell(c, s[c])}</td>`).join("")}
      <td>${esc(s.industry || "")}</td></tr>`).join("") : `<tr><td colspan="9" class="empty">沒有資料</td></tr>`;
  }

  async function loadIndustries() {
    const r = await api("/api/industries").catch(() => null);
    if (!r) return;
    if (!r.classified) {
      $("#industry-grid").innerHTML = `<div class="empty">還沒有產業分類，請執行 <code>python -m stockapp.tools collect</code></div>`;
      return;
    }
    $("#industry-grid").innerHTML = r.industries.map((x) => `<button class="ind-tile ${cls(x.pct)}" data-ind="${esc(x.industry)}">
      <b>${esc(x.industry)}</b><span>${x.pct == null ? "-" : (x.pct > 0 ? "+" : "") + fmt(x.pct) + "%"}</span>
      <small>${x.up}漲 ${x.down}跌・${fmt(x.value / 1e8, 0)}億</small></button>`).join("");
  }

  async function loadIndustry(name) {
    const r = await api(`/api/industry?name=${encodeURIComponent(name)}`);
    const t = $("#industry-table");
    t.classList.remove("hidden");
    t.querySelector("thead").innerHTML = `<tr><th colspan="7" style="text-align:left">${esc(name)}（${r.rows.length} 檔）</th></tr>
      <tr><th>股票</th><th>收盤</th><th>漲跌%</th><th>成交量(張)</th><th>外資</th><th>投信</th><th>融資增減</th></tr>`;
    t.querySelector("tbody").innerHTML = r.rows.map((s) => `<tr data-code="${s.code}"><td>${s.code} ${esc(s.name)}</td><td>${fmt(s.close)}</td>
      <td class="${cls(s.pct)}">${fmt(s.pct)}</td><td>${fmtInt(s.volume)}</td><td class="${cls(s.foreign)}">${fmtInt(s.foreign)}</td>
      <td class="${cls(s.trust)}">${fmtInt(s.trust)}</td><td class="${cls(s.margin_chg)}">${fmtInt(s.margin_chg)}</td></tr>`).join("");
  }

  /* ---------- 條件選股 ---------- */
  function renderScreenerMeta() {
    $("#presets").innerHTML = meta.presets.map((p) => `<button data-expr="${esc(p.expr)}" title="${esc(p.expr)}">${esc(p.name)}</button>`).join("");
    $("#formula-help").innerHTML = meta.formula_help.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
    if (!$("#screen-expr").value) $("#screen-expr").value = LS.get("screenExpr", "");
  }

  async function runScreen() {
    const expr = $("#screen-expr").value.trim();
    if (!expr) return;
    LS.set("screenExpr", expr);
    $("#screen-msg").textContent = "篩選中…";
    try {
      const r = await api(`/api/formula/screen?expr=${encodeURIComponent(expr)}&market=${$("#screen-market").value}`);
      $("#screen-msg").textContent = r.date ? `資料日 ${r.date}，掃描 ${r.scanned} 檔，符合 ${r.count} 檔${r.count > r.rows.length ? `（顯示成交量前 ${r.rows.length} 檔）` : ""}` : r.note;
      const names = r.rows.length ? Object.keys(r.rows[0].values) : [];
      $("#screen-table thead").innerHTML = `<tr><th>#</th><th>股票</th><th>收盤</th><th>漲跌%</th><th>成交量(張)</th>${names.map((n) => `<th>${esc(n)}</th>`).join("")}<th></th></tr>`;
      $("#screen-table tbody").innerHTML = r.rows.map((s, i) => `<tr data-code="${s.code}"><td>${i + 1}</td><td>${s.code} ${esc(s.name)}</td>
        <td>${fmt(s.close)}</td><td class="${cls(s.pct)}">${fmt(s.pct)}</td><td>${fmtInt(s.volume)}</td>
        ${names.map((n) => `<td>${fmt(s.values[n])}</td>`).join("")}<td><button data-add="${s.code}">＋自選</button></td></tr>`).join("")
        || `<tr><td colspan="6" class="empty">沒有符合的股票</td></tr>`;
    } catch (e) {
      $("#screen-msg").innerHTML = `<span class="up">${esc(e.message)}</span>`;
    }
  }

  /* ---------- 個股資料、除權息 ---------- */
  const m = (v) => (v == null ? "-" : fmt(v / 1000, 0));  // 千元 -> 百萬元

  async function loadCompany() {
    const code = state.code;
    const [p, fin, cal] = await Promise.all([api(`/api/profile/${code}`).catch(() => ({})), api(`/api/financials/${code}`).catch(() => []),
      api(`/api/calendar?code=${code}`).catch(() => ({ upcoming: [], history: [] }))]);
    const nx = p.next_exright;
    $("#company-head").innerHTML = `<h2>${code} ${esc(state.names[code] || "")}</h2>
      <div>產業：<b>${esc(p.industry || "未分類")}</b>${nx ? `　下次除權息：<b>${nx.date}</b> ${esc(nx.kind)}${nx.cash_div ? ` 現金 ${fmt(nx.cash_div, 4)} 元` : ""}` : ""}</div>
      ${p.peers?.length ? `<div class="presets" style="margin-top:8px">同產業：${p.peers.map((x) => `<button data-code="${x.code}">${x.code} ${esc(x.name)}</button>`).join("")}</div>` : ""}`;
    $("#fin-table thead").innerHTML = `<tr><th>季別</th><th>營收</th><th>毛利率%</th><th>營益率%</th><th>淨利率%</th><th>稅後淨利</th><th>EPS</th><th>年初至今 EPS</th><th>負債比%</th><th>每股淨值</th></tr>`;
    $("#fin-table tbody").innerHTML = fin.length ? fin.map((f) => `<tr><td>${f.year} ${f.basis === "累計" ? `Q1–Q${f.quarter} 累計` : `Q${f.quarter}`}</td><td>${m(f.revenue_q)}</td>
      <td>${fmt(f.gross_margin)}</td><td>${fmt(f.op_margin)}</td><td>${fmt(f.net_margin)}</td><td class="${cls(f.net_income_q)}">${m(f.net_income_q)}</td>
      <td class="${cls(f.eps_q)}"><b>${fmt(f.eps_q)}</b></td><td>${fmt(f.eps)}</td><td>${fmt(f.debt_ratio)}</td><td>${fmt(f.bvps)}</td></tr>`).join("")
      : `<tr><td colspan="10" class="empty">還沒有季報資料。公開資料只提供最新一季，執行 <code>python -m stockapp.tools collect</code> 後每季累積。</td></tr>`;
    $("#exr-table thead").innerHTML = `<tr><th>除權息日</th><th>權/息</th><th>前收盤</th><th>參考價</th><th>權值+息值</th></tr>`;
    $("#exr-table tbody").innerHTML = cal.history.length ? cal.history.map((x) => `<tr><td>${x.date}</td><td>${esc(x.kind)}</td>
      <td>${fmt(x.prev_close)}</td><td>${fmt(x.ref_price)}</td><td>${fmt(x.value, 4)}</td></tr>`).join("")
      : `<tr><td colspan="5" class="empty">沒有除權息紀錄（上市股票執行 <code>python -m stockapp.tools exrights</code> 回補）</td></tr>`;
  }

  async function loadCalendar() {
    const r = await api("/api/calendar").catch(() => ({ upcoming: [] }));
    $("#cal-table thead").innerHTML = `<tr><th>日期</th><th>股票</th><th>權/息</th><th>現金股利(元)</th><th>無償配股率</th><th>現增配股率</th><th>認購價</th><th></th></tr>`;
    const cash = (x) => x.cash_div ? fmt(x.cash_div, 4) : /息/.test(x.kind) ? '<span class="hint">未公告</span>' : "-";
    const ratio = (v) => (v ? fmt(v, 4) : "-");
    $("#cal-table tbody").innerHTML = r.upcoming.length ? r.upcoming.map((x) => `<tr data-code="${x.code}"><td>${x.date}</td>
      <td>${x.code} ${esc(x.name)}</td><td>${esc(x.kind)}</td><td>${cash(x)}</td><td>${ratio(x.stock_ratio)}</td>
      <td>${ratio(x.cash_capital_ratio)}</td><td>${x.subscription_price ? fmt(x.subscription_price) : "-"}</td>
      <td><button data-add="${x.code}">＋自選</button></td></tr>`).join("")
      : `<tr><td colspan="8" class="empty">沒有資料，請先收集盤後資料或執行 <code>python -m stockapp.tools collect</code></td></tr>`;
  }

  function onView(v) {
    if (v === "rank") { loadRank(); loadIndustries(); }
    if (v === "screener") renderScreenerMeta();
    if (v === "company") loadCompany();
    if (v === "calendar") loadCalendar();
  }

  function bindPages() {
    // 各頁表格：點列看圖、點＋自選加入自選股
    ["#rank-table", "#industry-table", "#screen-table", "#cal-table", "#company-head"].forEach((sel) => $(sel).addEventListener("click", (e) => {
      const add = e.target.closest("[data-add]");
      if (add) { e.stopPropagation(); addWatch(add.dataset.add); add.textContent = "已加入"; return; }
      const row = e.target.closest("[data-code]");
      if (row) selectStock(row.dataset.code);
    }));
    $("#rank-kinds").addEventListener("click", (e) => { const b = e.target.closest("[data-k]"); if (b) { rankKind = b.dataset.k; loadRank(); } });
    $("#rank-market").addEventListener("change", loadRank);
    $("#industry-grid").addEventListener("click", (e) => { const b = e.target.closest("[data-ind]"); if (b) loadIndustry(b.dataset.ind); });
    $("#presets").addEventListener("click", (e) => { const b = e.target.closest("[data-expr]"); if (b) { $("#screen-expr").value = b.dataset.expr; runScreen(); } });
    $("#screen-run").addEventListener("click", runScreen);
    $("#screen-expr").addEventListener("keydown", (e) => { if (e.key === "Enter") runScreen(); });
  }

  /* ---------- 自設快速鍵 ---------- */
  const cycle = (id) => {
    const sel = $(`#${id}`);
    sel.selectedIndex = (sel.selectedIndex + 1) % sel.options.length;
    sel.dispatchEvent(new Event("change"));
  };
  const moveWatch = (d) => {
    const i = state.watch.indexOf(state.code);
    const n = state.watch.length;
    if (n) selectStock(state.watch[((i < 0 ? 0 : i + d) % n + n) % n]);
  };
  const ACTIONS = {
    prev: ["上一檔自選股", "ArrowUp", () => moveWatch(-1)],
    next: ["下一檔自選股", "ArrowDown", () => moveWatch(1)],
    day: ["日 K", "F2", () => setPeriod("D")],
    week: ["週 K", "F3", () => setPeriod("W")],
    month: ["月 K", "F4", () => setPeriod("M")],
    adjust: ["還原 K 線開關", "F6", () => { $("#adjusted").click(); }],
    ind1: ["切換副圖一指標", "F7", () => cycle("ind1")],
    ind2: ["切換副圖二指標", "F8", () => cycle("ind2")],
    overlay: ["切換主圖疊加", "F9", () => cycle("overlay")],
    formula: ["開關自設公式列", "F10", () => $("#formula-toggle").click()],
    watch: ["看盤頁", "Alt+1", () => showView("watch")],
    analysis: ["盤後分析頁", "Alt+2", () => showView("analysis")],
    rank: ["排行・類股頁", "Alt+3", () => showView("rank")],
    screener: ["條件選股頁", "Alt+4", () => showView("screener")],
    company: ["個股資料頁", "Alt+5", () => showView("company")],
    calendar: ["除權息頁", "Alt+6", () => showView("calendar")],
    addwatch: ["目前個股加入自選", "Insert", () => addWatch(state.code)],
    help: ["快速鍵說明", "?", () => openKeys()],
  };
  let keymap = { ...Object.fromEntries(Object.entries(ACTIONS).map(([k, v]) => [k, v[1]])), ...LS.get("keys", {}) };
  let capturing = null;

  const keyName = (e) => {
    if (["Shift", "Control", "Alt", "Meta"].includes(e.key)) return null;
    const k = e.code?.startsWith("Digit") && e.altKey ? e.code.slice(5) : e.key.length === 1 ? e.key.toUpperCase() : e.key;
    return (e.ctrlKey ? "Ctrl+" : "") + (e.altKey ? "Alt+" : "") + (k === "?" || k === "/" && e.shiftKey ? "?" : k);
  };

  function renderKeys() {
    $("#keys-table").innerHTML = Object.entries(ACTIONS).map(([id, [label]]) =>
      `<tr><td>${esc(label)}</td><td><button data-key="${id}" class="${capturing === id ? "active" : ""}">${capturing === id ? "請按新的鍵…" : esc(keymap[id])}</button></td></tr>`).join("");
  }
  function openKeys() { $("#keys-dialog").classList.remove("hidden"); renderKeys(); }

  function bindKeys() {
    $("#keys-table").addEventListener("click", (e) => { const b = e.target.closest("[data-key]"); if (b) { capturing = b.dataset.key; renderKeys(); } });
    $("#keys-close").addEventListener("click", () => { capturing = null; $("#keys-dialog").classList.add("hidden"); });
    $("#keys-reset").addEventListener("click", () => {
      keymap = Object.fromEntries(Object.entries(ACTIONS).map(([k, v]) => [k, v[1]]));
      LS.set("keys", {});
      renderKeys();
    });
    document.addEventListener("keydown", (e) => {
      const name = keyName(e);
      if (!name) return;
      if (capturing) {
        e.preventDefault();
        if (name !== "Escape") { keymap[capturing] = name; LS.set("keys", keymap); }
        capturing = null;
        return renderKeys();
      }
      if (name === "Escape") return $("#keys-dialog").classList.add("hidden");
      const typing = e.target.closest("input, select, textarea");
      if (typing) return;
      const action = Object.keys(keymap).find((k) => keymap[k] === name);
      if (action) { e.preventDefault(); return ACTIONS[action][2](); }
      // 直接打代號或名稱 → 跳到搜尋框
      if (!e.ctrlKey && !e.altKey && !e.metaKey && e.key.length === 1 && /[\w一-鿿]/.test(e.key)) {
        const s = $("#search");
        s.focus();
        s.value = "";
      }
    });
  }

  async function init() {
    makePane("ind1");
    makePane("ind2");
    meta = await api("/api/indicators").catch(() => meta);
    fillSelects();
    bindToolbar();
    bindPages();
    bindKeys();
  }

  return { init, loadChart, draw, reload, liveMergeOK, onView };
})();
