"""盤中提示測試（用人工建構的 MIS 快照，不連網）。"""
import os
import tempfile
import unittest
from unittest import mock

from stockapp import alerts, sources


def snap(v, z, t, bid="999.00_998.00_", ask="1000.00_1001.00_", tv=None, code="2330", day="20261012"):
    """組一筆 MIS 回傳格式的快照。"""
    m = {"c": code, "n": "台積電", "ex": "tse", "z": z, "y": "990.00", "o": "995.00", "h": "1005.00", "l": "990.00",
         "v": str(v), "tv": str(tv if tv is not None else 1), "b": bid, "g": "10_20_", "a": ask, "f": "5_6_",
         "u": "1085.00", "w": "891.00", "d": day, "t": t}
    return sources.parse_realtime({"msgArray": [m]})


class DetectorTest(unittest.TestCase):
    def setUp(self):
        self.d = alerts.Detector({"min_lots": 50, "min_amount": 10_000_000, "price_enabled": False})
        self.d.feed(snap(1000, "999.00", "10:00:00"))  # 基準

    def test_first_snapshot_is_baseline(self):
        self.assertEqual(alerts.Detector().feed(snap(5000, "1000.00", "10:00:00")), [])

    def test_buy_at_ask(self):
        a = self.d.feed(snap(1080, "1000.00", "10:00:05", ask="1001.00_1002.00_", tv=80))
        self.assertEqual(len(a), 1)
        self.assertEqual((a[0]["kind"], a[0]["side"], a[0]["lots"], a[0]["amount"]), ("trade", "buy", 80, 80_000_000))
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
        d = alerts.Detector({"price_enabled": False})
        d.feed(snap(0, "-", "08:59:55"))
        a = d.feed(snap(3000, "995.00", "09:00:00"))
        self.assertEqual((a[0]["side"], a[0]["note"]), ("neutral", "開盤"))
        a = d.feed(snap(6000, "1000.00", "13:30:00"))
        self.assertEqual(a[0]["note"], "收盤")

    def test_new_day_resets(self):
        self.assertEqual(self.d.feed(snap(200, "1085.00", "09:01:00", day="20261013")), [])

    def test_disabled(self):
        self.d.settings["enabled"] = False
        self.assertEqual(self.d.feed(snap(5000, "1000.00", "10:00:05")), [])


class PriceMoveTest(unittest.TestCase):
    def setUp(self):
        self.d = alerts.Detector({"trade_enabled": False, "price_window": 2, "price_pct": 2.0})
        self.d.feed(snap(1000, "1000.00", "10:00:00"))

    def kinds(self, v, z, t, **kw):
        return [(a["kind"], a["side"]) for a in self.d.feed(snap(v, z, t, **kw))]

    def test_spike_within_window(self):
        self.assertEqual(self.kinds(1001, "1010.00", "10:00:30"), [])
        a = self.d.feed(snap(1002, "1020.00", "10:01:00"))
        self.assertEqual((a[0]["kind"], a[0]["side"], a[0]["pct"]), ("spike", "buy", 2.0))
        # 提示後重新起算，不會每 5 秒連發
        self.assertEqual(self.kinds(1003, "1025.00", "10:01:05"), [])

    def test_plunge_from_high(self):
        self.kinds(1001, "1010.00", "10:00:30")
        self.assertEqual(self.kinds(1002, "989.00", "10:01:30"), [("plunge", "sell")])  # 從 1010 跌 2.08%

    def test_slow_move_outside_window(self):
        self.assertEqual(self.kinds(1001, "1010.00", "10:01:30"), [])
        self.assertEqual(self.kinds(1002, "1019.00", "10:03:00"), [])  # 10:00 的 1000 已經超過 2 分鐘
        self.assertEqual(self.kinds(1003, "1020.00", "10:03:10"), [])  # 從 1010 只漲 0.99%

    def test_limit_up_once(self):
        self.assertEqual(self.kinds(1001, "1085.00", "10:00:05"), [("limit", "buy")])  # 漲停優先於急拉
        self.assertEqual(self.kinds(1002, "1085.00", "10:00:10"), [])
        self.kinds(1003, "1080.00", "10:00:15")
        self.assertEqual(self.kinds(1004, "1085.00", "10:00:20"), [("limit", "buy")])  # 打開又鎖回去

    def test_limit_down(self):
        self.assertEqual(self.kinds(1001, "891.00", "10:00:05"), [("limit", "sell")])

    def test_no_price_alert_before_open(self):
        d = alerts.Detector()
        d.feed(snap(0, "-", "08:59:00", bid="1085.00_"))
        self.assertEqual(d.feed(snap(0, "-", "08:59:05", bid="1085.00_")), [])  # 試撮掛在漲停不算


class ClassifyTest(unittest.TestCase):
    prev = {"price": 100, "bids": [{"price": 99.9, "vol": 1}], "asks": [{"price": 100, "vol": 1}]}

    def test_rules(self):
        self.assertEqual(alerts.classify(100, self.prev), "buy")
        self.assertEqual(alerts.classify(99.9, self.prev), "sell")
        mid = {"price": 100, "bids": [{"price": 99, "vol": 1}], "asks": [{"price": 101, "vol": 1}]}
        self.assertEqual(alerts.classify(100.5, mid), "buy")
        self.assertEqual(alerts.classify(99.5, mid), "sell")
        self.assertEqual(alerts.classify(100, mid), "neutral")

    def test_limit_up_no_ask(self):
        prev = {"price": 110, "bids": [{"price": 110, "vol": 500}], "asks": []}
        self.assertEqual(alerts.classify(110, prev, {"limit_up": 110}), "sell")  # 價格 ≤ 買價優先
        prev = {"price": 110, "bids": [], "asks": []}
        self.assertEqual(alerts.classify(110, prev, {"limit_up": 110}), "buy")


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
        rows = appmod.get_alerts()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["kind"], rows[0]["side"], rows[0]["lots"], rows[0]["single"]), ("trade", "buy", 200, True))
        self.assertEqual(appmod.get_alerts(since=rows[0]["id"]), [])


if __name__ == "__main__":
    unittest.main()
