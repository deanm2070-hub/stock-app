"""解析與選股邏輯的單元測試（使用人工建構的樣本，不連網）。執行：python -m unittest discover tests"""
import os
import random
import tempfile
import unittest
from datetime import date, timedelta

from stockapp import analysis, db, sources

D = date(2026, 10, 8)


class ParseTest(unittest.TestCase):
    def test_num(self):
        self.assertEqual(sources.num("1,234.5"), 1234.5)
        self.assertIsNone(sources.num("--"))
        self.assertEqual(sources.num("<p style= color:red>+</p>"), None)

    def test_twse_daily(self):
        js = {"stat": "OK", "tables": [
            {"title": "115年10月08日 價格指數(臺灣證券交易所)",
             "fields": ["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"],
             "data": [["發行量加權股價指數", "22,500.12", "<p style ='color:green'>-</p>", "120.50", "0.53", ""]]},
            {"title": "漲跌證券數合計", "fields": ["類型", "整體市場", "股票"],
             "data": [["上漲(漲停)", "5,000(30)", "400(10)"], ["下跌(跌停)", "3,000(2)", "500(1)"]]},
            {"title": "115年10月08日每日收盤行情(全部(不含權證、牛熊證))",
             "fields": ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌(+/-)", "漲跌價差"],
             "data": [["2330", "台積電", "30,000,000", "50,000", "30,000,000,000", "1,000.00", "1,010.00", "995.00", "1,005.00", "<p style= color:red>+</p>", "5.00"],
                      ["2317", "鴻海", "20,000,000", "1", "1", "200.00", "201.00", "198.00", "199.00", "<p style= color:green>-</p>", "1.00"],
                      ["0050", "元大台灣50", "1", "1", "1", "1", "1", "1", "1", "", "0.00"]]}]}
        prices, stocks, market = sources.parse_twse_daily(js, D)
        self.assertEqual([p["code"] for p in prices], ["2330", "2317"])
        self.assertEqual(prices[0]["close"], 1005.0)
        self.assertEqual(prices[1]["change"], -1.0)
        self.assertEqual(market["taiex_change"], -120.5)
        self.assertEqual(market["up_count"], 400)
        self.assertEqual(market["down_count"], 500)

    def test_closed_day(self):
        self.assertEqual(sources.parse_twse_daily({"stat": "很抱歉，沒有符合條件的資料!"}, D), ([], [], None))

    def test_t86(self):
        js = {"stat": "OK", "fields": ["證券代號", "證券名稱", "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)",
                                        "外陸資買賣超股數(不含外資自營商)", "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數",
                                        "投信買進股數", "投信賣出股數", "投信買賣超股數", "自營商買賣超股數", "三大法人買賣超股數"],
              "data": [["2330", "台積電", "1", "1", "1,000,000", "0", "0", "5,000", "0", "0", "-20,000", "3,000", "988,000"]]}
        r = sources.parse_twse_institutional(js, D)[0]
        self.assertEqual((r["foreign_net"], r["trust_net"], r["dealer_net"], r["total_net"]), (1005000, -20000, 3000, 988000))

    def test_margin(self):
        js = {"stat": "OK", "tables": [{"fields": ["項目"], "data": []},
              {"fields": ["代號", "名稱", "買進", "賣出", "現金償還", "前日餘額", "今日餘額", "次一營業日限額",
                          "買進", "賣出", "現券償還", "前日餘額", "今日餘額", "次一營業日限額", "資券互抵", "註記"],
               "data": [["2330", "台積電", "1", "1", "0", "20,000", "20,100", "0", "1", "1", "0", "300", "310", "0", "0", ""]]}]}
        r = sources.parse_twse_margin(js, D)[0]
        self.assertEqual((r["margin_balance"], r["short_balance"]), (20100, 310))

    def test_realtime(self):
        js = {"msgArray": [{"c": "2330", "n": "台積電", "ex": "tse", "z": "-", "y": "1000.00", "o": "1001.00",
                            "h": "1010.00", "l": "999.00", "v": "12345", "b": "1005.00_1004.00_", "g": "10_20_",
                            "a": "1006.00_", "f": "5_", "d": "20261008", "t": "10:00:00"}]}
        q = sources.parse_realtime(js)[0]
        self.assertEqual(q["price"], 1005.0)  # 無成交價時用最佳買價
        self.assertEqual(q["change"], 5.0)
        self.assertEqual(len(q["bids"]), 2)

    def test_tpex_daily(self):
        rows = [{"Date": "1151008", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶", "Close": "500.00",
                 "Change": "+5.00", "Open": "495", "High": "502", "Low": "494", "TradingShares": "1,000,000", "TransactionAmount": "5"}]
        prices, stocks = sources.parse_tpex_daily(rows)
        self.assertEqual(prices[0]["date"], "2026-10-08")
        self.assertEqual(stocks[0]["market"], "otc")


    def test_tdcc(self):
        text = "\ufeff資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n" + "\n".join(
            f"20261009,2330,{lvl},{100 if lvl < 17 else 9999},{1000},{pct}" for lvl, pct in
            [(1, 2.0), (8, 3.0), (11, 5.0), (12, 1.5), (14, 2.5), (15, 70.25), (16, 0), (17, 100)]) + \
            "\n20261009,0050,15,1,1,90\n"
        r = sources.parse_tdcc(text)
        self.assertEqual(len(r), 1)
        r = r[0]
        self.assertEqual(r["date"], "2026-10-09")
        self.assertEqual(r["big400_pct"], 74.25)
        self.assertEqual(r["big1000_pct"], 70.25)
        self.assertEqual(r["retail_pct"], 5.0)
        self.assertEqual(r["holders"], 9999)

    def test_cnyes(self):
        js = {"items": {"data": [
            {"newsId": 1, "title": "台積電營收創新高", "publishAt": 1791676800, "summary": "",
             "market": [{"code": "2330", "name": "台積電"}], "stock": ["2330"]},
            {"newsId": 2, "title": "", "publishAt": 1}]}}
        r = sources.parse_cnyes(js)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["codes"], ["2330"])
        self.assertTrue(r[0]["published"].endswith("+08:00"))

    def test_rss(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>x</title>
          <item><title>鴻海接單強勁 法人看好</title><link>https://tw.stock.yahoo.com/news/a.html</link>
          <pubDate>Thu, 08 Oct 2026 10:00:00 GMT</pubDate><description>&lt;p&gt;內文&lt;/p&gt;</description></item>
          <item><title></title><link>https://x</link></item></channel></rss>"""
        r = sources.parse_rss(xml)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["published"], "2026-10-08T18:00:00+08:00")
        self.assertEqual(r[0]["summary"], "內文")
        self.assertEqual(sources.parse_rss("not xml"), [])

    def test_sentiment(self):
        self.assertGreater(sources.news_sentiment("鴻海接單強勁 法人看好"), 0)
        self.assertLess(sources.news_sentiment("某公司營收衰退 下修財測"), 0)
        self.assertGreater(sources.news_sentiment("第三季虧損縮小"), 0)  # 片語優先
        self.assertEqual(sources.news_sentiment("董事會通過議案"), 0)


class ScreenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn = db.connect(os.path.join(self.tmp, "t.db"))
        random.seed(1)
        d, days = date(2026, 6, 1), []
        while len(days) < 70:
            if d.weekday() < 5:
                days.append(d.isoformat())
            d += timedelta(days=1)
        self.days = days
        rows, inst, mg = [], [], []
        # 1111：穩定上漲 + 投信連買 → 應入選；2222：下跌 → 不應入選
        for code, drift in [("1111", 0.6), ("2222", -0.5)]:
            px = 50.0
            for i, ds in enumerate(days):
                prev = px
                px = max(10.5, px + drift + random.uniform(-0.3, 0.3))
                vol = 3_000_000 if i < 69 else 6_000_000
                rows.append({"date": ds, "code": code, "open": prev, "high": px + 0.2, "low": prev - 0.2,
                             "close": round(px, 2), "volume": vol, "value": 0, "change": round(px - prev, 2)})
                inst.append({"date": ds, "code": code, "foreign_net": 200_000 if drift > 0 else -200_000,
                             "trust_net": 100_000 if drift > 0 else -50_000, "dealer_net": 0,
                             "total_net": 300_000 if drift > 0 else -250_000})
                mg.append({"date": ds, "code": code, "margin_balance": 5000 - i * 10, "short_balance": 100})
        db.upsert(self.conn, "daily_price", rows, [])
        db.upsert(self.conn, "institutional", inst, [])
        db.upsert(self.conn, "margin", mg, [])
        db.upsert_stocks(self.conn, [{"code": "1111", "name": "測試甲", "market": "tse"},
                                     {"code": "2222", "name": "測試乙", "market": "tse"}])
        db.upsert(self.conn, "fundamentals", [{"code": "1111", "pe": 15, "pb": 2, "dividend_yield": 4.5,
                                               "rev_month": "11509", "rev_yoy": 25, "rev_mom": 3, "updated": ""}], [])
        db.upsert(self.conn, "market_daily", [{"date": days[-1], "taiex": 22000, "taiex_change": 150, "taiex_pct": 0.7,
                                              "up_count": 600, "down_count": 300, "foreign_amt": 8e9, "trust_amt": 1e9,
                                              "dealer_amt": 0, "total_value": 4e11}], [])
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_screen(self):
        r = analysis.screen(self.conn, min_score=3)
        codes = [p["code"] for p in r["picks"]]
        self.assertEqual(codes, ["1111"])
        p = r["picks"][0]
        self.assertTrue(p["quality"])
        self.assertGreaterEqual(p["trust_streak"], 3)
        self.assertTrue(any("投信連買" in x["text"] for x in p["reasons"]))

    def test_market_summary(self):
        m = analysis.market_summary(self.conn)
        self.assertEqual(m["tone"], "偏多")
        self.assertEqual(m["breadth_ma20"], 50.0)

    def _add_extra(self):
        last = self.days[-1]
        # 1111 大戶連續增加、新聞偏多；2222 大戶減少、新聞偏空
        hold = []
        weeks = [(date.fromisoformat(last) - timedelta(days=7 * k)).isoformat() for k in range(4, -1, -1)]
        for w, d in enumerate(weeks):
            hold.append({"date": d, "code": "1111", "holders": 1000, "big400_pct": 50 + w * 0.6,
                         "big1000_pct": 40 + w * 0.5, "retail_pct": 20 - w * 0.4})
            hold.append({"date": d, "code": "2222", "holders": 1000, "big400_pct": 50 - w * 0.6,
                         "big1000_pct": 40 - w * 0.5, "retail_pct": 20 + w * 0.4})
        db.upsert(self.conn, "holders", hold, [])
        news = [
            {"url": "u1", "code": "1111", "published": last + "T15:00:00+08:00", "title": "測試甲接單強勁 營收創新高",
             "source": "t", "summary": "", "sentiment": 1.0, "fetched": ""},
            {"url": "u2", "code": "1111", "published": last + "T18:00:00+08:00", "title": "測試甲擴產 法人看好",
             "source": "t", "summary": "", "sentiment": 1.0, "fetched": ""},
            {"url": "u3", "code": "2222", "published": last + "T12:00:00+08:00", "title": "測試乙虧損 下修財測",
             "source": "t", "summary": "", "sentiment": -1.0, "fetched": ""},
            {"url": "u4", "code": "2222", "published": "2026-01-01T00:00:00+08:00", "title": "很舊的新聞",
             "source": "t", "summary": "", "sentiment": 1.0, "fetched": ""},
        ]
        db.upsert(self.conn, "news", news, [])
        self.conn.commit()

    def test_report(self):
        self._add_extra()
        r = analysis.build_report(self.conn, watchlist=["2222", "1111"])
        self.assertEqual([x["code"] for x in r["buy"]], ["1111"])
        self.assertEqual([x["code"] for x in r["sell"]], ["2222"])
        self.assertEqual([x["code"] for x in r["watch"]], ["2222", "1111"])
        b, s_ = r["buy"][0], r["sell"][0]
        self.assertEqual(b["verdict"], "偏多")
        self.assertEqual(s_["verdict"], "偏空")
        self.assertGreater(b["dims"]["holders"], 0)
        self.assertEqual(b["dims"]["news"], 2)
        self.assertLess(s_["dims"]["holders"], 0)
        self.assertLess(s_["dims"]["news"], 0)
        self.assertLess(s_["dims"]["trend"], 0)
        self.assertLess(s_["dims"]["chips"], 0)
        self.assertEqual(s_["news_count"], 1)  # 舊新聞不列入
        self.assertTrue(any(x["dim"] == "holders" and "週增" in x["text"] for x in b["reasons"]))
        self.assertEqual(r["coverage"]["holders_date"], self.days[-1])

    def test_report_saved_and_performance(self):
        self._add_extra()
        # 用倒數第二天產生報告，再看最後一天的表現
        rep = analysis.build_report(self.conn, until=self.days[-2])
        analysis.save_report(self.conn, rep)
        self.assertEqual(analysis.load_report(self.conn)["date"], self.days[-2])
        perf = analysis.performance(self.conn)
        self.assertEqual(perf[0]["next"], self.days[-1])
        self.assertEqual(perf[0]["buy"]["n"], len(rep["buy"]))

    def test_analyze_stock(self):
        self._add_extra()
        a = analysis.analyze_stock(self.conn, "2222")
        self.assertEqual(a["verdict"], "偏空")
        self.assertEqual(len(a["holders_history"]), 5)
        self.assertEqual(len(a["news"]), 2)
        self.assertIn("note", analysis.analyze_stock(self.conn, "9999"))

    def test_collect_news(self):
        from unittest import mock
        from stockapp import collector
        cn = {"items": {"data": [{"newsId": 9, "title": "測試甲(1111)接單強勁", "publishAt": 1791676800, "market": []}]}}
        rss = "<rss><channel><item><title>測試乙下修財測</title><link>https://y/1</link></item></channel></rss>"
        with mock.patch.object(sources, "fetch_cnyes", return_value=cn), \
             mock.patch.object(sources, "fetch_yahoo_rss", return_value=rss):
            collector.collect_news(self.conn, ["2222"])
        rows = {(r["code"], r["title"]): r["sentiment"] for r in self.conn.execute("SELECT * FROM news")}
        self.assertGreater(rows[("1111", "測試甲(1111)接單強勁")], 0)
        self.assertLess(rows[("2222", "測試乙下修財測")], 0)

    def test_kline(self):
        k = analysis.kline(self.conn, "1111")
        self.assertEqual(len(k), 70)
        self.assertIsNotNone(k[-1]["trust_net"])


if __name__ == "__main__":
    unittest.main()
