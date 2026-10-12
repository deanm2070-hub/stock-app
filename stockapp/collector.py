"""盤後資料收集。

用法：
    python -m stockapp.collector today            # 收今天（收盤後 16:30 以後執行最完整）
    python -m stockapp.collector backfill --days 90   # 回補最近 90 個日曆天的上市資料
    python -m stockapp.collector fundamentals     # 更新本益比、殖利率、月營收
    python -m stockapp.collector holders          # 更新集保大戶持股（每週）
    python -m stockapp.collector news             # 抓新聞並重新產生盤後報告
    python -m stockapp.collector report           # 只重新產生盤後報告
"""
import argparse
import re
from datetime import date, datetime, timedelta

from . import analysis, db, sources


def _log(conn, d, kind, status):
    conn.execute("INSERT OR REPLACE INTO collect_log(date,kind,status,at) VALUES(?,?,?,?)",
                 (d.isoformat(), kind, status, datetime.now().isoformat(timespec="seconds")))


def _done(conn, d, kind):
    r = conn.execute("SELECT status FROM collect_log WHERE date=? AND kind=?", (d.isoformat(), kind)).fetchone()
    return r is not None and r["status"] in ("ok", "closed")


def collect_twse(conn, d: date, force=False):
    """收一天的上市資料。回傳 False 表示休市。"""
    if not force and _done(conn, d, "twse_price"):
        return True
    prices, stocks, market = sources.parse_twse_daily(sources.fetch_twse_daily(d), d)
    if not prices:
        # 只有確定已過收盤公布時間才記為休市；當天資料還沒出來時不記，免得之後回補被略過
        if d < date.today() or datetime.now().hour >= 17:
            _log(conn, d, "twse_price", "closed")
        return False
    db.upsert_stocks(conn, stocks)
    db.upsert(conn, "daily_price", prices, ["date", "code"])
    _log(conn, d, "twse_price", "ok")

    if force or not _done(conn, d, "twse_inst"):
        inst = sources.parse_twse_institutional(sources.fetch_twse_institutional(d), d)
        db.upsert(conn, "institutional", inst, ["date", "code"])
        _log(conn, d, "twse_inst", "ok" if inst else "empty")

    if force or not _done(conn, d, "twse_margin"):
        mg = sources.parse_twse_margin(sources.fetch_twse_margin(d), d)
        db.upsert(conn, "margin", mg, ["date", "code"])
        _log(conn, d, "twse_margin", "ok" if mg else "empty")

    if market:
        market.update(sources.parse_twse_market_institutional(sources.fetch_twse_market_institutional(d)))
        db.upsert(conn, "market_daily", [market], ["date"])
    conn.commit()
    return True


def collect_tpex_latest(conn):
    """櫃買 OpenAPI 只給最新交易日，每天收盤後跑一次即可累積歷史。"""
    try:
        prices, stocks = sources.parse_tpex_daily(sources.fetch_tpex_daily())
        db.upsert_stocks(conn, stocks)
        db.upsert(conn, "daily_price", prices, ["date", "code"])
        print(f"[上櫃] 行情 {len(prices)} 筆")
    except RuntimeError as e:
        print("[上櫃] 行情失敗", e)
    for name, fetch, parse, table in [
        ("法人", sources.fetch_tpex_institutional, sources.parse_tpex_institutional, "institutional"),
        ("融資券", sources.fetch_tpex_margin, sources.parse_tpex_margin, "margin"),
    ]:
        try:
            rows = parse(fetch())
            db.upsert(conn, table, rows, ["date", "code"])
            print(f"[上櫃] {name} {len(rows)} 筆")
        except RuntimeError as e:
            print(f"[上櫃] {name}失敗", e)
    conn.commit()


def collect_fundamentals(conn):
    data = sources.fetch_fundamentals()
    now = datetime.now().isoformat(timespec="seconds")
    rows = [{"code": c, "pe": v.get("pe"), "pb": v.get("pb"), "dividend_yield": v.get("dividend_yield"),
             "rev_month": v.get("rev_month"), "rev_yoy": v.get("rev_yoy"), "rev_mom": v.get("rev_mom"),
             "updated": now} for c, v in data.items()]
    db.upsert(conn, "fundamentals", rows, ["code"])
    conn.commit()
    print(f"[基本面] {len(rows)} 檔")


def collect_holders(conn):
    """集保股權分散表只提供最新一週，每天抓一次，資料日相同就覆蓋，週週累積成歷史。"""
    try:
        rows = sources.parse_tdcc(sources.fetch_tdcc())
    except RuntimeError as e:
        print("[集保] 失敗", e)
        return 0
    db.upsert(conn, "holders", rows, ["date", "code"])
    conn.commit()
    print(f"[集保] {rows[0]['date'] if rows else '-'} {len(rows)} 檔")
    return len(rows)


def _match_codes(title, names):
    """從標題找出提到的個股：代號（4 碼）或名稱（2 字以上）。"""
    found = set()
    for code, name in names.items():
        if (name and len(name) >= 2 and name in title) or (code in title and re.search(rf"(?<!\d){code}(?!\d)", title)):
            found.add(code)
    return found


def _store_news(conn, items, names, code_hint=None):
    now = datetime.now().isoformat(timespec="seconds")
    rows = []
    for it in items:
        codes = set(it.get("codes") or [])
        codes |= _match_codes(it["title"], names)
        if code_hint:
            codes.add(code_hint)
        senti = sources.news_sentiment(it["title"] + " " + (it.get("summary") or "")[:120])
        for c in codes or {""}:
            rows.append({"url": it["url"], "code": c, "published": it.get("published") or now, "title": it["title"],
                         "source": it["source"], "summary": it.get("summary") or "", "sentiment": senti, "fetched": now})
    db.upsert(conn, "news", rows, ["url", "code"])
    return len(rows)


def collect_news(conn, codes=(), max_stocks=80):
    """先抓鉅亨網台股新聞（一次一批），再對候選股逐檔抓 Yahoo 股市 RSS。"""
    names = {r["code"]: r["name"] for r in conn.execute("SELECT code,name FROM stocks")}
    markets = {r["code"]: r["market"] for r in conn.execute("SELECT code,market FROM stocks")}
    n = 0
    try:
        n += _store_news(conn, sources.parse_cnyes(sources.fetch_cnyes()), names)
    except RuntimeError as e:
        print("[新聞] 鉅亨網失敗", e)
    for code in list(dict.fromkeys(codes))[:max_stocks]:
        try:
            items = sources.parse_rss(sources.fetch_yahoo_rss(code, markets.get(code) or "tse"))
            n += _store_news(conn, items, names, code_hint=code)
        except RuntimeError as e:
            print(f"[新聞] {code} 失敗", e)
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    conn.execute("DELETE FROM news WHERE published<?", (cutoff,))
    conn.commit()
    print(f"[新聞] {n} 筆")
    return n


def make_report(conn, with_news=True):
    """產生盤後報告。先不含新聞評一輪，挑出候選股與自選股抓新聞，再正式評分並存檔。"""
    watch = db.load_watchlist()
    if with_news:
        pre = analysis.build_report(conn, top=40, watchlist=watch)
        codes = watch + [x["code"] for x in pre["buy"]] + [x["code"] for x in pre["sell"][:20]]
        collect_news(conn, codes)
    rep = analysis.build_report(conn, watchlist=watch)
    analysis.save_report(conn, rep)
    print(f"[報告] {rep.get('date')} 偏多 {len(rep['buy'])} 檔、偏空 {len(rep['sell'])} 檔")
    return rep


def collect_today(conn, d=None):
    """收集指定日（預設今天）的盤後資料並產生報告。

    週末、假日或半夜執行時，當天沒有行情，改補最近一個還沒收過的交易日；
    櫃買、基本面、集保、新聞與報告都只看「最新資料」，所以一律照跑，報告以資料庫裡最新的交易日為準。"""
    d = d or date.today()
    opened = d.weekday() < 5 and collect_twse(conn, d, force=True)
    if not opened:
        prev = d
        for _ in range(10):  # 往回找最近的交易日（涵蓋春節等長假）
            prev -= timedelta(days=1)
            if prev.weekday() >= 5:
                continue
            if conn.execute("SELECT 1 FROM daily_price WHERE date=? LIMIT 1", (prev.isoformat(),)).fetchone():
                break  # 最近的交易日已經收過
            if _done(conn, prev, "twse_price"):
                continue  # 記錄為休市，再往前
            try:
                if collect_twse(conn, prev):
                    print(f"{d} 沒有行情，改收最近交易日 {prev}")
                    break
            except RuntimeError as e:
                print(prev, "失敗", e)
                break
    collect_tpex_latest(conn)
    collect_fundamentals(conn)
    collect_holders(conn)
    rep = make_report(conn)
    print(f"{d} {'完成' if opened else '休市，報告以 ' + str(rep.get('date')) + ' 為準'}")
    return opened

def backfill(conn, days):
    end = date.today()
    d = end - timedelta(days=days)
    while d <= end:
        if d.weekday() < 5:
            try:
                ok = collect_twse(conn, d)
                print(d, "ok" if ok else "休市")
            except RuntimeError as e:
                print(d, "失敗", e)
        d += timedelta(days=1)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["today", "backfill", "fundamentals", "holders", "news", "report"])
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--date", help="YYYY-MM-DD，today 指令可指定日期")
    a = p.parse_args()
    with db.session() as conn:
        if a.cmd == "today":
            collect_today(conn, date.fromisoformat(a.date) if a.date else None)
        elif a.cmd == "backfill":
            backfill(conn, a.days)
            collect_tpex_latest(conn)
            collect_fundamentals(conn)
            collect_holders(conn)
            make_report(conn)
        elif a.cmd == "holders":
            collect_holders(conn)
        elif a.cmd == "news":
            make_report(conn, with_news=True)
        elif a.cmd == "report":
            make_report(conn, with_news=False)
        else:
            collect_fundamentals(conn)


if __name__ == "__main__":
    main()
