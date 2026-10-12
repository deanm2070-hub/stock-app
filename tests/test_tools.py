"""新世紀類功能的測試：指標、自設公式、週月 K、還原、模擬、排行、類股、選股、資料解析。不連網。"""
import os
import tempfile
import unittest
from datetime import date, timedelta

from stockapp import db, extra_sources as xs, formula, indicators as ind, tools


def _days(n, start=date(2026, 3, 2)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _bars(closes, days=None):
    days = days or _days(len(closes))
    rows, prev = [], closes[0]
    for ds, c in zip(days, closes):
        rows.append({"date": ds, "open": prev, "high": max(prev, c) + 1, "low": min(prev, c) - 1, "close": c,
                     "volume": 1_000_000, "value": c * 1_000_000, "change": c - prev,
                     "foreign_net": 1000, "trust_net": -1000, "dealer_net": 0, "total_net": 0,
                     "margin_balance": 100, "short_balance": 10})
        prev = c
    return rows


class IndicatorTest(unittest.TestCase):
    def test_ma_ema(self):
        self.assertEqual(ind.ma([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])
        self.assertEqual(ind.ma([1, None, 3, 4], 2), [None, None, None, 3.5])
        e = ind.ema([1, 1, 1, 1, 1], 3)
        self.assertEqual(e, [None, None, 1, 1, 1])

    def test_rsi_extremes(self):
        up = ind.rsi_line(list(range(1, 30)), 6)
        self.assertEqual(up[-1], 100)
        dn = ind.rsi_line(list(range(30, 1, -1)), 6)
        self.assertEqual(dn[-1], 0)

    def test_kd_range_and_trend(self):
        b = ind.to_series(_bars([50 + i for i in range(40)]))
        k = ind.kd(b)
        self.assertTrue(all(0 <= x <= 100 for x in k["K"] if x is not None))
        self.assertGreater(k["K"][-1], 80)  # 一路上漲，K 值在高檔

    def test_macd_flat(self):
        b = ind.to_series(_bars([100] * 60))
        m = ind.macd(b)
        self.assertEqual(m["DIF"][-1], 0)
        self.assertEqual(m["OSC"][-1], 0)

    def test_all_indicators_run(self):
        rows = _bars([100 + (i % 7) - (i % 3) for i in range(80)])
        out = ind.compute(rows, list(ind.INDICATORS))
        self.assertEqual(set(out), set(ind.INDICATORS))
        for name, spec in out.items():
            for line, vals in spec["lines"].items():
                self.assertEqual(len(vals), 80, f"{name}.{line}")

    def test_boll_order(self):
        b = ind.to_series(_bars([100 + (i % 5) for i in range(40)]))
        bb = ind.boll(b)
        self.assertGreater(bb["上軌"][-1], bb["中軌"][-1])
        self.assertGreater(bb["中軌"][-1], bb["下軌"][-1])


class FormulaTest(unittest.TestCase):
    def setUp(self):
        self.s = ind.to_series(_bars([10, 11, 12, 11, 13, 14]))

    def ev(self, src):
        return formula.evaluate(formula.parse(src), self.s)

    def test_arith_and_named(self):
        res = self.ev("A:C*2; B:A-1; B>20")
        self.assertEqual(res[0][1][-1], 28)
        self.assertEqual(res[1][1][-1], 27)
        self.assertEqual(res[2][1][-1], 1)

    def test_functions(self):
        self.assertEqual(self.ev("REF(C,1)")[0][1][-1], 13)
        self.assertEqual(self.ev("HHV(C,3)")[0][1][-1], 14)
        self.assertEqual(self.ev("MA(C,2)")[0][1][-1], 13.5)
        self.assertEqual(self.ev("COUNT(C>REF(C,1),5)")[0][1][-1], 4)
        self.assertEqual(self.ev("IF(C>13,1,-1)")[0][1][-2], -1)
        self.assertEqual(self.ev("V")[0][1][-1], 1000)  # 張
        self.assertEqual(self.ev("FI>0 AND TI<0")[0][1][-1], 1)

    def test_cross(self):
        res = self.ev("CROSS(C,12)")[0][1]
        self.assertEqual(res[2], 0)  # 12 不算穿越
        self.assertEqual(res[4], 1)  # 11 -> 13

    def test_fullwidth_and_lowercase(self):
        self.assertEqual(self.ev("快線：ma(c，2)；快線>13")[1][1][-1], 1)

    def test_errors(self):
        for bad in ["C+", "FOO(C)", "XYZ>1", "MA(C,C)", "C C", "", "MA(C,0)"]:
            with self.assertRaises(formula.FormulaError, msg=bad):
                self.ev(bad)

    def test_no_python_eval(self):
        with self.assertRaises(formula.FormulaError):
            self.ev("__import__('os')")


class KlineToolsTest(unittest.TestCase):
    def test_resample_week_month(self):
        rows = _bars([float(i) for i in range(10, 30)], _days(20, date(2026, 3, 2)))
        w = tools.resample(rows, "W")
        self.assertEqual(len(w), 4)
        self.assertEqual(w[0]["open"], 10)
        self.assertEqual(w[0]["close"], 14)
        self.assertEqual(w[0]["volume"], 5_000_000)
        self.assertEqual(w[1]["change"], 5)
        self.assertEqual(w[0]["date"], "2026-03-06")
        m = tools.resample(rows, "M")
        self.assertEqual([r["date"][:7] for r in m], ["2026-03"])

    def test_adjust(self):
        rows = _bars([100, 100, 95, 96], ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06"])
        adj = tools.adjust(rows, [("2026-07-03", 95 / 100)])
        self.assertEqual([r["close"] for r in adj], [95, 95, 95, 96])
        self.assertEqual(adj[2]["change"], 0)
        self.assertEqual(rows[0]["close"], 100)  # 不改原資料

    def test_simulate(self):
        rows = _bars([10, 11], ["2026-10-08", "2026-10-09"])
        out = tools.simulate(rows, [12, 13])
        self.assertEqual([r["date"] for r in out[2:]], ["2026-10-12", "2026-10-13"])  # 跳過週末
        self.assertTrue(out[-1]["sim"])
        self.assertEqual(out[-1]["change"], 1)
        self.assertEqual(tools._next_dates("2026-01-30", 2, "M"), ["2026-02-28", "2026-03-31"])


class DbToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn = db.connect(os.path.join(self.tmp, "t.db"))
        tools.ensure_schema(self.conn)
        days = _days(30)
        self.days = days
        prices, inst, mg = [], [], []
        for code, f in [("1111", 1), ("2222", -1), ("3333", 0)]:
            px = 100.0
            for i, ds in enumerate(days):
                prev = px
                px = px + f * 1
                vol = 5_000_000 if (code == "1111" and i == 29) else 1_000_000
                prices.append({"date": ds, "code": code, "open": prev, "high": max(px, prev) + 0.5, "low": min(px, prev) - 0.5,
                               "close": px, "volume": vol, "value": int(px * vol), "change": px - prev})
                inst.append({"date": ds, "code": code, "foreign_net": f * 100_000, "trust_net": f * 50_000,
                             "dealer_net": 0, "total_net": f * 150_000})
                mg.append({"date": ds, "code": code, "margin_balance": 1000 + f * i * 10, "short_balance": 50})
        db.upsert(self.conn, "daily_price", prices, [])
        db.upsert(self.conn, "institutional", inst, [])
        db.upsert(self.conn, "margin", mg, [])
        db.upsert_stocks(self.conn, [{"code": "1111", "name": "甲", "market": "tse"},
                                     {"code": "2222", "name": "乙", "market": "tse"},
                                     {"code": "3333", "name": "丙", "market": "otc"}])
        db.upsert(self.conn, "company", [{"code": "1111", "industry": "半導體業", "updated": ""},
                                         {"code": "2222", "industry": "半導體業", "updated": ""},
                                         {"code": "3333", "industry": "航運業", "updated": ""}], [])
        db.upsert(self.conn, "market_daily", [{"date": d, "taiex": 20000 + i} for i, d in enumerate(days)], [])
        db.upsert(self.conn, "exrights", [{"date": days[10], "code": "1111", "prev_close": 110, "ref_price": 105,
                                           "value": 5, "kind": "息"}], [])
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_chart(self):
        c = tools.chart(self.conn, "1111", period="D", names=["KD", "MACD", "MA"], expr="X:C-REF(C,1)")
        self.assertEqual(len(c["bars"]), 30)
        self.assertIn("K", c["indicators"]["KD"]["lines"])
        self.assertEqual(c["formula"]["lines"]["X"][-1], 1)
        bad = tools.chart(self.conn, "1111", expr="MA(")
        self.assertIn("error", bad["formula"])
        adj = tools.chart(self.conn, "1111", adjusted=True)
        self.assertLess(adj["bars"][0]["close"], c["bars"][0]["close"])
        self.assertEqual(adj["bars"][-1]["close"], c["bars"][-1]["close"])
        w = tools.chart(self.conn, "1111", period="W", sim=[200])
        self.assertEqual(len(w["bars"]), 7)
        self.assertTrue(w["bars"][-1]["sim"])

    def test_rank(self):
        r = tools.rank(self.conn, "gain")
        self.assertEqual([x["code"] for x in r["rows"]], ["1111"])
        r = tools.rank(self.conn, "loss")
        self.assertEqual([x["code"] for x in r["rows"]], ["2222"])
        r = tools.rank(self.conn, "vol_ratio")
        self.assertEqual(r["rows"][0]["code"], "1111")
        self.assertEqual(r["rows"][0]["vol_ratio"], 5)
        r = tools.rank(self.conn, "trust_buy")
        self.assertEqual(r["rows"][0]["trust"], 50)
        r = tools.rank(self.conn, "margin_dec")
        self.assertEqual([x["code"] for x in r["rows"]], ["2222"])
        r = tools.rank(self.conn, "value", market="otc")
        self.assertEqual([x["code"] for x in r["rows"]], ["3333"])

    def test_industries(self):
        r = tools.industries(self.conn)
        names = [x["industry"] for x in r["industries"]]
        self.assertEqual(set(names), {"半導體業", "航運業"})
        semi = next(x for x in r["industries"] if x["industry"] == "半導體業")
        self.assertEqual((semi["up"], semi["down"]), (1, 1))
        m = tools.industry_members(self.conn, "航運業")
        self.assertEqual([x["code"] for x in m["rows"]], ["3333"])

    def test_formula_screen(self):
        r = tools.formula_screen(self.conn, "C>MA(C,5) AND EVERY(TI>0,3)")
        self.assertEqual([x["code"] for x in r["rows"]], ["1111"])
        r = tools.formula_screen(self.conn, "上漲:C>REF(C,1); 上漲", market="otc")
        self.assertEqual(r["rows"], [])
        for _, expr in tools.PRESETS:
            tools.formula_screen(self.conn, expr)  # 預設條件都要能跑
        with self.assertRaises(formula.FormulaError):
            tools.formula_screen(self.conn, "NOPE(C)")

    def test_compare(self):
        r = tools.compare(self.conn, ["TAIEX", "2222"], limit=10)
        self.assertEqual(len(r["TAIEX"]["points"]), 10)
        self.assertEqual(r["2222"]["name"], "乙")

    def test_financials(self):
        db.upsert(self.conn, "financials", [
            {"code": "1111", "year": 2026, "quarter": 1, "revenue": 100, "gross_profit": 40, "op_income": 20,
             "net_income": 10, "eps": 1.0, "assets": 1000, "liabilities": 400, "equity": 600, "bvps": 60},
            {"code": "1111", "year": 2026, "quarter": 2, "revenue": 250, "gross_profit": 100, "op_income": 50,
             "net_income": 30, "eps": 3.0, "assets": 1100, "liabilities": 440, "equity": 660, "bvps": 66}], [])
        f = tools.financials(self.conn, "1111")
        self.assertEqual(f[0]["revenue_q"], 150)
        self.assertEqual(f[0]["eps_q"], 2.0)
        self.assertEqual(f[0]["gross_margin"], 40.0)
        self.assertEqual(f[0]["debt_ratio"], 40.0)

    def test_profile_calendar(self):
        p = tools.profile(self.conn, "1111")
        self.assertEqual(p["industry"], "半導體業")
        self.assertEqual([x["code"] for x in p["peers"]], ["2222"])
        future = (date.today() + timedelta(days=5)).isoformat()
        db.upsert(self.conn, "exright_plan", [{"date": future, "code": "1111", "name": "甲", "kind": "息",
                                               "stock_ratio": None, "cash_div": 3.5, "market": "tse"}], [])
        c = tools.calendar(self.conn)
        self.assertEqual(c["upcoming"][0]["cash_div"], 3.5)
        self.assertEqual(c["history"][0]["code"], "1111")


class ParseExtraTest(unittest.TestCase):
    def test_exrights(self):
        js = {"stat": "OK", "fields": ["資料日期", "股票代號", "股票名稱", "除權息前收盤價", "除權息參考價", "權值+息值",
                                        "權/息", "漲停價格", "跌停價格", "開盤競價基準", "減除股利參考價", "詳細資料"],
              "data": [["115年07月16日", "2330", "台積電", "1,100.00", "1,095.00", "5.00", "息", "1", "1", "1", "1", ""],
                       ["115年07月16日", "00878", "國泰永續高股息", "20", "19.6", "0.4", "息", "", "", "", "", ""]]}
        r = xs.parse_twse_exrights(js)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["date"], "2026-07-16")
        self.assertEqual((r[0]["prev_close"], r[0]["ref_price"]), (1100, 1095))

    def test_plan(self):
        js = {"stat": "OK", "fields": ["除權除息日期", "股票代號", "名稱", "除權息", "無償配股率", "現金增資配股率",
                                        "現金增資認購價", "現金股利"],
              "data": [["115年10月15日", "2317", "鴻海", "息", "0", "0", "0", "5.5"]]}
        r = xs.parse_twse_exright_plan(js)[0]
        self.assertEqual((r["date"], r["code"], r["cash_div"]), ("2026-10-15", "2317", 5.5))

    def test_company(self):
        rows = [{"公司代號": "2330", "公司簡稱": "台積電", "產業別": "24", "_market": "tse"},
                {"公司代號": "6488", "公司簡稱": "環球晶", "產業別": "24", "_market": "otc"},
                {"公司代號": "0050", "公司簡稱": "x", "產業別": "", "_market": "tse"}]
        r = xs.parse_company_info(rows)
        self.assertEqual([(x["code"], x["industry"]) for x in r], [("2330", "半導體業"), ("6488", "半導體業")])

    def test_financials(self):
        inc = [{"出表日期": "1151012", "年度": "115", "季別": "2", "公司代號": "2330", "公司名稱": "台積電",
                "營業收入": "1,000", "營業毛利（毛損）": "600", "營業毛利（毛損）淨額": "600", "營業利益（損失）": "450",
                "稅前淨利（淨損）": "460", "本期淨利（淨損）": "400", "淨利（淨損）歸屬於母公司業主": "399",
                "基本每股盈餘（元）": "15.5"},
               {"年度": "115", "季別": "2", "公司代號": "2881", "收益合計": "300", "本期稅後淨利（淨損）": "90",
                "基本每股盈餘（元）": "4.2"}]
        bal = [{"年度": "115", "季別": "2", "公司代號": "2330", "資產總額": "5,000", "負債總額": "1,500",
                "權益總額": "3,500", "每股參考淨值": "135"}]
        r = {x["code"]: x for x in xs.parse_financials(inc, bal)}
        self.assertEqual(r["2330"]["year"], 2026)
        self.assertEqual((r["2330"]["revenue"], r["2330"]["eps"], r["2330"]["net_income"]), (1000, 15.5, 400))
        self.assertEqual(r["2330"]["equity"], 3500)
        self.assertEqual((r["2881"]["revenue"], r["2881"]["net_income"]), (300, 90))


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        os.environ["STOCKAPP_DB"] = os.path.join(cls.tmp, "api.db")
        os.environ["STOCKAPP_NO_SCHEDULER"] = "1"
        import importlib
        from stockapp import db as dbm
        importlib.reload(dbm)
        from stockapp import app as appm
        cls.appm = importlib.reload(appm)
        from fastapi.testclient import TestClient
        cls.client = TestClient(cls.appm.app)

    def test_endpoints(self):
        c = self.client
        self.assertGreater(len(c.get("/api/indicators").json()["indicators"]), 15)
        self.assertEqual(c.get("/api/chart/2330?ind=KD,MACD&period=W").json()["bars"], [])
        self.assertEqual(c.get("/api/rank?kind=foreign_buy").status_code, 200)
        self.assertEqual(c.get("/api/industries").status_code, 200)
        self.assertEqual(c.get("/api/formula/screen?expr=MA(").status_code, 400)
        self.assertEqual(c.get("/api/chart/2330?sim=abc").status_code, 400)
        self.assertEqual(c.get("/api/calendar").status_code, 200)
        self.assertEqual(c.get("/api/financials/2330").json(), [])
        self.assertEqual(c.get("/api/profile/2330").json()["industry"], None)


if __name__ == "__main__":
    unittest.main()
