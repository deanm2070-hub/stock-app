"""盤中即時提示：大單（外盤大買／內盤大賣）、急拉急殺、觸及漲跌停。

證交所 MIS 免費報價約 5 秒一筆快照，不是逐筆成交明細，所以：
- 大單是比較前後兩次快照的累積成交量。兩次快照之間的多筆成交會被合併成一筆量差，
  量差剛好等於最後一筆成交量（tv）時標為「單筆」，否則標為「累計」。
- 內外盤用「上一次快照」的最佳買賣價判斷：成交價 ≥ 上次最佳賣價為外盤（主動買），
  ≤ 上次最佳買價為內盤（主動賣）；落在中間時再看價格比上次漲或跌。
- 急拉急殺是看最近幾分鐘內的最低價／最高價到現在的漲跌幅，5 秒內的瞬間來回看不到。
"""
import json
import os
import threading
from collections import deque

DEFAULT_SETTINGS = {
    "enabled": True,
    "trade_enabled": True,
    "min_lots": 50,             # 大單：量差至少幾張
    "min_amount": 10_000_000,   # 大單：量差金額至少幾元（張數 × 1000 × 成交價）
    "price_enabled": True,
    "price_window": 2,          # 急拉急殺：看最近幾分鐘
    "price_pct": 2.0,           # 急拉急殺：幾分鐘內漲跌幅至少幾 %
    "limit_enabled": True,      # 觸及漲停、跌停
}

LABELS = {
    ("trade", "buy"): "外盤大買", ("trade", "sell"): "內盤大賣", ("trade", "neutral"): "大量成交",
    ("spike", "buy"): "急拉", ("plunge", "sell"): "急殺",
    ("limit", "buy"): "漲停", ("limit", "sell"): "跌停",
}


def load_settings(path):
    s = dict(DEFAULT_SETTINGS)
    try:
        with open(path, encoding="utf-8") as f:
            s.update({k: v for k, v in json.load(f).items() if k in DEFAULT_SETTINGS})
    except (OSError, ValueError):
        pass
    return s


def save_settings(path, s):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)


def classify(price, prev, cur=None):
    """回傳 'buy' / 'sell' / 'neutral'。prev 是上一次快照，cur 是這次快照（用來判斷漲跌停）。"""
    asks, bids = prev.get("asks") or [], prev.get("bids") or []
    if asks and price >= asks[0]["price"]:
        return "buy"
    if bids and price <= bids[0]["price"]:
        return "sell"
    cur = cur or {}
    # 漲停鎖住沒有賣單、跌停鎖住沒有買單
    if not asks and cur.get("limit_up") and price >= cur["limit_up"]:
        return "buy"
    if not bids and cur.get("limit_down") and price <= cur["limit_down"]:
        return "sell"
    last = prev.get("price")
    if last is not None and price > last:
        return "buy"
    if last is not None and price < last:
        return "sell"
    return "neutral"


def _secs(t):
    try:
        h, m, s = (int(x) for x in t.split(":"))
        return h * 3600 + m * 60 + s
    except (ValueError, AttributeError):
        return None


class Detector:
    """餵入每次抓到的報價，回傳新產生的提示。執行緒安全。"""

    def __init__(self, settings=None):
        self.settings = {**DEFAULT_SETTINGS, **(settings or {})}
        self._last = {}     # code -> 上一次快照
        self._hist = {}     # code -> deque[(秒, 價)]，急拉急殺用
        self._lock = threading.Lock()

    def feed(self, quotes):
        out = []
        with self._lock:
            for q in quotes:
                out += self._feed_one(q)
        return out

    def _feed_one(self, q):
        code, vol, price = q.get("code"), q.get("volume"), q.get("price")
        if not code or vol is None:
            return []
        day, t = (q.get("time") or "")[:8], (q.get("time") or "")[9:]
        prev = self._last.get(code)
        self._last[code] = q
        # 第一次看到、換日或累積量倒退（資料重置）都只當作基準，不提示
        if prev is None or prev.get("volume") is None or (prev.get("time") or "")[:8] != day or vol < prev["volume"]:
            self._hist.pop(code, None)
            if vol and price is not None and _secs(t) is not None:
                self._hist[code] = deque([(_secs(t), price)])
            return []
        if price is None or not self.settings.get("enabled"):
            return []
        base = {"date": f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 else None, "time": t,
                "code": code, "name": q.get("name"), "price": price,
                "lots": None, "amount": None, "single": False, "pct": None, "note": None}
        out = []
        a = self._big_trade(q, prev, base, t)
        if a:
            out.append(a)
        if vol > 0:  # 有成交後才看價格；開盤前試撮的價格不算
            lim = self._limit(q, prev, base)
            move = self._price_move(code, price, t, base)
            a = lim or move  # 同時觸及漲跌停時只報漲跌停
            if a:
                out.append(a)
        return out

    def _big_trade(self, q, prev, base, t):
        s = self.settings
        lots = q["volume"] - prev["volume"]
        if not s.get("trade_enabled") or lots <= 0:
            return None
        amount = lots * 1000 * q["price"]
        if lots < (s.get("min_lots") or 0) or amount < (s.get("min_amount") or 0):
            return None
        note = None
        if prev["volume"] == 0:
            side, note = "neutral", "開盤"
        elif t >= "13:30:00":
            side, note = "neutral", "收盤"
        else:
            side = classify(q["price"], prev, q)
        return {**base, "kind": "trade", "side": side, "lots": lots, "amount": round(amount),
                "single": q.get("tick_volume") == lots, "note": note}

    def _limit(self, q, prev, base):
        """剛碰到漲停或跌停時提示一次；打開後再鎖回去會再提示。"""
        if not self.settings.get("limit_enabled"):
            return None
        price, up, down = q["price"], q.get("limit_up"), q.get("limit_down")
        was = prev.get("price") if prev.get("volume") else None
        pct = q.get("pct")
        if up and price >= up and not (was is not None and was >= up):
            return {**base, "kind": "limit", "side": "buy", "pct": pct}
        if down and price <= down and not (was is not None and was <= down):
            return {**base, "kind": "limit", "side": "sell", "pct": pct}
        return None

    def _price_move(self, code, price, t, base):
        """最近 price_window 分鐘內從低點拉上來或從高點殺下來超過 price_pct %。提示後重新起算，避免一路連發。"""
        s = self.settings
        now = _secs(t)
        if now is None:
            return None
        h = self._hist.setdefault(code, deque())
        h.append((now, price))
        while h and now - h[0][0] > (s.get("price_window") or 0) * 60:
            h.popleft()
        if not s.get("price_enabled") or not s.get("price_pct"):
            return None
        lo, hi = min(p for _, p in h), max(p for _, p in h)
        rise, fall = (price - lo) / lo * 100, (price - hi) / hi * 100
        if rise >= s["price_pct"]:
            kind, side, pct, ref = "spike", "buy", rise, lo
        elif fall <= -s["price_pct"]:
            kind, side, pct, ref = "plunge", "sell", fall, hi
        else:
            return None
        h.clear()
        h.append((now, price))
        return {**base, "kind": kind, "side": side, "pct": round(pct, 2),
                "note": f"{s['price_window']} 分鐘內從 {ref:g}"}
