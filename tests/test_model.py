"""Offline tests of the rules and the replay.   python -m unittest discover -s tests"""
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backtest  # noqa: E402
from model import HIGH_DAYS, Position, breakout, buy_signal, decide, sell_reason  # noqa: E402

LONG, CASH = Position("long", 5.0, "2021-01-01"), Position()


def rows(closes, ma20=None, zone="normal"):
    start = date(2021, 1, 1)
    return [{"date": (start + timedelta(days=i)).isoformat(), "open": c, "high": c, "low": c, "close": c, "ma20": ma20,
             "volume_zone": zone} for i, c in enumerate(closes)]


class Rules(unittest.TestCase):
    def test_a_breakout_needs_thirty_consecutive_days(self):
        short = rows([1.0] * (HIGH_DAYS - 2) + [5.0])
        self.assertFalse(breakout(short, len(short) - 1))
        r = rows([5.0] * (HIGH_DAYS - 1) + [6.0])
        self.assertTrue(breakout(r, len(r) - 1))
        del r[10]                                   # a missing day: unknown
        r = rows([5.0]) + r
        self.assertFalse(breakout(r, len(r) - 1))

    def test_the_highest_close_of_the_window_only(self):
        r = rows([10.0] + [5.0] * (HIGH_DAYS - 1) + [6.0])
        self.assertTrue(breakout(r, len(r) - 1))    # the 10.0 is 31 rows back: outside the window
        r = rows([5.0] * 5 + [10.0] + [5.0] * (HIGH_DAYS - 2) + [9.0])
        self.assertFalse(breakout(r, len(r) - 1))   # an older close inside the window is higher
        r = rows([5.0] * HIGH_DAYS)
        self.assertTrue(breakout(r, len(r) - 1))    # a tie with the high counts

    def test_sell_below_the_average_or_on_low_volume(self):
        self.assertEqual(sell_reason(rows([9.0], ma20=10.0), 0), "sell (below 20-day SMA)")
        self.assertIsNone(sell_reason(rows([10.0], ma20=10.0), 0))          # at the average is not below it
        self.assertIsNone(sell_reason(rows([9.0], ma20=None), 0))           # unknown average: no change
        for zone in ("low", "extremely_low"):
            self.assertEqual(sell_reason(rows([11.0], ma20=10.0, zone=zone), 0), "sell (low volume)")
        self.assertIsNone(sell_reason(rows([11.0], ma20=10.0, zone=None), 0))   # unknown zone: no change

    def test_no_buy_on_a_low_volume_day(self):
        up = rows([5.0] * (HIGH_DAYS - 1) + [6.0], ma20=5.0)
        self.assertTrue(buy_signal(up, len(up) - 1))
        up[-1]["volume_zone"] = "low"
        self.assertFalse(buy_signal(up, len(up) - 1))
        up[-1]["volume_zone"] = None
        self.assertTrue(buy_signal(up, len(up) - 1))

    def test_decide_moves_between_long_and_cash_only(self):
        up = rows([5.0] * (HIGH_DAYS - 1) + [6.0], ma20=5.0)
        self.assertEqual(decide(up, len(up) - 1, CASH), (["buy"], "long"))
        self.assertEqual(decide(up, len(up) - 1, LONG), ([], "long"))
        down = rows([5.0] * HIGH_DAYS + [4.0], ma20=5.0)
        self.assertEqual(decide(down, len(down) - 1, LONG), (["sell (below 20-day SMA)"], "cash"))
        self.assertEqual(decide(down, len(down) - 1, CASH), ([], "cash"))


class Replay(unittest.TestCase):
    def test_orders_fill_at_the_next_open(self):
        r = rows([5.0] * (HIGH_DAYS - 1) + [6.0, 6.0, 4.0, 4.0], ma20=5.0)
        r[HIGH_DAYS]["open"] = 7.0        # the buy decided at the 6.0 close fills here
        r[HIGH_DAYS + 2]["open"] = 3.5    # the sell decided at the 4.0 close fills here
        self.assertEqual(backtest.simulate(r), [{
            "side": "long", "entry_date": r[HIGH_DAYS]["date"], "entry_price": 7.0,
            "exit_date": r[HIGH_DAYS + 2]["date"], "exit_price": 3.5, "reason": "sell (below 20-day SMA)"}])

    def test_a_trade_still_open_at_the_end_has_no_exit(self):
        r = rows([5.0] * (HIGH_DAYS - 1) + [6.0, 6.0], ma20=5.0)
        (t,) = backtest.simulate(r)
        self.assertEqual((t["exit_date"], t["reason"]), (None, "open"))


if __name__ == "__main__":
    unittest.main()
