"""自設公式（對應新世紀的「自設公式」與「自設指標選股」）。

語法類似一般看盤軟體的公式：
    快線:MA(C,5); 慢線:MA(C,20); CROSS(快線,慢線)
- 以分號分隔多條敘述，「名稱:運算式」會畫成一條線，之後的敘述可以引用該名稱。
- 選股時以「最後一條敘述在最後一根 K 棒的值」判斷，非 0 即入選。
- 不使用 eval，只認得下面列出的變數與函式。
"""
import math
import re

from . import indicators as ind

VARS = {
    "O": "open", "OPEN": "open", "H": "high", "HIGH": "high", "L": "low", "LOW": "low",
    "C": "close", "CLOSE": "close", "V": "volume", "VOL": "volume",
    "FI": "foreign", "TI": "trust", "DI": "dealer", "II": "inst",  # 法人買賣超（張）
    "MB": "margin", "SB": "short",                                 # 融資、融券餘額（張）
}

HELP = [
    ("變數", "O 開、H 高、L 低、C 收、V 量（張）、FI 外資、TI 投信、DI 自營、II 法人合計（張）、MB 融資、SB 融券餘額"),
    ("運算", "+ - * /、> < >= <= = <>、AND OR NOT"),
    ("函式", "MA EMA SMA(X,N,M) REF HHV LLV SUM STD ABS MAX MIN IF(條件,A,B) CROSS(A,B) COUNT(條件,N) EVERY EXIST"),
    ("指標", "RSI(N) K(N) D(N) DIF() DEA() OSC() BIAS(N) WR(N) CCI(N) ATR(N) OBV()"),
    ("範例", "MA(C,5)>MA(C,20) AND V>2*MA(V,20)；TI>0 AND REF(TI,1)>0 AND REF(TI,2)>0"),
]


class FormulaError(ValueError):
    pass


TOKEN = re.compile(r"\s*(?:(\d+\.?\d*|\.\d+)|([A-Za-z_一-鿿][A-Za-z0-9_一-鿿]*)|(>=|<=|<>|!=|==|&&|\|\||[-+*/()<>=,:;!]))")


def tokenize(src):
    pos, out = 0, []
    src = src.replace("；", ";").replace("：", ":").replace("，", ",").replace("（", "(").replace("）", ")")
    while pos < len(src):
        if src[pos:].strip() == "":
            break
        m = TOKEN.match(src, pos)
        if not m:
            raise FormulaError(f"看不懂的字元：{src[pos:pos + 10]!r}")
        pos = m.end()
        if m.group(1):
            out.append(("num", float(m.group(1))))
        elif m.group(2):
            out.append(("id", m.group(2).upper() if m.group(2).isascii() else m.group(2)))
        else:
            op = {"&&": "AND", "||": "OR", "!": "NOT", "==": "=", "!=": "<>"}.get(m.group(3), m.group(3))
            out.append(("op" if op not in ("AND", "OR", "NOT") else "id", op))
    return out


class Parser:
    """遞迴下降：or > and > not > 比較 > 加減 > 乘除 > 單元 > 原子。"""

    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self, k=0):
        return self.t[self.i + k] if self.i + k < len(self.t) else (None, None)

    def take(self, val=None):
        tok = self.peek()
        if val is not None and tok[1] != val:
            raise FormulaError(f"預期「{val}」，看到「{tok[1] or '結尾'}」")
        self.i += 1
        return tok

    def program(self):
        stmts = []
        while self.peek()[0]:
            if self.peek()[1] == ";":
                self.take()
                continue
            name = None
            if self.peek()[0] == "id" and self.peek(1)[1] == ":":
                name = self.take()[1]
                self.take(":")
            stmts.append((name, self.expr()))
            if self.peek()[0] and self.peek()[1] != ";":
                raise FormulaError(f"多出來的「{self.peek()[1]}」，敘述之間請用分號分隔")
        if not stmts:
            raise FormulaError("公式是空的")
        return stmts

    def expr(self):
        node = self.and_()
        while self.peek()[1] == "OR":
            self.take()
            node = ("bin", "OR", node, self.and_())
        return node

    def and_(self):
        node = self.not_()
        while self.peek()[1] == "AND":
            self.take()
            node = ("bin", "AND", node, self.not_())
        return node

    def not_(self):
        if self.peek()[1] == "NOT":
            self.take()
            return ("not", self.not_())
        return self.cmp()

    def cmp(self):
        node = self.add()
        while self.peek()[1] in (">", "<", ">=", "<=", "=", "<>"):
            op = self.take()[1]
            node = ("bin", op, node, self.add())
        return node

    def add(self):
        node = self.mul()
        while self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            node = ("bin", op, node, self.mul())
        return node

    def mul(self):
        node = self.unary()
        while self.peek()[1] in ("*", "/"):
            op = self.take()[1]
            node = ("bin", op, node, self.unary())
        return node

    def unary(self):
        if self.peek()[1] == "-":
            self.take()
            return ("neg", self.unary())
        if self.peek()[1] == "+":
            self.take()
        return self.atom()

    def atom(self):
        kind, val = self.take()
        if kind == "num":
            return ("num", val)
        if val == "(":
            node = self.expr()
            self.take(")")
            return node
        if kind == "id":
            if self.peek()[1] == "(":
                self.take("(")
                args = []
                if self.peek()[1] != ")":
                    args.append(self.expr())
                    while self.peek()[1] == ",":
                        self.take()
                        args.append(self.expr())
                self.take(")")
                return ("call", val, args)
            return ("var", val)
        raise FormulaError(f"這裡不該出現「{val or '結尾'}」")


def parse(src):
    if len(src) > 2000:
        raise FormulaError("公式太長")
    return Parser(tokenize(src)).program()


# ---------- 求值（向量化：所有值都是與 K 棒等長的 list） ----------


def _const(x, n):
    return [x] * n


def _map2(a, b, fn):
    return [fn(x, y) if x is not None and y is not None else None for x, y in zip(a, b)]


def _bool(x):
    return 1.0 if x else 0.0


BIN = {
    "+": lambda x, y: x + y, "-": lambda x, y: x - y, "*": lambda x, y: x * y,
    "/": lambda x, y: x / y if y else None,
    ">": lambda x, y: _bool(x > y), "<": lambda x, y: _bool(x < y),
    ">=": lambda x, y: _bool(x >= y), "<=": lambda x, y: _bool(x <= y),
    "=": lambda x, y: _bool(abs(x - y) < 1e-9), "<>": lambda x, y: _bool(abs(x - y) >= 1e-9),
    "AND": lambda x, y: _bool(x and y), "OR": lambda x, y: _bool(x or y),
}


def _cross(a, b):
    out = [None] * len(a)
    for i in range(1, len(a)):
        if None not in (a[i], b[i], a[i - 1], b[i - 1]):
            out[i] = _bool(a[i - 1] <= b[i - 1] and a[i] > b[i])
    return out


def _count(cond, n):
    return ind._window([None if x is None else _bool(x) for x in cond], n, sum)


def _int_arg(args, k, default, name):
    if len(args) <= k:
        if default is None:
            raise FormulaError(f"{name} 少了參數")
        return default
    v = args[k]
    if v is None or any(x != v[0] for x in v if x is not None) or v[0] is None:
        raise FormulaError(f"{name} 的週期參數必須是常數")
    n = int(v[0])
    if not 1 <= n <= 500:
        raise FormulaError(f"{name} 的週期要在 1~500 之間")
    return n


class Evaluator:
    def __init__(self, series):
        self.s = series
        self.n = len(series["close"])
        self.names = {}

    def ev(self, node):
        kind = node[0]
        if kind == "num":
            return _const(node[1], self.n)
        if kind == "neg":
            return [-x if x is not None else None for x in self.ev(node[1])]
        if kind == "not":
            return [None if x is None else _bool(not x) for x in self.ev(node[1])]
        if kind == "bin":
            return _map2(self.ev(node[2]), self.ev(node[3]), BIN[node[1]])
        if kind == "var":
            v = node[1]
            if v in self.names:
                return self.names[v]
            if v in VARS:
                return self.s[VARS[v]]
            raise FormulaError(f"不認得的變數「{v}」")
        if kind == "call":
            return self.call(node[1], node[2])
        raise FormulaError("公式錯誤")

    def call(self, fn, raw):
        args = [self.ev(a) for a in raw]
        a0 = args[0] if args else None
        n = lambda k=1, d=None: _int_arg(args, k, d, fn)  # noqa: E731
        s = self.s
        if fn in ("MA", "EMA", "REF", "HHV", "LLV", "SUM", "STD", "COUNT", "EVERY", "EXIST") and not args:
            raise FormulaError(f"{fn} 少了參數")
        if fn == "MA":
            return ind.ma(a0, n())
        if fn == "EMA":
            return ind.ema(a0, n())
        if fn == "SMA":
            return ind.sma(a0, n(), n(2, 1))
        if fn == "REF":
            return ind.ref(a0, n(1, 1))
        if fn == "HHV":
            return ind.hhv(a0, n())
        if fn == "LLV":
            return ind.llv(a0, n())
        if fn == "SUM":
            return ind.msum(a0, n())
        if fn == "STD":
            return ind.std(a0, n())
        if fn == "ABS":
            return [abs(x) if x is not None else None for x in a0]
        if fn in ("MAX", "MIN"):
            if len(args) != 2:
                raise FormulaError(f"{fn} 需要兩個參數")
            return _map2(args[0], args[1], max if fn == "MAX" else min)
        if fn == "IF":
            if len(args) != 3:
                raise FormulaError("IF 需要三個參數")
            return [None if c is None else (x if c else y) for c, x, y in zip(*args)]
        if fn == "CROSS":
            if len(args) != 2:
                raise FormulaError("CROSS 需要兩個參數")
            return _cross(args[0], args[1])
        if fn == "COUNT":
            return _count(a0, n())
        if fn == "EVERY":
            k = n()
            return [None if x is None else _bool(x == k) for x in _count(a0, k)]
        if fn == "EXIST":
            return [None if x is None else _bool(x > 0) for x in _count(a0, n())]
        # 內建指標：參數為週期
        if fn == "RSI":
            return ind.rsi_line(s["close"], n(0, 6))
        if fn in ("K", "D"):
            return ind.kd(s, n(0, 9))[fn]
        if fn in ("DIF", "DEA", "OSC"):
            m = ind.macd(s)
            return m["MACD" if fn == "DEA" else fn]
        if fn == "BIAS":
            k = n(0, 20)
            return ind._op(s["close"], ind.ma(s["close"], k), lambda x, m: (x - m) / m * 100)
        if fn == "WR":
            k = n(0, 14)
            return ind.wr(s, k, k)[f"WR{k}"]
        if fn == "CCI":
            k = n(0, 14)
            return ind.cci(s, k)[f"CCI{k}"]
        if fn == "ATR":
            k = n(0, 14)
            return ind.atr(s, k)[f"ATR{k}"]
        if fn == "OBV":
            return ind.obv(s)["OBV"]
        raise FormulaError(f"不認得的函式「{fn}」")


def evaluate(program, series):
    """回傳 [(名稱, 值序列)]，沒有命名的敘述名稱為 None。"""
    ev = Evaluator(series)
    out = []
    for name, node in program:
        vals = ev.ev(node)
        vals = [None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else x for x in vals]
        if name:
            ev.names[name.upper() if name.isascii() else name] = vals
        out.append((name, vals))
    return out


def run(src, rows):
    """給圖表用：回傳 {名稱: [值...]}。"""
    prog = parse(src)
    res = evaluate(prog, ind.to_series(rows))
    lines = {}
    for i, (name, vals) in enumerate(res):
        lines[name or f"公式{i + 1}"] = [round(x, 4) if x is not None else None for x in vals]
    return lines
