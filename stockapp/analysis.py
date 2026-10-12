"""盤後分析：大盤盤勢判讀、個股五面向評分（走勢、法人、大戶、新聞、基本面）、隔日偏多／偏空名單。

規則式評分，每個加減分都附上理由，方便自己檢查為什麼入選。
這是技術、籌碼、基本面的條件篩選，不是投資建議。
"""
import json
from collections import defaultdict
from datetime import date, datetime, timedelta

LOOKBACK = 80  # 讀取的交易日數


def _ma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def _rsi(closes, n=14):
    if len(closes) <= n:
        return None
    gains = losses = 0.0
    for a, b in zip(closes[-n - 1:-1], closes[-n:]):
        d = b - a
        gains += max(d, 0)
        losses += max(-d, 0)
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100 - 100 / (1 + rs)


def _streak(xs):
    """最後連續為正的天數。"""
    n = 0
    for x in reversed(xs):
        if x is not None and x > 0:
            n += 1
        else:
            break
    return n


def trading_dates(conn, limit=LOOKBACK, until=None):
    q = "SELECT DISTINCT date FROM daily_price"
    args = []
    if until:
        q += " WHERE date<=?"
        args.append(until)
    q += " ORDER BY date DESC LIMIT ?"
    args.append(limit)
    return sorted(r["date"] for r in conn.execute(q, args))


def latest_date(conn):
    r = conn.execute("SELECT MAX(date) d FROM daily_price").fetchone()
    return r["d"]


NEWS_DAYS = 3        # 新聞納入評分的天數
BUY_MIN = 4          # 偏多門檻
SELL_MAX = -3        # 偏空門檻


def _load(conn, dates):
    lo = dates[0]
    hi = dates[-1]
    prices = defaultdict(dict)
    for r in conn.execute("SELECT * FROM daily_price WHERE date BETWEEN ? AND ?", (lo, hi)):
        prices[r["code"]][r["date"]] = r
    inst = defaultdict(dict)
    for r in conn.execute("SELECT * FROM institutional WHERE date BETWEEN ? AND ?", (lo, hi)):
        inst[r["code"]][r["date"]] = r
    margin = defaultdict(dict)
    for r in conn.execute("SELECT * FROM margin WHERE date BETWEEN ? AND ?", (lo, hi)):
        margin[r["code"]][r["date"]] = r
    names = {r["code"]: (r["name"], r["market"]) for r in conn.execute("SELECT * FROM stocks")}
    fund = {r["code"]: r for r in conn.execute("SELECT * FROM fundamentals")}
    return prices, inst, margin, names, fund


def _load_extra(conn, dates):
    """集保大戶（週資料）與近幾日新聞。"""
    hi = dates[-1]
    holders = defaultdict(list)
    for r in conn.execute("SELECT * FROM holders WHERE date BETWEEN ? AND ? ORDER BY date", (dates[0], hi)):
        holders[r["code"]].append(r)
    d = date.fromisoformat(hi)
    lo_ts = (d - timedelta(days=NEWS_DAYS)).isoformat()
    hi_ts = (d + timedelta(days=4)).isoformat()  # 含盤後到下一個交易日開盤前的新聞
    news = defaultdict(list)
    for r in conn.execute("SELECT * FROM news WHERE code!='' AND published>=? AND published<? ORDER BY published DESC",
                          (lo_ts, hi_ts)):
        news[r["code"]].append(r)
    return holders, news


def verdict(score):
    return "偏多" if score >= BUY_MIN else "偏空" if score <= SELL_MAX else "中性"


def evaluate(code, dates, prices, inst, margin, fund, holders=(), news=()):
    """對一檔股票做五個面向的評分：走勢、法人、大戶、新聞、基本面。

    回傳 dict（score、dims、reasons、metrics），資料不足 20 天或當天沒交易則回傳 None。
    每條加減分都附理由與所屬面向，方便檢查。"""
    rows = [prices[d] for d in dates if d in prices and prices[d]["close"]]
    if len(rows) < 20 or rows[-1]["date"] != dates[-1]:
        return None
    closes = [r["close"] for r in rows]
    vols = [(r["volume"] or 0) / 1000 for r in rows]  # 張
    last = rows[-1]
    close = last["close"]
    avg_vol20 = _ma(vols, 20)
    ma5, ma20, ma60 = _ma(closes, 5), _ma(closes, 20), _ma(closes, 60)

    dims = {"trend": 0.0, "chips": 0.0, "holders": 0.0, "news": 0.0, "fundamental": 0.0}
    reasons = []

    def add(dim, pts, why):
        dims[dim] += pts
        reasons.append({"dim": dim, "pts": pts, "text": why})

    # --- 走勢 ---
    if close > ma5 > ma20:
        add("trend", 2, "均線多頭排列（收盤 > MA5 > MA20）")
    elif close < ma5 < ma20:
        add("trend", -2, "均線空頭排列（收盤 < MA5 < MA20）")
    elif close < ma20:
        add("trend", -1, "收盤跌破月線 MA20")
    if ma60:
        if close > ma60:
            add("trend", 1, "站上季線 MA60")
        else:
            add("trend", -1, "位於季線 MA60 之下")
    chg = last["change"] or 0
    avg_vol5_prev = sum(vols[-6:-1]) / 5 if len(vols) >= 6 else None
    if avg_vol5_prev and vols[-1] >= 1.5 * avg_vol5_prev:
        ratio = vols[-1] / avg_vol5_prev
        if chg > 0:
            add("trend", 1, f"價漲量增（量為 5 日均量 {ratio:.1f} 倍）")
        elif chg < 0:
            add("trend", -1, f"價跌量增（量為 5 日均量 {ratio:.1f} 倍），賣壓湧現")
    if close >= max(r["high"] or r["close"] for r in rows[-20:]):
        add("trend", 1, "收盤創 20 日新高")
    elif close <= min(r["low"] or r["close"] for r in rows[-20:]):
        add("trend", -1, "收盤創 20 日新低")
    rsi = _rsi(closes)
    bias20 = (close - ma20) / ma20 * 100
    if rsi is not None and rsi >= 80:
        add("trend", -1, f"RSI {rsi:.0f} 短線過熱")
    if bias20 >= 10:
        add("trend", -1, f"月線乖離 {bias20:.1f}% 偏大，追價風險高")

    # --- 法人 / 融資 ---
    ins = [inst.get(d) for d in dates[-10:]]
    fx = [(r["foreign_net"] if r else None) for r in ins]
    tr = [(r["trust_net"] if r else None) for r in ins]
    tot5 = sum((r["total_net"] or 0) for r in ins[-5:] if r) / 1000  # 張
    fx_streak, tr_streak = _streak(fx), _streak(tr)
    fx_sell = _streak([-x if x is not None else None for x in fx])
    tr_sell = _streak([-x if x is not None else None for x in tr])
    if fx_streak >= 3:
        add("chips", 1, f"外資連買 {fx_streak} 天")
    elif fx_sell >= 3:
        add("chips", -1, f"外資連賣 {fx_sell} 天")
    elif fx and fx[-1] and fx[-1] > 0:
        add("chips", 0.5, "外資今日買超")
    if tr_streak >= 3:
        add("chips", 2, f"投信連買 {tr_streak} 天")
    elif tr_sell >= 3:
        add("chips", -2, f"投信連賣 {tr_sell} 天")
    elif tr and tr[-1] and tr[-1] > 0:
        add("chips", 0.5, "投信今日買超")
    inst_ratio = tot5 / (avg_vol20 * 5) * 100 if avg_vol20 else 0
    if inst_ratio >= 10:
        add("chips", 1, f"法人 5 日買超占成交量 {inst_ratio:.0f}%")
    elif inst_ratio <= -10:
        add("chips", -1, f"法人 5 日賣超占成交量 {-inst_ratio:.0f}%")

    mg = [margin.get(d) for d in dates[-6:]]
    mg = [r["margin_balance"] for r in mg if r and r["margin_balance"] is not None]
    margin_chg = None
    if len(mg) >= 2 and mg[0]:
        margin_chg = (mg[-1] - mg[0]) / mg[0] * 100
        up5 = len(closes) >= 6 and closes[-1] > closes[-6]
        if margin_chg < -3 and up5:
            add("chips", 1, f"股價上漲但融資減少 {-margin_chg:.1f}%，籌碼安定")
        elif margin_chg > 10:
            add("chips", -1, f"融資 5 日暴增 {margin_chg:.1f}%，散戶追價")
        elif margin_chg > 3 and not up5:
            add("chips", -1, f"股價走弱但融資增加 {margin_chg:.1f}%，籌碼凌亂")

    # --- 大戶（集保股權分散表，週資料） ---
    h = list(holders)
    big400 = big1000 = big_chg1 = big_chg4 = retail_chg1 = None
    if h:
        big400, big1000 = h[-1]["big400_pct"], h[-1]["big1000_pct"]
    if len(h) >= 2:
        big_chg1 = round(h[-1]["big400_pct"] - h[-2]["big400_pct"], 2)
        retail_chg1 = round(h[-1]["retail_pct"] - h[-2]["retail_pct"], 2)
        weeks = 4 if len(h) >= 5 else len(h) - 1
        base = h[-1 - weeks]
        big_chg4 = round(h[-1]["big400_pct"] - base["big400_pct"], 2)
        if big_chg1 >= 0.5:
            add("holders", 2, f"400 張大戶持股週增 {big_chg1:.2f} 個百分點（{big400:.1f}%）")
        elif big_chg1 >= 0.2:
            add("holders", 1, f"400 張大戶持股週增 {big_chg1:.2f} 個百分點（{big400:.1f}%）")
        elif big_chg1 <= -0.5:
            add("holders", -2, f"400 張大戶持股週減 {-big_chg1:.2f} 個百分點（{big400:.1f}%）")
        elif big_chg1 <= -0.2:
            add("holders", -1, f"400 張大戶持股週減 {-big_chg1:.2f} 個百分點（{big400:.1f}%）")
        if len(h) >= 3:
            if big_chg4 >= 1:
                add("holders", 1, f"大戶持股近 {weeks} 週累增 {big_chg4:.2f} 個百分點")
            elif big_chg4 <= -1:
                add("holders", -1, f"大戶持股近 {weeks} 週累減 {-big_chg4:.2f} 個百分點")
        if retail_chg1 <= -0.3 and big_chg1 > 0:
            add("holders", 0.5, f"散戶（50 張以下）持股減少 {-retail_chg1:.2f} 個百分點，籌碼集中")
        elif retail_chg1 >= 0.3 and big_chg1 < 0:
            add("holders", -0.5, f"散戶持股增加 {retail_chg1:.2f} 個百分點，籌碼分散")

    # --- 新聞（關鍵字情緒） ---
    nl = list(news)
    senti = round(sum(n["sentiment"] or 0 for n in nl), 2)
    if nl:
        pos = [n for n in nl if (n["sentiment"] or 0) > 0]
        neg = [n for n in nl if (n["sentiment"] or 0) < 0]
        head = lambda xs: f"〈{xs[0]['title'][:40]}〉" if xs else ""
        if senti >= 1.5:
            add("news", 2, f"近 {NEWS_DAYS} 日新聞 {len(nl)} 則明顯偏多，例如{head(pos)}")
        elif senti >= 0.5:
            add("news", 1, f"近 {NEWS_DAYS} 日新聞偏多，例如{head(pos)}")
        elif senti <= -1.5:
            add("news", -2, f"近 {NEWS_DAYS} 日新聞 {len(nl)} 則明顯偏空，例如{head(neg)}")
        elif senti <= -0.5:
            add("news", -1, f"近 {NEWS_DAYS} 日新聞偏空，例如{head(neg)}")

    # --- 基本面 ---
    f = fund.get(code)
    rev_yoy = pe = dy = None
    if f:
        rev_yoy, pe, dy = f["rev_yoy"], f["pe"], f["dividend_yield"]
        if rev_yoy is not None:
            if rev_yoy >= 20:
                add("fundamental", 2, f"月營收年增 {rev_yoy:.1f}%")
            elif rev_yoy > 0:
                add("fundamental", 1, f"月營收年增 {rev_yoy:.1f}%")
            elif rev_yoy <= -20:
                add("fundamental", -1, f"月營收年減 {-rev_yoy:.1f}%")
        if pe and 0 < pe <= 20:
            add("fundamental", 1, f"本益比 {pe:.1f} 不高")
        elif pe and pe > 40:
            add("fundamental", -0.5, f"本益比 {pe:.1f} 偏高")
        if dy and dy >= 4:
            add("fundamental", 1, f"殖利率 {dy:.1f}%")

    score = round(sum(dims.values()), 1)
    quality = bool(rev_yoy is not None and rev_yoy > 0 and pe and 0 < pe <= 25)
    metrics = {
        "close": close, "change": chg, "pct": round(chg / (close - chg) * 100, 2) if close != chg else None,
        "volume": round(vols[-1]), "avg_vol20": round(avg_vol20),
        "ma5": round(ma5, 2), "ma20": round(ma20, 2), "ma60": round(ma60, 2) if ma60 else None,
        "rsi": round(rsi, 1) if rsi is not None else None, "bias20": round(bias20, 2),
        "foreign_streak": fx_streak, "trust_streak": tr_streak,
        "foreign_sell_streak": fx_sell, "trust_sell_streak": tr_sell, "inst5": round(tot5),
        "margin_chg5": round(margin_chg, 2) if margin_chg is not None else None,
        "big400_pct": big400, "big1000_pct": big1000, "big400_chg1w": big_chg1, "big400_chg4w": big_chg4,
        "retail_chg1w": retail_chg1, "holders_date": h[-1]["date"] if h else None,
        "news_count": len(nl), "news_sentiment": senti,
        "rev_yoy": rev_yoy, "pe": pe, "dividend_yield": dy, "quality": quality,
    }
    return {"score": score, "dims": {k: round(v, 1) for k, v in dims.items()}, "reasons": reasons,
            "verdict": verdict(score), "metrics": metrics}


def score_stock(code, dates, prices, inst, margin, fund, holders=(), news=()):
    """舊版介面：回傳 (score, reasons, metrics) 或 None（不符合基本門檻：收盤 ≥ 10、均量 ≥ 1000 張、站上月線）。"""
    ev = evaluate(code, dates, prices, inst, margin, fund, holders, news)
    if not ev:
        return None
    m = ev["metrics"]
    if m["close"] < 10 or m["avg_vol20"] < 1000 or m["close"] <= m["ma20"]:
        return None
    return ev["score"], ev["reasons"], {**m, "dims": ev["dims"]}


def screen(conn, until=None, top=30, min_score=4, quality_only=False):
    dates = trading_dates(conn, until=until)
    if len(dates) < 20:
        return {"date": dates[-1] if dates else None, "picks": [],
                "note": f"資料只有 {len(dates)} 個交易日，至少需要 20 天，請先執行 backfill。"}
    prices, inst, margin, names, fund = _load(conn, dates)
    holders, news = _load_extra(conn, dates)
    picks = []
    for code, by_date in prices.items():
        res = score_stock(code, dates, by_date, inst.get(code, {}), margin.get(code, {}), fund,
                          holders.get(code, ()), news.get(code, ()))
        if not res:
            continue
        score, reasons, m = res
        if score < min_score or (quality_only and not m["quality"]):
            continue
        name, market = names.get(code, ("", None))
        picks.append({"code": code, "name": name, "market": market, "score": score, "reasons": reasons, **m})
    picks.sort(key=lambda p: (p["score"], p["inst5"] / max(p["avg_vol20"], 1)), reverse=True)
    return {"date": dates[-1], "picks": picks[:top], "candidates": len(picks)}


def market_summary(conn, until=None):
    dates = trading_dates(conn, limit=60, until=until)
    if not dates:
        return None
    d = dates[-1]
    m = conn.execute("SELECT * FROM market_daily WHERE date<=? ORDER BY date DESC LIMIT 1", (d,)).fetchone()
    m = dict(m) if m else {"date": d}

    # 市場寬度：站上 20 日均線的比例
    prices, *_ = _load(conn, dates[-20:]) if len(dates) >= 20 else ({},)
    above = total = 0
    for by_date in prices.values():
        closes = [by_date[x]["close"] for x in dates[-20:] if x in by_date and by_date[x]["close"]]
        if len(closes) == 20:
            total += 1
            above += closes[-1] > sum(closes) / 20
    m["breadth_ma20"] = round(above / total * 100, 1) if total else None

    # 近 5 日外資累計
    rows = conn.execute("SELECT * FROM market_daily WHERE date<=? ORDER BY date DESC LIMIT 5", (d,)).fetchall()
    m["foreign_amt_5d"] = sum(r["foreign_amt"] or 0 for r in rows) if rows else None

    # 盤勢判讀（規則式）
    signals = []
    bull = 0
    pct = m.get("taiex_pct")
    if pct is not None:
        signals.append(f"加權指數 {'上漲' if pct >= 0 else '下跌'} {abs(pct):.2f}%")
        bull += 1 if pct > 0.5 else -1 if pct < -0.5 else 0
    fa = m.get("foreign_amt")
    if fa is not None:
        signals.append(f"外資{'買超' if fa >= 0 else '賣超'} {abs(fa) / 1e8:.1f} 億")
        bull += 1 if fa > 5e9 else -1 if fa < -5e9 else 0
    ta = m.get("trust_amt")
    if ta is not None:
        signals.append(f"投信{'買超' if ta >= 0 else '賣超'} {abs(ta) / 1e8:.1f} 億")
    up, down = m.get("up_count"), m.get("down_count")
    if up is not None and down:
        signals.append(f"上漲 {up} 家、下跌 {down} 家")
        bull += 1 if up > down * 1.5 else -1 if down > up * 1.5 else 0
    b = m["breadth_ma20"]
    if b is not None:
        signals.append(f"{b:.0f}% 個股站上月線")
        bull += 1 if b >= 60 else -1 if b <= 40 else 0
    m["tone"] = "偏多" if bull >= 2 else "偏空" if bull <= -2 else "盤整"
    m["signals"] = signals
    m["date"] = d
    return m


def kline(conn, code, limit=240):
    rows = conn.execute(
        """SELECT p.date, p.open, p.high, p.low, p.close, p.volume, p.change,
                  i.foreign_net, i.trust_net, i.dealer_net, i.total_net,
                  m.margin_balance, m.short_balance
           FROM daily_price p
           LEFT JOIN institutional i ON i.code=p.code AND i.date=p.date
           LEFT JOIN margin m ON m.code=p.code AND m.date=p.date
           WHERE p.code=? ORDER BY p.date DESC LIMIT ?""", (code, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


# ---------- 每日報告：隔日偏多／偏空名單 ----------

def _item(code, names, ev, news_rows=()):
    name, market = names.get(code, ("", None))
    reasons = sorted(ev["reasons"], key=lambda x: -abs(x["pts"]))
    return {"code": code, "name": name, "market": market, "score": ev["score"], "verdict": ev["verdict"],
            "dims": ev["dims"], "reasons": reasons, **ev["metrics"],
            "news": [{"title": n["title"], "url": n["url"], "source": n["source"], "published": n["published"],
                      "sentiment": n["sentiment"]} for n in list(news_rows)[:3]]}


def build_report(conn, until=None, top=20, watchlist=(), min_vol=1000, min_price=10):
    """產生盤後報告：
    - buy：總分 ≥ BUY_MIN、站上月線、沒有明顯利空新聞，依分數排序
    - sell：總分 ≤ SELL_MAX，依分數由低到高（宜避開或減碼）
    - watch：自選股逐檔診斷（不套門檻）
    """
    dates = trading_dates(conn, until=until)
    if len(dates) < 20:
        return {"date": dates[-1] if dates else None, "buy": [], "sell": [], "watch": [],
                "note": f"資料只有 {len(dates)} 個交易日，至少需要 20 天，請先執行 backfill。"}
    prices, inst, margin, names, fund = _load(conn, dates)
    holders, news = _load_extra(conn, dates)
    buy, sell, evaluated, watch = [], [], 0, []
    watchset = set(watchlist)
    for code, by_date in prices.items():
        ev = evaluate(code, dates, by_date, inst.get(code, {}), margin.get(code, {}), fund,
                      holders.get(code, ()), news.get(code, ()))
        if not ev:
            continue
        m = ev["metrics"]
        if code in watchset:
            watch.append(_item(code, names, ev, news.get(code, ())))
        if m["close"] < min_price or m["avg_vol20"] < min_vol:
            continue
        evaluated += 1
        if ev["score"] >= BUY_MIN and m["close"] > m["ma20"] and ev["dims"]["news"] > -2:
            buy.append((ev, code))
        elif ev["score"] <= SELL_MAX:
            sell.append((ev, code))
    ratio = lambda ev: ev["metrics"]["inst5"] / max(ev["metrics"]["avg_vol20"], 1)
    buy.sort(key=lambda x: (x[0]["score"], ratio(x[0])), reverse=True)
    sell.sort(key=lambda x: (x[0]["score"], ratio(x[0])))
    order = {c: i for i, c in enumerate(watchlist)}
    watch.sort(key=lambda w: order.get(w["code"], 999))
    hd = conn.execute("SELECT MAX(date) d FROM holders WHERE date<=?", (dates[-1],)).fetchone()["d"]
    return {
        "date": dates[-1],
        "generated": datetime.now().isoformat(timespec="seconds"),
        "market": market_summary(conn, until=dates[-1]),
        "buy": [_item(c, names, ev, news.get(c, ())) for ev, c in buy[:top]],
        "sell": [_item(c, names, ev, news.get(c, ())) for ev, c in sell[:top]],
        "watch": watch,
        "counts": {"evaluated": evaluated, "buy": len(buy), "sell": len(sell)},
        "coverage": {"holders_date": hd, "news": sum(len(v) for v in news.values()), "news_stocks": len(news)},
    }


def save_report(conn, rep):
    if not rep.get("date"):
        return
    conn.execute("INSERT OR REPLACE INTO reports(date,generated,body) VALUES(?,?,?)",
                 (rep["date"], rep.get("generated"), json.dumps(rep, ensure_ascii=False)))
    conn.commit()


def load_report(conn, d=None):
    if d:
        r = conn.execute("SELECT body FROM reports WHERE date=?", (d,)).fetchone()
    else:
        r = conn.execute("SELECT body FROM reports ORDER BY date DESC LIMIT 1").fetchone()
    return json.loads(r["body"]) if r else None


def analyze_stock(conn, code, until=None):
    """單一個股的盤後診斷（不套選股門檻），附近期新聞與大戶週變化。"""
    dates = trading_dates(conn, until=until)
    if len(dates) < 20:
        return {"code": code, "note": "盤後資料不足 20 個交易日"}
    prices, inst, margin, names, fund = _load(conn, dates)
    holders, news = _load_extra(conn, dates)
    ev = evaluate(code, dates, prices.get(code, {}), inst.get(code, {}), margin.get(code, {}), fund,
                  holders.get(code, ()), news.get(code, ()))
    if not ev:
        return {"code": code, "date": dates[-1], "note": "這檔股票的盤後資料不足或當天沒有交易"}
    item = _item(code, names, ev, news.get(code, ()))
    item["date"] = dates[-1]
    item["news"] = [dict(n) for n in conn.execute(
        "SELECT title,url,source,published,sentiment FROM news WHERE code=? ORDER BY published DESC LIMIT 10", (code,))]
    item["holders_history"] = [dict(r) for r in conn.execute(
        "SELECT date,holders,big400_pct,big1000_pct,retail_pct FROM holders WHERE code=? ORDER BY date DESC LIMIT 12",
        (code,))][::-1]
    return item


def performance(conn, limit=20):
    """回測過去報告：名單在下一個交易日的平均漲跌幅與勝率（偏多看漲、偏空看跌）。"""
    reports = conn.execute("SELECT date, body FROM reports ORDER BY date DESC LIMIT ?", (limit,)).fetchall()
    rows = []
    for r in reports:
        nxt = conn.execute("SELECT MIN(date) d FROM daily_price WHERE date>?", (r["date"],)).fetchone()["d"]
        if not nxt:
            continue
        body = json.loads(r["body"])
        res = {"date": r["date"], "next": nxt}
        for side in ("buy", "sell"):
            codes = [x["code"] for x in body.get(side, [])]
            pcts = []
            for c in codes:
                a = conn.execute("SELECT close FROM daily_price WHERE code=? AND date=?", (c, r["date"])).fetchone()
                b = conn.execute("SELECT close FROM daily_price WHERE code=? AND date=?", (c, nxt)).fetchone()
                if a and b and a["close"] and b["close"]:
                    pcts.append((b["close"] - a["close"]) / a["close"] * 100)
            win = sum(1 for p in pcts if (p > 0 if side == "buy" else p < 0))
            res[side] = {"n": len(pcts), "avg_pct": round(sum(pcts) / len(pcts), 2) if pcts else None,
                         "win_rate": round(win / len(pcts) * 100, 1) if pcts else None}
        rows.append(res)
    return rows
