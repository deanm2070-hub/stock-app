"""盤後分析：大盤盤勢判讀 + 明日觀察名單。

規則式評分，每個加減分都附上理由，方便自己檢查為什麼入選。
這是技術、籌碼、基本面的條件篩選，不是投資建議。
"""
from collections import defaultdict

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


def score_stock(code, dates, prices, inst, margin, fund):
    """回傳 (score, reasons, metrics) 或 None（不符合基本門檻）。"""
    rows = [prices[d] for d in dates if d in prices and prices[d]["close"]]
    if len(rows) < 20 or rows[-1]["date"] != dates[-1]:
        return None
    closes = [r["close"] for r in rows]
    vols = [(r["volume"] or 0) / 1000 for r in rows]  # 張
    last = rows[-1]
    close = last["close"]
    avg_vol20 = _ma(vols, 20)
    if close < 10 or avg_vol20 < 1000:
        return None

    ma5, ma20, ma60 = _ma(closes, 5), _ma(closes, 20), _ma(closes, 60)
    if close <= ma20:  # 基本門檻：站上月線
        return None

    score, reasons = 0, []

    def add(pts, why):
        nonlocal score
        score += pts
        reasons.append({"pts": pts, "text": why})

    # --- 技術面 ---
    if close > ma5 > ma20:
        add(2, "均線多頭排列（收盤 > MA5 > MA20）")
    if ma60 and close > ma60:
        add(1, "站上季線 MA60")
    chg = last["change"] or 0
    avg_vol5_prev = sum(vols[-6:-1]) / 5 if len(vols) >= 6 else None
    if avg_vol5_prev and vols[-1] >= 1.5 * avg_vol5_prev and chg > 0:
        add(1, f"價漲量增（量為 5 日均量 {vols[-1] / avg_vol5_prev:.1f} 倍）")
    if close >= max(r["high"] or r["close"] for r in rows[-20:]):
        add(1, "收盤創 20 日新高")
    rsi = _rsi(closes)
    bias20 = (close - ma20) / ma20 * 100
    if rsi is not None and rsi >= 80:
        add(-1, f"RSI {rsi:.0f} 短線過熱")
    if bias20 >= 10:
        add(-1, f"月線乖離 {bias20:.1f}% 偏大，追價風險高")

    # --- 籌碼面 ---
    ins = [inst.get(d) for d in dates[-10:]]
    fx = [(r["foreign_net"] if r else None) for r in ins]
    tr = [(r["trust_net"] if r else None) for r in ins]
    tot5 = sum((r["total_net"] or 0) for r in ins[-5:] if r) / 1000  # 張
    fx_streak, tr_streak = _streak(fx), _streak(tr)
    if fx_streak >= 3:
        add(1, f"外資連買 {fx_streak} 天")
    elif fx and fx[-1] and fx[-1] > 0:
        add(0.5, "外資今日買超")
    if tr_streak >= 3:
        add(2, f"投信連買 {tr_streak} 天")
    elif tr and tr[-1] and tr[-1] > 0:
        add(0.5, "投信今日買超")
    inst_ratio = tot5 / (avg_vol20 * 5) * 100 if avg_vol20 else 0
    if inst_ratio >= 10:
        add(1, f"法人 5 日買超占成交量 {inst_ratio:.0f}%")
    elif inst_ratio <= -10:
        add(-1, f"法人 5 日賣超占成交量 {-inst_ratio:.0f}%")

    mg = [margin.get(d) for d in dates[-6:]]
    mg = [r["margin_balance"] for r in mg if r and r["margin_balance"] is not None]
    margin_chg = None
    if len(mg) >= 2 and mg[0]:
        margin_chg = (mg[-1] - mg[0]) / mg[0] * 100
        if margin_chg < -3 and closes[-1] > closes[-6]:
            add(1, f"股價上漲但融資減少 {-margin_chg:.1f}%，籌碼安定")
        elif margin_chg > 10:
            add(-1, f"融資 5 日暴增 {margin_chg:.1f}%，散戶追價")

    # --- 基本面 ---
    f = fund.get(code)
    rev_yoy = pe = dy = None
    if f:
        rev_yoy, pe, dy = f["rev_yoy"], f["pe"], f["dividend_yield"]
        if rev_yoy is not None:
            if rev_yoy >= 20:
                add(2, f"月營收年增 {rev_yoy:.1f}%")
            elif rev_yoy > 0:
                add(1, f"月營收年增 {rev_yoy:.1f}%")
            elif rev_yoy <= -20:
                add(-1, f"月營收年減 {-rev_yoy:.1f}%")
        if pe and 0 < pe <= 20:
            add(1, f"本益比 {pe:.1f} 不高")
        elif pe and pe > 40:
            add(-0.5, f"本益比 {pe:.1f} 偏高")
        if dy and dy >= 4:
            add(1, f"殖利率 {dy:.1f}%")

    quality = bool(rev_yoy is not None and rev_yoy > 0 and pe and 0 < pe <= 25)
    metrics = {
        "close": close, "change": chg, "pct": round(chg / (close - chg) * 100, 2) if close != chg else None,
        "volume": round(vols[-1]), "avg_vol20": round(avg_vol20),
        "ma5": round(ma5, 2), "ma20": round(ma20, 2), "ma60": round(ma60, 2) if ma60 else None,
        "rsi": round(rsi, 1) if rsi is not None else None, "bias20": round(bias20, 2),
        "foreign_streak": fx_streak, "trust_streak": tr_streak, "inst5": round(tot5),
        "margin_chg5": round(margin_chg, 2) if margin_chg is not None else None,
        "rev_yoy": rev_yoy, "pe": pe, "dividend_yield": dy, "quality": quality,
    }
    return score, reasons, metrics


def screen(conn, until=None, top=30, min_score=4, quality_only=False):
    dates = trading_dates(conn, until=until)
    if len(dates) < 20:
        return {"date": dates[-1] if dates else None, "picks": [],
                "note": f"資料只有 {len(dates)} 個交易日，至少需要 20 天，請先執行 backfill。"}
    prices, inst, margin, names, fund = _load(conn, dates)
    picks = []
    for code, by_date in prices.items():
        res = score_stock(code, dates, by_date, inst.get(code, {}), margin.get(code, {}), fund)
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
