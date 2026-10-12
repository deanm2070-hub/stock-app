"""公開資料來源：證交所（TWSE）、櫃買中心（TPEx）、證交所 MIS 盤中報價。

抓取（fetch_*）與解析（parse_*）分開，解析函式只吃 JSON，方便測試。
證交所對頻繁請求會暫時封鎖 IP，所有請求都經過 _get() 做節流。
"""
import re
import threading
import time
from datetime import date

import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
MIN_INTERVAL = 3.0  # 秒，證交所盤後資料兩次請求的最小間隔

_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept": "application/json,text/plain,*/*"})
_lock = threading.Lock()
_last_call = {"t": 0.0}


def _get(url, params=None, throttle=True, timeout=20):
    if throttle:
        with _lock:
            wait = MIN_INTERVAL - (time.time() - _last_call["t"])
            if wait > 0:
                time.sleep(wait)
            _last_call["t"] = time.time()
    last_err = None
    for attempt in range(3):
        try:
            r = _session.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"抓取失敗 {url}: {last_err}")


# ---------- 共用小工具 ----------

def num(v):
    """'1,234.5' -> 1234.5；'--'、''、'X' -> None。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    s = re.sub(r"<[^>]+>", "", str(v)).replace(",", "").strip()
    if s in ("", "--", "---", "-", "X", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def inum(v):
    n = num(v)
    return int(n) if n is not None else None


def is_common_stock(code):
    """只保留 4 碼普通股（排除 ETF 00xx、權證、特別股等）。"""
    return bool(re.fullmatch(r"[1-9]\d{3}", code or ""))


def ymd(d: date):
    return d.strftime("%Y%m%d")


def roc_date(d: date, sep="/"):
    return f"{d.year - 1911}{sep}{d:%m}{sep}{d:%d}"


def _field_index(fields, *candidates, startswith=False):
    for c in candidates:
        for i, f in enumerate(fields):
            f2 = f.strip()
            if (startswith and f2.startswith(c)) or f2 == c:
                return i
    return None


# ---------- 證交所：每日收盤行情 + 大盤 ----------

def fetch_twse_daily(d: date):
    return _get("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
                {"date": ymd(d), "type": "ALLBUT0999", "response": "json"})


def parse_twse_daily(js, d: date):
    """回傳 (prices, stocks, market)。休市日回傳 ([], [], None)。"""
    if not js or js.get("stat") != "OK":
        return [], [], None
    ds = d.isoformat()
    prices, stocks, market = [], [], {"date": ds}
    for t in js.get("tables", []):
        title = t.get("title") or ""
        fields = t.get("fields") or []
        data = t.get("data") or []
        if "每日收盤行情" in title and "證券代號" in fields:
            fi = {f: i for i, f in enumerate(fields)}
            sign_i = _field_index(fields, "漲跌(+/-)")
            for row in data:
                code = row[fi["證券代號"]].strip()
                if not is_common_stock(code):
                    continue
                chg = num(row[fi["漲跌價差"]])
                if chg is not None and sign_i is not None and "-" in re.sub(r"<[^>]+>", "", row[sign_i]):
                    chg = -chg
                prices.append({
                    "date": ds, "code": code,
                    "open": num(row[fi["開盤價"]]), "high": num(row[fi["最高價"]]),
                    "low": num(row[fi["最低價"]]), "close": num(row[fi["收盤價"]]),
                    "volume": inum(row[fi["成交股數"]]), "value": inum(row[fi["成交金額"]]),
                    "change": chg,
                })
                stocks.append({"code": code, "name": row[fi["證券名稱"]].strip(), "market": "tse"})
        elif "價格指數" in title and "臺灣證券交易所" in title:
            for row in data:
                if row and row[0].strip() == "發行量加權股價指數":
                    fi = {f: i for i, f in enumerate(fields)}
                    sign = row[fi.get("漲跌(+/-)", 2)]
                    neg = "-" in re.sub(r"<[^>]+>", "", sign)
                    pts = num(row[fi.get("漲跌點數", 3)]) or 0
                    pct = num(row[fi.get("漲跌百分比(%)", 4)]) or 0
                    market["taiex"] = num(row[fi.get("收盤指數", 1)])
                    market["taiex_change"] = -pts if neg else pts
                    market["taiex_pct"] = -abs(pct) if neg else pct
        elif "漲跌證券數" in title:
            col = _field_index(fields, "股票") or 1
            for row in data:
                label = row[0]
                m = re.match(r"([\d,]+)", row[col] or "")
                if not m:
                    continue
                if label.startswith("上漲"):
                    market["up_count"] = int(m.group(1).replace(",", ""))
                elif label.startswith("下跌"):
                    market["down_count"] = int(m.group(1).replace(",", ""))
        elif "大盤統計資訊" in title:
            vi = _field_index(fields, "成交金額(元)", "成交金額")
            for row in data:
                if row and row[0].startswith("總計") and vi is not None:
                    market["total_value"] = inum(row[vi])
    return prices, stocks, (market if "taiex" in market else None)


# ---------- 證交所：三大法人個股買賣超 ----------

def fetch_twse_institutional(d: date):
    return _get("https://www.twse.com.tw/rwd/zh/fund/T86",
                {"date": ymd(d), "selectType": "ALLBUT0999", "response": "json"})


def parse_twse_institutional(js, d: date):
    if not js or js.get("stat") != "OK":
        return []
    fields = js.get("fields") or []
    i_code = _field_index(fields, "證券代號")
    i_fx = _field_index(fields, "外陸資買賣超股數", startswith=True)
    i_fxd = _field_index(fields, "外資自營商買賣超股數")
    i_trust = _field_index(fields, "投信買賣超股數")
    i_dealer = _field_index(fields, "自營商買賣超股數")
    i_total = _field_index(fields, "三大法人買賣超股數")
    out = []
    for row in js.get("data") or []:
        code = row[i_code].strip()
        if not is_common_stock(code):
            continue
        fx = (inum(row[i_fx]) or 0) + ((inum(row[i_fxd]) or 0) if i_fxd is not None else 0)
        out.append({
            "date": d.isoformat(), "code": code,
            "foreign_net": fx,
            "trust_net": inum(row[i_trust]) or 0,
            "dealer_net": inum(row[i_dealer]) or 0,
            "total_net": inum(row[i_total]) or 0,
        })
    return out


def fetch_twse_market_institutional(d: date):
    return _get("https://www.twse.com.tw/rwd/zh/fund/BFI82U",
                {"dayDate": ymd(d), "type": "day", "response": "json"})


def parse_twse_market_institutional(js):
    """回傳 {'foreign_amt','trust_amt','dealer_amt'}（買賣差額，元）。"""
    if not js or js.get("stat") != "OK":
        return {}
    out = {"foreign_amt": 0, "trust_amt": 0, "dealer_amt": 0}
    for row in js.get("data") or []:
        name, diff = row[0].strip(), inum(row[-1]) or 0
        if name.startswith("外資"):
            out["foreign_amt"] += diff
        elif name.startswith("投信"):
            out["trust_amt"] += diff
        elif name.startswith("自營商"):
            out["dealer_amt"] += diff
    return out


# ---------- 證交所：融資融券 ----------

def fetch_twse_margin(d: date):
    return _get("https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN",
                {"date": ymd(d), "selectType": "ALL", "response": "json"})


def parse_twse_margin(js, d: date):
    """個股表欄位：代號,名稱,[融資]買進,賣出,現金償還,前日餘額,今日餘額,限額,[融券]買進,賣出,現券償還,前日餘額,今日餘額,..."""
    if not js or js.get("stat") != "OK":
        return []
    tables = js.get("tables") or [{"fields": js.get("fields"), "data": js.get("data")}]
    for t in tables:
        fields = t.get("fields") or []
        if fields and fields[0].strip() == "代號":
            out = []
            for row in t.get("data") or []:
                code = row[0].strip()
                if not is_common_stock(code) or len(row) < 13:
                    continue
                out.append({"date": d.isoformat(), "code": code,
                            "margin_balance": inum(row[6]), "short_balance": inum(row[12])})
            return out
    return []


# ---------- 櫃買中心（OpenAPI 只提供最新一個交易日） ----------

TPEX_OPENAPI = "https://www.tpex.org.tw/openapi/v1/"


def _pick(row, *keys):
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return None


def _pick_like(row, *needles):
    """依欄位名稱關鍵字找值（櫃買 OpenAPI 欄位名稱偶有調整）。"""
    for k, v in row.items():
        kl = k.lower()
        if all(n.lower() in kl for n in needles):
            return v
    return None


def parse_roc_compact(s):
    """'1131011' 或 '113/10/11' -> date。"""
    s = re.sub(r"\D", "", str(s or ""))
    if len(s) == 7:
        return date(int(s[:3]) + 1911, int(s[3:5]), int(s[5:7]))
    return None


def fetch_tpex_daily():
    return _get(TPEX_OPENAPI + "tpex_mainboard_daily_close_quotes")


def parse_tpex_daily(rows):
    prices, stocks = [], []
    for r in rows or []:
        code = (_pick(r, "SecuritiesCompanyCode", "Code") or "").strip()
        if not is_common_stock(code):
            continue
        d = parse_roc_compact(_pick(r, "Date"))
        if not d:
            continue
        prices.append({
            "date": d.isoformat(), "code": code,
            "open": num(_pick(r, "Open")), "high": num(_pick(r, "High")),
            "low": num(_pick(r, "Low")), "close": num(_pick(r, "Close")),
            "volume": inum(_pick(r, "TradingShares", "TradeVolume")),
            "value": inum(_pick(r, "TransactionAmount", "TradeValue")),
            "change": num(_pick(r, "Change")),
        })
        stocks.append({"code": code, "name": (_pick(r, "CompanyName", "Name") or "").strip(), "market": "otc"})
    return prices, stocks


def fetch_tpex_institutional():
    return _get(TPEX_OPENAPI + "tpex_3insti_daily_trading")


def parse_tpex_institutional(rows):
    out = []
    for r in rows or []:
        code = (_pick(r, "SecuritiesCompanyCode", "Code") or "").strip()
        d = parse_roc_compact(_pick(r, "Date"))
        if not is_common_stock(code) or not d:
            continue
        fx = inum(_pick_like(r, "foreign", "difference")) or inum(_pick_like(r, "foreign", "net")) or 0
        trust = inum(_pick_like(r, "investmenttrust", "difference")) or inum(_pick_like(r, "trust", "net")) or 0
        dealer = inum(_pick_like(r, "dealers", "difference")) or inum(_pick_like(r, "dealer", "net")) or 0
        total = inum(_pick_like(r, "total", "difference")) or (fx + trust + dealer)
        out.append({"date": d.isoformat(), "code": code, "foreign_net": fx,
                    "trust_net": trust, "dealer_net": dealer, "total_net": total})
    return out


def fetch_tpex_margin():
    return _get(TPEX_OPENAPI + "tpex_mainboard_margin_balance")


def parse_tpex_margin(rows):
    out = []
    for r in rows or []:
        code = (_pick(r, "SecuritiesCompanyCode", "Code") or "").strip()
        d = parse_roc_compact(_pick(r, "Date"))
        if not is_common_stock(code) or not d:
            continue
        out.append({"date": d.isoformat(), "code": code,
                    "margin_balance": inum(_pick_like(r, "margin", "balance", "today")
                                           or _pick_like(r, "marginpurchase", "balance")),
                    "short_balance": inum(_pick_like(r, "short", "balance", "today")
                                          or _pick_like(r, "shortsale", "balance"))})
    return out


# ---------- 基本面：本益比 / 殖利率 / 月營收 ----------

def fetch_fundamentals():
    """回傳 {code: {...}}。四個 OpenAPI 端點，個別失敗不影響其他。"""
    out = {}

    def merge(code, **kw):
        if is_common_stock(code):
            out.setdefault(code, {}).update({k: v for k, v in kw.items() if v is not None})

    for url, kc, kpe, kyd, kpb in [
        ("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", "Code", "PEratio", "DividendYield", "PBratio"),
        (TPEX_OPENAPI + "tpex_mainboard_peratio_analysis", "SecuritiesCompanyCode", "PriceEarningRatio", "YieldRatio", "PriceBookRatio"),
    ]:
        try:
            for r in _get(url):
                merge((r.get(kc) or "").strip(), pe=num(r.get(kpe)), dividend_yield=num(r.get(kyd)), pb=num(r.get(kpb)))
        except RuntimeError as e:
            print("[基本面]", e)

    for url in ["https://openapi.twse.com.tw/v1/opendata/t187ap05_L", TPEX_OPENAPI + "mopsfin_t187ap05_O"]:
        try:
            for r in _get(url):
                merge((r.get("公司代號") or "").strip(),
                      rev_month=r.get("資料年月"),
                      rev_yoy=num(r.get("營業收入-去年同月增減(%)")),
                      rev_mom=num(r.get("營業收入-上月比較增減(%)")))
        except RuntimeError as e:
            print("[月營收]", e)
    return out


# ---------- 盤中即時報價（證交所 MIS，約 5 秒更新） ----------

MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
_mis_ready = {"ok": False}


def fetch_realtime(codes_with_market):
    """codes_with_market: [('2330','tse'), ('6488','otc')]。回傳 parse_realtime 結果。"""
    if not codes_with_market:
        return []
    if not _mis_ready["ok"]:
        try:
            _session.get("https://mis.twse.com.tw/stock/index.jsp", timeout=10)
            _mis_ready["ok"] = True
        except requests.RequestException:
            pass
    ex_ch = "|".join(f"{m}_{c}.tw" for c, m in codes_with_market)
    js = _get(MIS_URL, {"ex_ch": ex_ch, "json": 1, "delay": 0, "_": int(time.time() * 1000)}, throttle=False, timeout=10)
    return parse_realtime(js)


def _levels(prices, vols):
    ps = [num(x) for x in (prices or "").split("_") if x]
    vs = [inum(x) for x in (vols or "").split("_") if x]
    return [{"price": p, "vol": v} for p, v in zip(ps, vs) if p]


def parse_realtime(js):
    out = []
    for m in (js or {}).get("msgArray", []):
        bids = _levels(m.get("b"), m.get("g"))
        asks = _levels(m.get("a"), m.get("f"))
        last = num(m.get("z"))
        if last is None:  # 尚未成交或試撮，用最佳買價或開盤價代替
            last = bids[0]["price"] if bids else num(m.get("o"))
        prev = num(m.get("y"))
        out.append({
            "code": m.get("c"), "name": m.get("n"), "market": m.get("ex"),
            "price": last, "prev_close": prev,
            "change": round(last - prev, 2) if last is not None and prev else None,
            "pct": round((last - prev) / prev * 100, 2) if last is not None and prev else None,
            "open": num(m.get("o")), "high": num(m.get("h")), "low": num(m.get("l")),
            "volume": inum(m.get("v")),  # 累積成交量（張）
            "tick_volume": inum(m.get("tv")),
            "limit_up": num(m.get("u")), "limit_down": num(m.get("w")),
            "bids": bids, "asks": asks,
            "time": f"{m.get('d', '')} {m.get('t', '')}".strip(),
        })
    return out
