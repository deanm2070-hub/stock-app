"""網頁伺服器：python run.py 後開 http://127.0.0.1:8000"""
import json
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import alerts, analysis, collector, db, sources

STATIC = os.path.join(os.path.dirname(__file__), "static")
WATCHLIST = os.path.join(os.path.dirname(db.DB_PATH), "watchlist.json")
DEFAULT_WATCH = ["2330", "2317", "2454", "2308", "2881"]
TW = timezone(timedelta(hours=8))

app = FastAPI(title="台股籌碼看盤")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
conn = db.connect()
conn_lock = threading.Lock()


def q(sql, args=()):
    with conn_lock:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


# ---------- 個股 ----------

@app.get("/api/stocks")
def search(q_: str = Query("", alias="q"), limit: int = 20):
    like = f"%{q_}%"
    return q("SELECT code,name,market FROM stocks WHERE code LIKE ? OR name LIKE ? ORDER BY code LIMIT ?",
             (like, like, limit))


@app.get("/api/stock/{code}")
def stock_info(code: str):
    s = q("SELECT * FROM stocks WHERE code=?", (code,))
    f = q("SELECT * FROM fundamentals WHERE code=?", (code,))
    if not s:
        raise HTTPException(404, "查無此股票，請先收集資料")
    return {**s[0], "fundamentals": f[0] if f else None}


@app.get("/api/kline/{code}")
def kline(code: str, limit: int = 240):
    with conn_lock:
        return analysis.kline(conn, code, limit)


_quote_cache = {}  # code -> (抓取時間, 報價)
_quote_lock = threading.Lock()
QUOTE_TTL = 4


def get_quotes(codes):
    """抓即時報價，每檔 4 秒內走快取，避免被證交所封鎖。新抓到的報價順便送去偵測盤中提示。"""
    with _quote_lock:
        now = time.time()
        need = [c for c in codes if c not in _quote_cache or now - _quote_cache[c][0] >= QUOTE_TTL]
        if need:
            markets = {r["code"]: r["market"] for r in q(
                f"SELECT code,market FROM stocks WHERE code IN ({','.join('?' * len(need))})", need)}
            pairs = [(c, markets.get(c) or "tse") for c in need]
            data = sources.fetch_realtime(pairs)
            # 不知道市場別又查不到的，改用上櫃再試一次
            got = {d["code"] for d in data}
            retry = [(c, "otc") for c, m in pairs if c not in got and not markets.get(c)]
            if retry:
                try:
                    data += sources.fetch_realtime(retry)
                except RuntimeError:
                    pass
            now = time.time()
            for d in data:
                _quote_cache[d["code"]] = (now, d)
            _record_alerts(detector.feed(data))
        return [_quote_cache[c][1] for c in codes if c in _quote_cache]


@app.get("/api/quote")
def quote(codes: str):
    """盤中即時報價。"""
    codes = [c.strip() for c in codes.split(",") if c.strip()][:50]
    try:
        return get_quotes(codes)
    except RuntimeError as e:
        raise HTTPException(502, str(e))


# ---------- 盤中提示（大單、急拉急殺、漲跌停） ----------

ALERT_SETTINGS = os.path.join(os.path.dirname(db.DB_PATH), "alerts.json")
detector = alerts.Detector(alerts.load_settings(ALERT_SETTINGS))
ALERT_COLS = ["date", "time", "code", "name", "kind", "side", "price", "lots", "amount", "single", "pct", "note"]


def _record_alerts(rows):
    if not rows:
        return
    with conn_lock:
        conn.executemany(f"INSERT INTO alerts({','.join(ALERT_COLS)}) VALUES({','.join('?' * len(ALERT_COLS))})",
                         [tuple(int(r[c]) if c == "single" else r[c] for c in ALERT_COLS) for r in rows])
        conn.commit()


@app.get("/api/alerts")
def get_alerts(since: int = 0, limit: int = 300):
    """since=上次拿到的最大 id，只回傳更新的；since=0 回傳最近一個有資料的交易日。"""
    if since:
        rows = q("SELECT * FROM alerts WHERE id>? ORDER BY id LIMIT ?", (since, limit))
    else:
        rows = q("SELECT * FROM alerts WHERE date=(SELECT MAX(date) FROM alerts) ORDER BY id DESC LIMIT ?",
                 (limit,))[::-1]
    for r in rows:
        r["single"] = bool(r["single"])
    return rows


class AlertSettings(BaseModel):
    enabled: bool = True
    trade_enabled: bool = True
    min_lots: int = 50
    min_amount: int = 10_000_000
    price_enabled: bool = True
    price_window: int = 2
    price_pct: float = 2.0
    limit_enabled: bool = True


@app.get("/api/alerts/settings")
def get_alert_settings():
    return detector.settings


@app.post("/api/alerts/settings")
def set_alert_settings(s: AlertSettings):
    detector.settings = dict(s)
    alerts.save_settings(ALERT_SETTINGS, detector.settings)
    return detector.settings


def _is_trading_time(now=None):
    now = now or datetime.now(TW)
    return now.weekday() < 5 and (9, 0) <= (now.hour, now.minute) <= (13, 31)


def _monitor():
    """盤中每 5 秒抓一次自選股，就算網頁沒開或在背景也會偵測。"""
    while True:
        if detector.settings.get("enabled") and _is_trading_time():
            try:
                get_quotes(get_watch()[:50])
            except Exception:  # noqa: BLE001 - 網路錯誤下一輪再試
                pass
        time.sleep(5)


# ---------- 自選股 ----------

class Watch(BaseModel):
    codes: list[str]


@app.get("/api/watchlist")
def get_watch():
    try:
        with open(WATCHLIST, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return DEFAULT_WATCH


@app.post("/api/watchlist")
def set_watch(w: Watch):
    os.makedirs(os.path.dirname(WATCHLIST), exist_ok=True)
    with open(WATCHLIST, "w", encoding="utf-8") as f:
        json.dump(w.codes, f)
    return w.codes


# ---------- 盤後分析 ----------

@app.get("/api/market")
def market(date_: str | None = Query(None, alias="date")):
    with conn_lock:
        return analysis.market_summary(conn, until=date_) or {}


@app.get("/api/screen")
def screen(date_: str | None = Query(None, alias="date"), min_score: float = 4, top: int = 30, quality_only: bool = False):
    with conn_lock:
        return analysis.screen(conn, until=date_, top=top, min_score=min_score, quality_only=quality_only)


# ---------- 資料收集 ----------

_job = {"running": False, "last": None, "error": None}


def _run_collect(d=None):
    if _job["running"]:
        return
    _job.update(running=True, error=None)
    try:
        c = db.connect()  # 收集用獨立連線，不卡住查詢
        collector.collect_today(c, d)
        c.close()
        _job["last"] = datetime.now().isoformat(timespec="seconds")
    except Exception as e:  # noqa: BLE001 - 收集失敗要回報給前端
        _job["error"] = str(e)
    finally:
        _job["running"] = False


@app.post("/api/collect")
def collect_now():
    threading.Thread(target=_run_collect, daemon=True).start()
    return {"started": True}


@app.get("/api/status")
def status():
    d = q("SELECT MAX(date) d, COUNT(DISTINCT date) n FROM daily_price")[0]
    return {**_job, "latest_date": d["d"], "trading_days": d["n"]}


# 每個交易日 16:45 收盤後資料、21:45 再補一次融資融券（晚上才公布）
SCHEDULE = [(16, 45), (21, 45)]


def _scheduler():
    fired = set()
    while True:
        now = datetime.now()
        for hh, mm in SCHEDULE:
            key = (now.date(), hh, mm)
            if now.weekday() < 5 and (now.hour, now.minute) >= (hh, mm) and key not in fired:
                fired.add(key)
                if (now.hour, now.minute) < (hh + 1, mm):  # 只在排程時間後一小時內補跑
                    _run_collect(date.today())
        time.sleep(30)


if os.environ.get("STOCKAPP_NO_SCHEDULER") != "1":
    threading.Thread(target=_scheduler, daemon=True).start()
    threading.Thread(target=_monitor, daemon=True).start()
