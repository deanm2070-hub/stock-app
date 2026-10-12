"""技術指標（對應新世紀的技術分析指標），全部只用日 K 與籌碼資料計算。

每個函式吃 list（可含 None），回傳等長 list，資料不足的位置為 None。
INDICATORS 是給圖表用的指標表：名稱 -> (中文名, 計算函式, 是否疊在主圖)。
"""
import math

# ---------- 序列工具 ----------


def _valid(xs):
    return [x for x in xs if x is not None]


def ma(xs, n):
    out, s, cnt = [None] * len(xs), 0.0, 0
    win = []
    for i, x in enumerate(xs):
        win.append(x)
        if x is not None:
            s += x
            cnt += 1
        if len(win) > n:
            y = win.pop(0)
            if y is not None:
                s -= y
                cnt -= 1
        if len(win) == n and cnt == n:
            out[i] = s / n
    return out


def ema(xs, n):
    """指數移動平均，以前 n 筆簡單平均作為起點。"""
    out = [None] * len(xs)
    k = 2 / (n + 1)
    prev, seed = None, []
    for i, x in enumerate(xs):
        if x is None:
            continue
        if prev is None:
            seed.append(x)
            if len(seed) == n:
                prev = sum(seed) / n
                out[i] = prev
            continue
        prev = prev + k * (x - prev)
        out[i] = prev
    return out


def sma(xs, n, m=1):
    """中式 SMA：Y = (m*X + (n-m)*Y') / n，常用於 KD、RSI 平滑。"""
    out, prev = [None] * len(xs), None
    for i, x in enumerate(xs):
        if x is None:
            continue
        prev = x if prev is None else (m * x + (n - m) * prev) / n
        out[i] = prev
    return out


def ref(xs, n):
    n = int(n)
    return [xs[i - n] if 0 <= i - n < len(xs) else None for i in range(len(xs))]


def _window(xs, n, fn):
    n = int(n)
    out = [None] * len(xs)
    for i in range(n - 1, len(xs)):
        w = xs[i - n + 1:i + 1]
        if None not in w:
            out[i] = fn(w)
    return out


def hhv(xs, n):
    return _window(xs, n, max)


def llv(xs, n):
    return _window(xs, n, min)


def msum(xs, n):
    return _window(xs, n, sum)


def std(xs, n):
    def f(w):
        m = sum(w) / len(w)
        return math.sqrt(sum((x - m) ** 2 for x in w) / len(w))
    return _window(xs, n, f)


def _op(a, b, fn):
    return [fn(x, y) if x is not None and y is not None else None for x, y in zip(a, b)]


def _div(a, b):
    return [x / y if x is not None and y not in (None, 0) else None for x, y in zip(a, b)]


def _r(xs, d=2):
    return [round(x, d) if x is not None else None for x in xs]


# ---------- 指標 ----------


def kd(b, n=9, k=3, d=3):
    lo, hi = llv(b["low"], n), hhv(b["high"], n)
    rsv = [((c - l) / (h - l) * 100 if h != l else 50) if None not in (c, l, h) else None
           for c, l, h in zip(b["close"], lo, hi)]
    K, D, pk, pd = [None] * len(rsv), [None] * len(rsv), 50.0, 50.0
    for i, r in enumerate(rsv):
        if r is None:
            continue
        pk = pk * (k - 1) / k + r / k
        pd = pd * (d - 1) / d + pk / d
        K[i], D[i] = pk, pd
    return {"K": _r(K), "D": _r(D)}


def macd(b, fast=12, slow=26, sig=9):
    dif = _op(ema(b["close"], fast), ema(b["close"], slow), lambda x, y: x - y)
    dea = ema(dif, sig)
    osc = _op(dif, dea, lambda x, y: x - y)
    return {"DIF": _r(dif), "MACD": _r(dea), "OSC": _r(osc, 3)}


def rsi_line(closes, n):
    diff = [None] + [c - p if None not in (c, p) else None for p, c in zip(closes, closes[1:])]
    up = sma([max(x, 0) if x is not None else None for x in diff], n)
    dn = sma([max(-x, 0) if x is not None else None for x in diff], n)
    return [(u / (u + d) * 100 if u + d else 50) if None not in (u, d) else None for u, d in zip(up, dn)]


def rsi(b, n1=6, n2=12):
    return {f"RSI{n1}": _r(rsi_line(b["close"], n1)), f"RSI{n2}": _r(rsi_line(b["close"], n2))}


def bias(b, n1=10, n2=20):
    c = b["close"]
    return {f"BIAS{n}": _r(_op(c, ma(c, n), lambda x, m: (x - m) / m * 100)) for n in (n1, n2)}


def boll(b, n=20, k=2):
    mid, sd = ma(b["close"], n), std(b["close"], n)
    return {"上軌": _r(_op(mid, sd, lambda m, s: m + k * s)), "中軌": _r(mid),
            "下軌": _r(_op(mid, sd, lambda m, s: m - k * s))}


def mas(b):
    return {f"MA{n}": _r(ma(b["close"], n)) for n in (5, 10, 20, 60, 120, 240)}


def wr(b, n1=14, n2=28):
    out = {}
    for n in (n1, n2):
        hi, lo = hhv(b["high"], n), llv(b["low"], n)
        out[f"WR{n}"] = _r([((h - c) / (h - l) * 100 if h != l else 50) if None not in (h, l, c) else None
                            for h, l, c in zip(hi, lo, b["close"])])
    return out


def _tr(b):
    h, l, c = b["high"], b["low"], b["close"]
    out = [None] * len(c)
    for i in range(len(c)):
        if None in (h[i], l[i]):
            continue
        pc = c[i - 1] if i and c[i - 1] is not None else c[i]
        out[i] = max(h[i] - l[i], abs(h[i] - pc), abs(l[i] - pc))
    return out


def dmi(b, n=14):
    h, l = b["high"], b["low"]
    pdm, mdm = [None], [None]
    for i in range(1, len(h)):
        if None in (h[i], h[i - 1], l[i], l[i - 1]):
            pdm.append(None)
            mdm.append(None)
            continue
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm.append(up if up > dn and up > 0 else 0)
        mdm.append(dn if dn > up and dn > 0 else 0)
    tr = sma(_tr(b), n)
    pdi = [x * 100 if x is not None else None for x in _div(sma(pdm, n), tr)]
    mdi = [x * 100 if x is not None else None for x in _div(sma(mdm, n), tr)]
    dx = [abs(p - m) / (p + m) * 100 if None not in (p, m) and p + m else None for p, m in zip(pdi, mdi)]
    adx = sma(dx, n)
    # 前 n 筆的平滑還不穩定，不顯示
    for arr in (pdi, mdi, adx):
        for i in range(min(n, len(arr))):
            arr[i] = None
    return {"+DI": _r(pdi), "-DI": _r(mdi), "ADX": _r(adx)}


def atr(b, n=14):
    return {f"ATR{n}": _r(ma(_tr(b), n))}


def obv(b):
    c, v = b["close"], b["volume"]
    out, s = [None] * len(c), 0.0
    for i in range(len(c)):
        if c[i] is None or v[i] is None:
            continue
        if i and c[i - 1] is not None:
            s += v[i] if c[i] > c[i - 1] else -v[i] if c[i] < c[i - 1] else 0
        out[i] = s
    return {"OBV": _r(out, 0), "OBV MA20": _r(ma(out, 20), 0)}


def cci(b, n=14):
    tp = [(h + l + c) / 3 if None not in (h, l, c) else None for h, l, c in zip(b["high"], b["low"], b["close"])]
    m = ma(tp, n)
    md = _window(tp, n, lambda w: sum(abs(x - sum(w) / len(w)) for x in w) / len(w))
    return {f"CCI{n}": _r([(t - a) / (0.015 * d) if None not in (t, a, d) and d else None for t, a, d in zip(tp, m, md)])}


def psy(b, n=12):
    c = b["close"]
    up = [None] + [1 if x > p else 0 if None not in (x, p) else None for p, x in zip(c, c[1:])]
    s = msum(up, n)
    p = [x / n * 100 if x is not None else None for x in s]
    return {f"PSY{n}": _r(p), "PSY MA6": _r(ma(p, 6))}


def mtm(b, n=10, m=10):
    c = b["close"]
    v = _op(c, ref(c, n), lambda x, y: x - y)
    return {f"MTM{n}": _r(v), f"MTM MA{m}": _r(ma(v, m))}


def roc(b, n=12):
    c = b["close"]
    v = _op(c, ref(c, n), lambda x, y: (x - y) / y * 100 if y else None)
    return {f"ROC{n}": _r(v), "ROC MA6": _r(ma(v, 6))}


def arbr(b, n=26):
    o, h, l, c = b["open"], b["high"], b["low"], b["close"]
    pc = ref(c, 1)
    ar = _div(msum(_op(h, o, lambda x, y: x - y), n), msum(_op(o, l, lambda x, y: x - y), n))
    br = _div(msum([max(x - y, 0) if None not in (x, y) else None for x, y in zip(h, pc)], n),
              msum([max(y - x, 0) if None not in (x, y) else None for x, y in zip(l, pc)], n))
    return {"AR": _r([x * 100 if x is not None else None for x in ar]),
            "BR": _r([x * 100 if x is not None else None for x in br])}


def vr(b, n=26):
    c, v = b["close"], b["volume"]
    up, dn, eq = [None], [None], [None]
    for i in range(1, len(c)):
        if None in (c[i], c[i - 1], v[i]):
            up.append(None); dn.append(None); eq.append(None)  # noqa: E702
            continue
        up.append(v[i] if c[i] > c[i - 1] else 0)
        dn.append(v[i] if c[i] < c[i - 1] else 0)
        eq.append(v[i] if c[i] == c[i - 1] else 0)
    su, sd, se = msum(up, n), msum(dn, n), msum(eq, n)
    return {f"VR{n}": _r([(u + e / 2) / (d + e / 2) * 100 if None not in (u, d, e) and d + e / 2 else None
                          for u, d, e in zip(su, sd, se)])}


def mfi(b, n=14):
    tp = [(h + l + c) / 3 if None not in (h, l, c) else None for h, l, c in zip(b["high"], b["low"], b["close"])]
    pos, neg = [None], [None]
    for i in range(1, len(tp)):
        if None in (tp[i], tp[i - 1], b["volume"][i]):
            pos.append(None); neg.append(None)  # noqa: E702
            continue
        mf = tp[i] * b["volume"][i]
        pos.append(mf if tp[i] > tp[i - 1] else 0)
        neg.append(mf if tp[i] < tp[i - 1] else 0)
    sp, sn = msum(pos, n), msum(neg, n)
    return {f"MFI{n}": _r([(p / (p + q) * 100 if p + q else 50) if None not in (p, q) else None for p, q in zip(sp, sn)])}


def trix(b, n=12, m=9):
    e = ema(ema(ema(b["close"], n), n), n)
    t = _op(e, ref(e, 1), lambda x, y: (x - y) / y * 100 if y else None)
    return {"TRIX": _r(t, 3), f"TRIX MA{m}": _r(ma(t, m), 3)}


def dma(b, n1=10, n2=50, m=10):
    d = _op(ma(b["close"], n1), ma(b["close"], n2), lambda x, y: x - y)
    return {"DMA": _r(d), "AMA": _r(ma(d, m))}


def sar(b, step=0.02, cap=0.2):
    h, l = b["high"], b["low"]
    out = [None] * len(h)
    idx = [i for i in range(len(h)) if h[i] is not None and l[i] is not None]
    if len(idx) < 2:
        return {"SAR": out}
    i0 = idx[0]
    up, af, ep, s = True, step, h[i0], l[i0]
    for j, i in enumerate(idx[1:], 1):
        prev = idx[j - 1]
        s = s + af * (ep - s)
        if up:
            s = min(s, l[prev], l[idx[j - 2]] if j >= 2 else l[prev])
            if l[i] < s:
                up, s, ep, af = False, ep, l[i], step
            elif h[i] > ep:
                ep, af = h[i], min(af + step, cap)
        else:
            s = max(s, h[prev], h[idx[j - 2]] if j >= 2 else h[prev])
            if h[i] > s:
                up, s, ep, af = True, ep, h[i], step
            elif l[i] < ep:
                ep, af = l[i], min(af + step, cap)
        out[i] = round(s, 2)
    return {"SAR": out}


def vol_ma(b):
    v = b["volume"]
    return {"量": v, "均量5": _r(ma(v, 5), 0), "均量20": _r(ma(v, 20), 0)}


# ---------- 籌碼指標（用免費的法人與融資券資料） ----------


def _cum(xs):
    out, s = [], 0.0
    for x in xs:
        if x is not None:
            s += x
            out.append(s)
        else:
            out.append(None)
    return out


def inst_cum(b):
    return {"外資累計": _r(_cum(b["foreign"]), 0), "投信累計": _r(_cum(b["trust"]), 0),
            "自營累計": _r(_cum(b["dealer"]), 0)}


def short_ratio(b):
    return {"券資比%": _r(_div([s * 100 if s is not None else None for s in b["short"]], b["margin"]))}


# 名稱 -> (中文說明, 函式, 疊主圖, 預設參數說明)
INDICATORS = {
    "MA": ("均線", mas, True, "5/10/20/60/120/240"),
    "BOLL": ("布林通道", boll, True, "20,2"),
    "SAR": ("拋物線 SAR", sar, True, "0.02,0.2"),
    "VOL": ("成交量均量", vol_ma, False, "5,20"),
    "KD": ("隨機指標 KD", kd, False, "9,3,3"),
    "MACD": ("MACD", macd, False, "12,26,9"),
    "RSI": ("相對強弱 RSI", rsi, False, "6,12"),
    "BIAS": ("乖離率", bias, False, "10,20"),
    "WR": ("威廉指標", wr, False, "14,28"),
    "DMI": ("趨向指標 DMI", dmi, False, "14"),
    "ATR": ("真實波幅 ATR", atr, False, "14"),
    "OBV": ("能量潮 OBV", obv, False, ""),
    "CCI": ("順勢指標 CCI", cci, False, "14"),
    "PSY": ("心理線 PSY", psy, False, "12"),
    "MTM": ("動量 MTM", mtm, False, "10,10"),
    "ROC": ("變動率 ROC", roc, False, "12"),
    "ARBR": ("人氣意願 AR/BR", arbr, False, "26"),
    "VR": ("成交量比率 VR", vr, False, "26"),
    "MFI": ("資金流量 MFI", mfi, False, "14"),
    "TRIX": ("三重指數 TRIX", trix, False, "12,9"),
    "DMA": ("平均差 DMA", dma, False, "10,50,10"),
    "INST": ("法人累計買賣超", inst_cum, False, "張"),
    "SHORT": ("券資比", short_ratio, False, "%"),
}

# 繪圖時以柱狀圖顯示的線
HISTOGRAM_LINES = {"OSC", "量"}


def to_series(rows):
    """K 線列（dict）轉成指標用的欄位序列。量、法人以張為單位。"""
    def col(k, scale=1):
        return [(r.get(k) / scale if r.get(k) is not None else None) for r in rows]
    return {
        "date": [r["date"] for r in rows],
        "open": col("open"), "high": col("high"), "low": col("low"), "close": col("close"),
        "volume": col("volume", 1000),
        "foreign": col("foreign_net", 1000), "trust": col("trust_net", 1000),
        "dealer": col("dealer_net", 1000), "inst": col("total_net", 1000),
        "margin": col("margin_balance"), "short": col("short_balance"),
    }


def compute(rows, names):
    b = to_series(rows)
    out = {}
    for name in names:
        spec = INDICATORS.get(name)
        if spec:
            out[name] = {"title": spec[0], "overlay": spec[2], "lines": spec[1](b)}
    return out
