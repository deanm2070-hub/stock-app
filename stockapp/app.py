"""網頁伺服器：python run.py 後開 http://127.0.0.1:8000"""
import json
import os
import threading
import time
from datetime import date, datetime

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analysis, collector, db, sources

STATIC = os.path.join(os.path.dirname(__file__), "static")
WATCHLIST = os.path.join(os.path.dirname(db.DB_PATH), "watchlist.json")
DEFAULT_WATCH = ["2330", "2317", "2454", "2308", "2881"]

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


_quote_cache = {}


@app.get("/api/quote")
def quote(codes: str):
    """盤中即時報價，同一組股票 4 秒內重複查詢走快取，避免被證交所封鎖。"""
    codes = [c.strip() for c in codes.split(",") if c.strip()][:50]
    key = ",".join(codes)
    hit = _quote_cache.get(key)
    if hit and time.time() - hit[0] < 4:
        return hit[1]
    markets = {r["code"]: r["market"] for r in q(
        f"SELECT code,market FROM stocks WHERE code IN ({','.join('?' * len(codes))})", codes)} if codes else {}
    pairs = [(c, markets.get(c) or "tse") for c in codes]
    try:
        data = sources.fetch_realtime(pairs)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    # 不知道市場別又查不到的，改用上櫃再試一次
    got = {d["code"] for d in data}
    retry = [(c, "otc") for c, m in pairs if c not in got and not markets.get(c)]
    if retry:
        try:
            data += sources.fetch_realtime(retry)
        except RuntimeError:
            pass
    _quote_cache[key] = (time.time(), data)
    return data


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
