"""大單偵測測試（用人工建構的 MIS 快照，不連網）。"""
import os
import tempfile
import unittest
from unittest import mock

from stockapp import bigtrade, sources


def snap(v, z, t, bid="999.00_998.00_", ask="1000.00_1001.00_", tv=None, code="2330"):
    """組一筆 MIS 回傳格式的快照。"""
    m = {"c": code, "n": "台積電", "ex": "tse", "z": z, "y": "990.00", "o": "995.00", "h": "1005.00", "l": "990.00",
         "v": str(v), "tv": str(tv if tv is not None else 1), "b": bid, "g": "10_20_", "a": ask, "f": "5_6_",
         "u": "1085.00", "w": "891.00", "d": "20261012", "t": t}
    return sources.parse_realtime({"msgArray": [m]})


class DetectorTest(unittest.TestCase):
    def setUp(self):
        self.d = bigtrade.Detector({"enabled": True, "min_lots": 50, "min_amount": 10_000_000})
        self.d.feed(snap(1000, "999.00", "10:00:00"))  # 基準

    def test_first_snapshot_is_baseline(self):
        self.assertEqual(bigtrade.Detector().feed(snap(5000, "1000.00", "10:00:00")), [])

    def test_buy_at_ask(self):
        a = self.d.feed(snap(1080, "1000.00", "10:00:05", ask="1001.00_1002.00_", tv=80))
        self.assertEqual(len(a), 1)
        self.assertEqual((a[0]["side"], a[0]["lots"], a[0]["amount"]), ("buy", 80, 80_000_000))
        self.assertTrue(a[0]["single"])
        self.assertEqual((a[0]["date"], a[0]["time"]), ("2026-10-12", "10:00:05"))

    def test_sell_at_bid_merged(self):
        a = self.d.feed(snap(1100, "999.00", "10:00:05", tv=3))
        self.assertEqual(a[0]["side"], "sell")
        self.assertFalse(a[0]["single"])  # 量差 100 張不等於最後一筆 3 張，是累計

    def test_below_threshold(self):
        self.assertEqual(self.d.feed(snap(1040, "1000.00", "10:00:05")), [])  # 40 張 < 50
        self.d.settings["min_lots"] = 30
        self.assertEqual(len(self.d.feed(snap(1080, "1000.00", "10:00:10"))), 1)

    def test_amount_threshold(self):
        self.d.settings.update(min_lots=0, min_amount=100_000_000)
        self.assertEqual(self.d.feed(snap(1080, "1000.00", "10:00:05")), [])  # 8000 萬 < 1 億

    def test_same_snapshot_no_double_count(self):
        self.d.feed(snap(1080, "1000.00", "10:00:05"))
        self.assertEqual(self.d.feed(snap(1080, "1000.00", "10:00:05")), [])

    def test_open_and_close_auction(self):
        d = bigtrade.Detector()
        d.feed(snap(0, "-", "08:59:55"))
        a = d.feed(snap(3000, "995.00", "09:00:00"))
        self.assertEqual((a[0]["side"], a[0]["note"]), ("neutral", "開盤"))
        a = d.feed(snap(6000, "1000.00", "13:30:00"))
        self.assertEqual(a[0]["note"], "收盤")

    def test_new_day_resets(self):
        q = snap(200, "1000.00", "09:01:00")
        q[0]["time"] = "20261013 09:01:00"
        self.assertEqual(self.d.feed(q), [])

    def test_disabled(self):
        self.d.settings["enabled"] = False
        self.assertEqual(self.d.feed(snap(5000, "1000.00", "10:00:05")), [])


class ClassifyTest(unittest.TestCase):
    prev = {"price": 100, "bids": [{"price": 99.9, "vol": 1}], "asks": [{"price": 100, "vol": 1}]}

    def test_rules(self):
        self.assertEqual(bigtrade.classify(100, self.prev), "buy")
        self.assertEqual(bigtrade.classify(99.9, self.prev), "sell")
        mid = {"price": 100, "bids": [{"price": 99, "vol": 1}], "asks": [{"price": 101, "vol": 1}]}
        self.assertEqual(bigtrade.classify(100.5, mid), "buy")
        self.assertEqual(bigtrade.classify(99.5, mid), "sell")
        self.assertEqual(bigtrade.classify(100, mid), "neutral")

    def test_limit_up_no_ask(self):
        prev = {"price": 110, "bids": [{"price": 110, "vol": 500}], "asks": []}
        self.assertEqual(bigtrade.classify(110, prev, {"limit_up": 110}), "sell")  # 價格 ≤ 買價優先
        prev = {"price": 110, "bids": [], "asks": []}
        self.assertEqual(bigtrade.classify(110, prev, {"limit_up": 110}), "buy")


class ApiTest(unittest.TestCase):
    def test_quote_feeds_detector_and_records(self):
        tmp = tempfile.mkdtemp()
        os.environ["STOCKAPP_DB"] = os.path.join(tmp, "t.db")
        os.environ["STOCKAPP_NO_SCHEDULER"] = "1"
        import importlib
        from stockapp import db
        importlib.reload(db)
        from stockapp import app as appmod
        appmod = importlib.reload(appmod)
        seq = [snap(1000, "999.00", "10:00:00"), snap(1200, "1000.00", "10:00:05", tv=200)]
        with mock.patch.object(appmod.sources, "fetch_realtime", side_effect=lambda pairs: seq.pop(0)):
            appmod.quote("2330")
            appmod._quote_cache.clear()
            appmod.quote("2330")
        rows = appmod.big_trades()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["side"], rows[0]["lots"], rows[0]["single"]), ("buy", 200, True))
        self.assertEqual(appmod.big_trades(since=rows[0]["id"]), [])


if __name__ == "__main__":
    unittest.main()
