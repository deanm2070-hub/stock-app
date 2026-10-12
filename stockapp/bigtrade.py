"""盤中大單偵測：比較前後兩次即時報價快照的累積成交量，量差超過門檻就提示。

證交所 MIS 免費報價約 5 秒一筆快照，不是逐筆成交明細，所以兩次快照之間的
多筆成交會被合併成一筆量差。量差剛好等於最後一筆成交量（tv）時標為「單筆」，
否則標為「累計」，表示可能是幾筆成交加總。

內外盤判斷用「上一次快照」的最佳買賣價：成交價 ≥ 上次最佳賣價為外盤（主動買），
≤ 上次最佳買價為內盤（主動賣）；落在中間時再看價格比上次漲或跌。
"""
import json
import os
import threading

DEFAULT_SETTINGS = {
    "enabled": True,
    "min_lots": 50,             # 量差至少幾張
    "min_amount": 10_000_000,   # 量差金額至少幾元（張數 × 1000 × 成交價）
}

SIDE_TEXT = {"buy": "外盤大買", "sell": "內盤大賣", "neutral": "大量成交"}


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


class Detector:
    """餵入每次抓到的報價，回傳新產生的大單提示。執行緒安全。"""

    def __init__(self, settings=None):
        self.settings = dict(settings or DEFAULT_SETTINGS)
        self._last = {}  # code -> 上一次快照
        self._lock = threading.Lock()

    def feed(self, quotes):
        alerts = []
        with self._lock:
            for q in quotes:
                a = self._feed_one(q)
                if a:
                    alerts.append(a)
        return alerts

    def _feed_one(self, q):
        code, vol, price = q.get("code"), q.get("volume"), q.get("price")
        if not code or vol is None:
            return None
        day = (q.get("time") or "")[:8]
        prev = self._last.get(code)
        self._last[code] = q
        # 第一次看到、換日或累積量倒退（資料重置）都只當作基準，不提示
        if prev is None or prev.get("volume") is None or (prev.get("time") or "")[:8] != day or vol < prev["volume"]:
            return None
        lots = vol - prev["volume"]
        if lots <= 0 or price is None:
            return None
        s = self.settings
        amount = lots * 1000 * price
        if not s.get("enabled") or lots < (s.get("min_lots") or 0) or amount < (s.get("min_amount") or 0):
            return None

        t = (q.get("time") or "")[9:]
        note = None
        if prev["volume"] == 0:
            side, note = "neutral", "開盤"
        elif t >= "13:30:00":
            side, note = "neutral", "收盤"
        else:
            side = classify(price, prev, q)
        return {
            "date": f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 else None,
            "time": t,
            "code": code,
            "name": q.get("name"),
            "side": side,
            "price": price,
            "lots": lots,
            "amount": round(amount),
            "single": q.get("tick_volume") == lots,
            "note": note,
        }
