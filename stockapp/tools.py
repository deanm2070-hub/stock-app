"""新世紀類看盤工具：週月 K、還原 K 線、技術指標、自設公式、模擬資料、排行、類股、條件選股、
除權息行事曆、季報財務。

資料收集：
    python -m stockapp.tools collect            # 除權息、行事曆、產業別、季報（盤後收集時也會自動跑）
    python -m stockapp.tools exrights --days 365   # 回補除權息結果（還原 K 線用）
"""
import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import db, extra_sources as xs, formula, indicators

SCHEMA = """
CREATE TABLE IF NOT EXISTS exrights (
    date TEXT, code TEXT,
    prev_close REAL, ref_price REAL,  -- 除權息前收盤價、參考價，還原因子 = ref/prev
    value REAL, kind TEXT,
    PRIMARY KEY (date, code)
);
CREATE TABLE IF NOT EXISTS exright_plan (
    date TEXT, code TEXT, name TEXT, kind TEXT,
    stock_ratio REAL, cash_div REAL, market TEXT,
    PRIMARY KEY (date, code, kind)
);
CREATE TABLE IF NOT EXISTS company (
    code TEXT PRIMARY KEY, industry TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS financials (
    code TEXT, year INTEGER, quarter INTEGER,   -- 累計數（千元），同公開資訊觀測站
    revenue REAL, gross_profit REAL, op_income REAL, pretax_income REAL, net_income REAL, eps REAL,
    assets REAL, liabilities REAL, equity REAL, bvps REAL,
    PRIMARY KEY (code, year, quarter)
);
CREATE INDEX IF NOT EXISTS idx_exr_code ON exrights(code, date);
"""


def ensure_schema(conn):
    conn.executescript(SCHEMA)


# ---------- K 線處理 ----------

def _rows(conn, code, limit):
    rows = conn.execute(
        """SELECT p.date, p.open, p.high, p.low, p.close, p.volume, p.value, p.change,
                  i.foreign_net, i.trust_net, i.dealer_net, i.total_net,
                  m.margin_balance, m.short_balance
           FROM daily_price p
           LEFT JOIN institutional i ON i.code=p.code AND i.date=p.date
           LEFT JOIN margin m ON m.code=p.code AND m.date=p.date
           WHERE p.code=? AND p.close IS NOT NULL ORDER BY p.date DESC LIMIT ?""", (code, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def _recalc_change(rows):
    for prev, r in zip(rows, rows[1:]):
        if prev["close"] is not None and r["close"] is not None:
            r["change"] = round(r["close"] - prev["close"], 2)


def adjust(rows, events):
    """還原權值：除權息日之前的價格乘上 參考價/前收盤 的累積因子。events: [(date, factor)]。"""
    if not events or not rows:
        return rows
    events = sorted(e for e in events if e[1] and 0 < e[1] < 1.5)
    out = []
    for r in rows:
        f = 1.0
        for d, k in events:
            if r["date"] < d:
                f *= k
        r = dict(r)
        if f != 1.0:
            for k in ("open", "high", "low", "close"):
                if r[k] is not None:
                    r[k] = round(r[k] * f, 2)
        out.append(r)
    _recalc_change(out)
    return out


def _period_key(ds, period):
    d = date.fromisoformat(ds)
    if period == "W":
        y, w, _ = d.isocalendar()
        return (y, w)
    return ds[:7]


def resample(rows, period):
    """日 K 合併成週 K（W）或月 K（M）。日期用該期最後一個交易日。"""
    if period not in ("W", "M"):
        return rows
    groups = []
    for r in rows:
        k = _period_key(r["date"], period)
        if groups and groups[-1][0] == k:
            groups[-1][1].append(r)
        else:
            groups.append((k, [r]))

    def ssum(g, f):
        vals = [x[f] for x in g if x.get(f) is not None]
        return sum(vals) if vals else None

    def last(g, f):
        vals = [x[f] for x in g if x.get(f) is not None]
        return vals[-1] if vals else None

    out = []
    for _, g in groups:
        out.append({
            "date": g[-1]["date"], "open": next((x["open"] for x in g if x["open"] is not None), g[0]["close"]),
            "high": max((x["high"] or x["close"]) for x in g), "low": min((x["low"] or x["close"]) for x in g),
            "close": g[-1]["close"], "volume": ssum(g, "volume"), "value": ssum(g, "value"), "change": None,
            "foreign_net": ssum(g, "foreign_net"), "trust_net": ssum(g, "trust_net"),
            "dealer_net": ssum(g, "dealer_net"), "total_net": ssum(g, "total_net"),
            "margin_balance": last(g, "margin_balance"), "short_balance": last(g, "short_balance"),
        })
    _recalc_change(out)
    return out


def _next_dates(last, n, period):
    d, out = date.fromisoformat(last), []
    while len(out) < n:
        if period == "W":
            d += timedelta(days=7)
        elif period == "M":
            nxt = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
            d = (nxt + timedelta(days=32)).replace(day=1) - timedelta(days=1)  # 下個月月底
        else:
            d += timedelta(days=1)
            if d.weekday() >= 5:
                continue
        out.append(d.isoformat())
    return out


def simulate(rows, prices, period="D"):
    """模擬資料：在最後面接上假設的收盤價（開高低都等於該價、量沿用前一根），看指標會怎麼走。"""
    if not rows or not prices:
        return rows
    rows = list(rows)
    for ds, p in zip(_next_dates(rows[-1]["date"], len(prices), period), prices):
        prev = rows[-1]
        rows.append({"date": ds, "open": p, "high": p, "low": p, "close": p, "volume": prev.get("volume"),
                     "value": None, "change": round(p - prev["close"], 2), "foreign_net": None, "trust_net": None,
                     "dealer_net": None, "total_net": None, "margin_balance": prev.get("margin_balance"),
                     "short_balance": prev.get("short_balance"), "sim": True})
    return rows


def exright_events(conn, code):
    return [(r["date"], r["ref_price"] / r["prev_close"]) for r in conn.execute(
        "SELECT date, prev_close, ref_price FROM exrights WHERE code=? AND prev_close>0", (code,))]


def chart(conn, code, period="D", adjusted=False, sim=None, names=(), expr=None, limit=None):
    """看盤主圖資料：K 線 + 選定的指標 + 自設公式。"""
    period = period if period in ("D", "W", "M") else "D"
    limit = limit or {"D": 600, "W": 2500, "M": 5000}[period]
    rows = _rows(conn, code, limit)
    events = exright_events(conn, code)
    if adjusted:
        rows = adjust(rows, events)
    rows = resample(rows, period)
    rows = simulate(rows, sim or [], period)
    out = {"code": code, "period": period, "adjusted": adjusted, "bars": rows,
           "exrights": [{"date": d, "factor": round(f, 5)} for d, f in events],
           "indicators": indicators.compute(rows, names) if rows else {}}
    if expr:
        try:
            out["formula"] = {"lines": formula.run(expr, rows) if rows else {}}
        except formula.FormulaError as e:
            out["formula"] = {"error": str(e)}
    return out


def compare(conn, codes, limit=240, adjusted=True):
    """疊圖：回傳各檔（含 TAIEX 加權指數）的收盤序列。"""
    out = {}
    for c in codes[:6]:
        if c.upper() == "TAIEX":
            rs = conn.execute("SELECT date, taiex close FROM market_daily WHERE taiex IS NOT NULL "
                              "ORDER BY date DESC LIMIT ?", (limit,)).fetchall()
            out["TAIEX"] = {"name": "加權指數", "points": [{"date": r["date"], "close": r["close"]} for r in reversed(rs)]}
            continue
        rows = _rows(conn, c, limit)
        if adjusted:
            rows = adjust(rows, exright_events(conn, c))
        name = conn.execute("SELECT name FROM stocks WHERE code=?", (c,)).fetchone()
        out[c] = {"name": name["name"] if name else "", "points": [{"date": r["date"], "close": r["close"]} for r in rows]}
    return out


# ---------- 排行 ----------

RANKS = {
    "gain": ("漲幅", "pct", True), "loss": ("跌幅", "pct", False),
    "volume": ("成交量", "volume", True), "value": ("成交值", "value", True),
    "amplitude": ("振幅", "amplitude", True), "vol_ratio": ("量增（對 5 日均量）", "vol_ratio", True),
    "foreign_buy": ("外資買超", "foreign", True), "foreign_sell": ("外資賣超", "foreign", False),
    "trust_buy": ("投信買超", "trust", True), "trust_sell": ("投信賣超", "trust", False),
    "dealer_buy": ("自營商買超", "dealer", True), "dealer_sell": ("自營商賣超", "dealer", False),
    "inst_buy": ("法人合計買超", "inst", True), "inst_sell": ("法人合計賣超", "inst", False),
    "margin_inc": ("融資增加", "margin_chg", True), "margin_dec": ("融資減少", "margin_chg", False),
    "short_inc": ("融券增加", "short_chg", True), "short_dec": ("融券減少", "short_chg", False),
}


def _dates(conn, until=None, n=6):
    q, args = "SELECT DISTINCT date FROM daily_price", []
    if until:
        q += " WHERE date<=?"
        args.append(until)
    q += " ORDER BY date DESC LIMIT ?"
    return sorted(r["date"] for r in conn.execute(q, args + [n]))


def market_snapshot(conn, until=None):
    """某交易日全市場的行情、法人、融資券變化，排行與類股共用。"""
    dates = _dates(conn, until)
    if not dates:
        return None, {}
    d = dates[-1]
    prev = dates[-2] if len(dates) > 1 else None
    snap = {}
    names = {r["code"]: (r["name"], r["market"]) for r in conn.execute("SELECT code,name,market FROM stocks")}
    inds = {r["code"]: r["industry"] for r in conn.execute("SELECT code,industry FROM company")}
    for r in conn.execute("SELECT * FROM daily_price WHERE date=? AND close IS NOT NULL", (d,)):
        c, chg = r["close"], r["change"] or 0
        base = c - chg
        nm, mk = names.get(r["code"], ("", None))
        snap[r["code"]] = {
            "code": r["code"], "name": nm, "market": mk, "industry": inds.get(r["code"]),
            "close": c, "change": chg, "pct": round(chg / base * 100, 2) if base else None,
            "volume": round((r["volume"] or 0) / 1000), "value": r["value"] or 0,
            "amplitude": round((r["high"] - r["low"]) / base * 100, 2) if base and r["high"] and r["low"] else None,
        }
    vol5 = defaultdict(list)
    for r in conn.execute("SELECT code, volume FROM daily_price WHERE date>=? AND date<?", (dates[0], d)):
        vol5[r["code"]].append((r["volume"] or 0) / 1000)
    for code, s in snap.items():
        v = vol5.get(code)
        s["vol_ratio"] = round(s["volume"] / (sum(v) / len(v)), 2) if v and sum(v) else None
    for r in conn.execute("SELECT * FROM institutional WHERE date=?", (d,)):
        s = snap.get(r["code"])
        if s:
            s.update(foreign=round((r["foreign_net"] or 0) / 1000), trust=round((r["trust_net"] or 0) / 1000),
                     dealer=round((r["dealer_net"] or 0) / 1000), inst=round((r["total_net"] or 0) / 1000))
    if prev:
        pm = {r["code"]: r for r in conn.execute("SELECT * FROM margin WHERE date=?", (prev,))}
        for r in conn.execute("SELECT * FROM margin WHERE date=?", (d,)):
            s, p = snap.get(r["code"]), pm.get(r["code"])
            if s:
                s["margin"], s["short"] = r["margin_balance"], r["short_balance"]
                if p and r["margin_balance"] is not None and p["margin_balance"] is not None:
                    s["margin_chg"] = r["margin_balance"] - p["margin_balance"]
                if p and r["short_balance"] is not None and p["short_balance"] is not None:
                    s["short_chg"] = r["short_balance"] - p["short_balance"]
    return d, snap


def rank(conn, kind="gain", until=None, top=50, market=None, min_volume=100):
    if kind not in RANKS:
        kind = "gain"
    label, field, desc = RANKS[kind]
    d, snap = market_snapshot(conn, until)
    rows = [s for s in snap.values() if s.get(field) is not None and (not market or s["market"] == market)]
    if field in ("pct", "amplitude", "vol_ratio"):
        rows = [s for s in rows if s["volume"] >= min_volume]
    rows = [s for s in rows if (s[field] > 0 if desc else s[field] < 0)] if field not in ("volume", "value", "amplitude") else rows
    rows.sort(key=lambda s: s[field], reverse=desc)
    return {"date": d, "kind": kind, "label": label, "field": field, "rows": rows[:top],
            "kinds": [{"key": k, "label": v[0]} for k, v in RANKS.items()]}


def industries(conn, until=None):
    d, snap = market_snapshot(conn, until)
    groups = defaultdict(list)
    for s in snap.values():
        groups[s["industry"] or "未分類"].append(s)
    out = []
    for name, g in groups.items():
        pcts = [s["pct"] for s in g if s["pct"] is not None]
        val = sum(s["value"] for s in g)
        # 類股漲跌幅以成交值加權
        wp = sum((s["pct"] or 0) * s["value"] for s in g) / val if val else (sum(pcts) / len(pcts) if pcts else None)
        out.append({"industry": name, "count": len(g), "pct": round(wp, 2) if wp is not None else None,
                    "up": sum(p > 0 for p in pcts), "down": sum(p < 0 for p in pcts), "value": val,
                    "inst": sum(s.get("inst") or 0 for s in g),
                    "leaders": sorted(g, key=lambda s: s["value"], reverse=True)[:3]})
    out.sort(key=lambda x: (x["pct"] is None, -(x["pct"] or 0)))
    return {"date": d, "industries": out, "classified": any(s["industry"] for s in snap.values())}


def industry_members(conn, name, until=None):
    d, snap = market_snapshot(conn, until)
    rows = [s for s in snap.values() if (s["industry"] or "未分類") == name]
    rows.sort(key=lambda s: s["value"], reverse=True)
    return {"date": d, "industry": name, "rows": rows}


# ---------- 自設公式選股 ----------

def formula_screen(conn, expr, until=None, lookback=150, top=200, market=None):
    prog = formula.parse(expr)  # 語法錯誤直接丟出 FormulaError
    dates = _dates(conn, until, lookback)
    if not dates:
        return {"date": None, "rows": [], "note": "沒有盤後資料"}
    lo, hi = dates[0], dates[-1]
    by_code = defaultdict(list)
    for r in conn.execute(
            """SELECT p.code, p.date, p.open, p.high, p.low, p.close, p.volume, p.change,
                      i.foreign_net, i.trust_net, i.dealer_net, i.total_net, m.margin_balance, m.short_balance
               FROM daily_price p
               LEFT JOIN institutional i ON i.code=p.code AND i.date=p.date
               LEFT JOIN margin m ON m.code=p.code AND m.date=p.date
               WHERE p.date BETWEEN ? AND ? AND p.close IS NOT NULL ORDER BY p.code, p.date""", (lo, hi)):
        by_code[r["code"]].append(dict(r))
    names = {r["code"]: (r["name"], r["market"]) for r in conn.execute("SELECT code,name,market FROM stocks")}
    hits, errors = [], None
    for code, rows in by_code.items():
        if rows[-1]["date"] != hi:
            continue
        nm, mk = names.get(code, ("", None))
        if market and mk != market:
            continue
        try:
            res = formula.evaluate(prog, indicators.to_series(rows))
        except formula.FormulaError as e:
            errors = str(e)
            break
        last = res[-1][1][-1]
        if not last:
            continue
        last_row = rows[-1]
        base = last_row["close"] - (last_row["change"] or 0)
        hits.append({"code": code, "name": nm, "market": mk, "close": last_row["close"],
                     "pct": round((last_row["change"] or 0) / base * 100, 2) if base else None,
                     "volume": round((last_row["volume"] or 0) / 1000),
                     "values": {(n or f"公式{i + 1}"): (round(v[-1], 2) if v[-1] is not None else None)
                                for i, (n, v) in enumerate(res)}})
    if errors:
        raise formula.FormulaError(errors)
    hits.sort(key=lambda h: h["volume"], reverse=True)
    return {"date": hi, "count": len(hits), "rows": hits[:top], "scanned": len(by_code)}


PRESETS = [
    ("均線多頭排列", "C>MA(C,5) AND MA(C,5)>MA(C,10) AND MA(C,10)>MA(C,20) AND MA(C,20)>MA(C,60)"),
    ("KD 黃金交叉（低檔）", "CROSS(K(9),D(9)) AND K(9)<30"),
    ("MACD 翻正", "CROSS(DIF(),DEA())"),
    ("爆量長紅", "V>2*MA(V,5) AND C>O*1.04"),
    ("創 60 日新高", "C>=HHV(H,60)"),
    ("突破布林上軌", "C>MA(C,20)+2*STD(C,20)"),
    ("投信連 3 買", "EVERY(TI>0,3)"),
    ("外資投信同買", "FI>0 AND TI>0"),
    ("股價漲融資減", "C>REF(C,5) AND MB<REF(MB,5)"),
    ("RSI 超賣", "RSI(6)<20"),
    ("跌破季線", "CROSS(MA(C,60),C)"),
    ("量縮整理", "V<0.5*MA(V,20) AND ABS(C/MA(C,20)-1)<0.03"),
]


# ---------- 除權息行事曆、季報 ----------

def calendar(conn, code=None, days_ahead=60):
    today = date.today().isoformat()
    end = (date.today() + timedelta(days=days_ahead)).isoformat()
    q = "SELECT * FROM exright_plan WHERE date BETWEEN ? AND ?"
    args = [today, end]
    if code:
        q, args = "SELECT * FROM exright_plan WHERE code=?", [code]
    plan = [dict(r) for r in conn.execute(q + " ORDER BY date, code", args)]
    hq = "SELECT e.*, s.name FROM exrights e LEFT JOIN stocks s ON s.code=e.code"
    hargs = []
    if code:
        hq += " WHERE e.code=?"
        hargs.append(code)
    hist = [dict(r) for r in conn.execute(hq + " ORDER BY e.date DESC LIMIT 100", hargs)]
    return {"upcoming": plan, "history": hist}


def financials(conn, code, limit=12):
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM financials WHERE code=? ORDER BY year DESC, quarter DESC LIMIT ?", (code, limit + 4))]
    cum = {(r["year"], r["quarter"]): r for r in rows}
    out = []
    for r in rows[:limit]:
        prev = cum.get((r["year"], r["quarter"] - 1)) if r["quarter"] > 1 else None
        q = dict(r)
        # 損益表為年度累計，單季 = 本季累計 − 上季累計
        for k in ("revenue", "gross_profit", "op_income", "net_income", "eps"):
            if r["quarter"] == 1:
                q[k + "_q"] = r[k]
            else:
                q[k + "_q"] = (r[k] - prev[k]) if prev and r[k] is not None and prev[k] is not None else None
        rev = q["revenue_q"]
        q["gross_margin"] = round(q["gross_profit_q"] / rev * 100, 2) if rev and q["gross_profit_q"] is not None else None
        q["op_margin"] = round(q["op_income_q"] / rev * 100, 2) if rev and q["op_income_q"] is not None else None
        q["net_margin"] = round(q["net_income_q"] / rev * 100, 2) if rev and q["net_income_q"] is not None else None
        q["debt_ratio"] = round(r["liabilities"] / r["assets"] * 100, 2) if r["assets"] and r["liabilities"] is not None else None
        q["roe"] = round(r["net_income"] / r["equity"] * 100, 2) if r["equity"] and r["net_income"] is not None else None
        if q["eps_q"] is not None:
            q["eps_q"] = round(q["eps_q"], 2)
        out.append(q)
    return out


def profile(conn, code):
    c = conn.execute("SELECT industry FROM company WHERE code=?", (code,)).fetchone()
    ind = c["industry"] if c else None
    peers = []
    if ind:
        peers = [dict(r) for r in conn.execute(
            """SELECT c.code, s.name FROM company c JOIN stocks s ON s.code=c.code
               WHERE c.industry=? AND c.code<>? ORDER BY c.code LIMIT 30""", (ind, code))]
    nxt = conn.execute("SELECT * FROM exright_plan WHERE code=? AND date>=? ORDER BY date LIMIT 1",
                       (code, date.today().isoformat())).fetchone()
    return {"code": code, "industry": ind, "peers": peers, "next_exright": dict(nxt) if nxt else None}


# ---------- 收集 ----------

def _last_update(conn, kind):
    r = conn.execute("SELECT MAX(at) a FROM collect_log WHERE kind=?", (kind,)).fetchone()
    return r["a"] if r else None


def _log(conn, kind, status):
    db.upsert(conn, "collect_log", [{"date": date.today().isoformat(), "kind": kind, "status": status,
                                     "at": datetime.now().isoformat(timespec="seconds")}], [])


def collect_exrights(conn, start, end):
    """證交所除權息結果，一次最多查一年。"""
    n = 0
    d = start
    while d <= end:
        e = min(end, d + timedelta(days=365))
        try:
            rows = xs.parse_twse_exrights(xs.fetch_twse_exrights(d, e))
            n += db.upsert(conn, "exrights", rows, [])
        except RuntimeError as err:
            print("[除權息]", err)
        d = e + timedelta(days=1)
    conn.commit()
    print(f"[除權息] {n} 筆")
    return n


def collect(conn, d=None, force=False):
    """盤後收集時呼叫：除權息與預告每天更新，產業別與季報一週更新一次。"""
    ensure_schema(conn)
    d = d or date.today()
    collect_exrights(conn, d - timedelta(days=7), d)
    try:
        plan = xs.parse_twse_exright_plan(xs.fetch_twse_exright_plan())
        db.upsert(conn, "exright_plan", plan, [])
        print(f"[除權息預告] {len(plan)} 筆")
    except RuntimeError as e:
        print("[除權息預告]", e)
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    if force or (_last_update(conn, "company") or "") < week_ago:
        info = xs.parse_company_info(xs.fetch_company_info())
        now = datetime.now().isoformat(timespec="seconds")
        db.upsert(conn, "company", [{"code": r["code"], "industry": r["industry"], "updated": now} for r in info], [])
        db.upsert_stocks(conn, [r for r in info if r["name"]])
        _log(conn, "company", "ok" if info else "empty")
        print(f"[產業別] {len(info)} 檔")
    if force or (_last_update(conn, "financials") or "") < week_ago:
        fin = xs.parse_financials(*xs.fetch_financials())
        db.upsert(conn, "financials", fin, [])
        _log(conn, "financials", "ok" if fin else "empty")
        print(f"[季報] {len(fin)} 筆")
    conn.commit()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["collect", "exrights"])
    p.add_argument("--days", type=int, default=365)
    a = p.parse_args()
    with db.session() as conn:
        ensure_schema(conn)
        if a.cmd == "collect":
            collect(conn, force=True)
        else:
            collect_exrights(conn, date.today() - timedelta(days=a.days), date.today())


if __name__ == "__main__":
    main()
