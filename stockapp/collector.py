"""盤後資料收集。

用法：
    python -m stockapp.collector today            # 收今天（收盤後 16:30 以後執行最完整）
    python -m stockapp.collector backfill --days 90   # 回補最近 90 個日曆天的上市資料
    python -m stockapp.collector fundamentals     # 更新本益比、殖利率、月營收
"""
import argparse
from datetime import date, datetime, timedelta

from . import db, sources, tools


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


def collect_today(conn, d=None):
    d = d or date.today()
    if d.weekday() >= 5:
        print(f"{d} 是週末，略過")
        return False
    opened = collect_twse(conn, d, force=True)
    if opened:
        collect_tpex_latest(conn)
        collect_fundamentals(conn)
        try:
            tools.collect(conn, d)  # 除權息、行事曆、產業別、季報
        except Exception as e:  # noqa: BLE001 - 附加資料失敗不影響主要收集
            print("[附加資料] 失敗", e)
    print(f"{d} {'完成' if opened else '休市'}")
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
    p.add_argument("cmd", choices=["today", "backfill", "fundamentals"])
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
        else:
            collect_fundamentals(conn)


if __name__ == "__main__":
    main()
