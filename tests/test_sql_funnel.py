"""SQL-level tests for the sequential funnel (Q3/Q9/Q9b).

Runs the ACTUAL statements from sql/queries.sql against a tiny synthetic
SQLite table, so the ">=" temporal rule, reverse-order exclusion and the
user-level cross-item scope are verified on the real SQL, not a
reimplementation.

Synthetic users (all on 2017-12-01):
  U1: pv 10:00 (item A) -> cart 10:05 (item B) -> buy 10:10 (item C)
      [in path; cross-item by design]
  U2: pv 10:00, cart 10:00, buy 10:00 (same second)
      [in path via ">="; real data has 0 such ties at anchors]
  U3: buy 09:00 -> pv 10:00 -> cart 11:00
      [intent after pv, but buy before first intent -> not in path]
  U4: fav 09:00 -> pv 10:00 -> buy 11:00
      [intent before first pv -> not in path]
  U5: pv 10:00 -> buy 10:30 (no intent at all)
      [no-intent buyer -> non-path]
  U6: cart 09:00 -> buy 09:30 (no pv at all)
      [no-pv buyer -> non-path]

Expected Q3: pv=5, pv_to_intent=3, intent_to_buy=2.
Expected Q9: strict_seq (3, 2, 0.6667); any_intent (5, 5, 1.0).
Expected Q9b: no_intent_buyers=1 (U5), wrong_order_buyers=3 (U3, U4, U6).
"""
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path

import importlib.util
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "run_sqlite_mod", REPO_ROOT / "sql" / "run_sqlite.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_sqlite_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


RUNNER = _load_runner()
QUERIES = dict(RUNNER.split_queries(
    (REPO_ROOT / "sql" / "queries.sql").read_text(encoding="utf-8")))

ROWS = [
    # (user_id, item_id, behavior_type, datetime)
    (1, 101, "pv", "2017-12-01 10:00:00"),
    (1, 202, "cart", "2017-12-01 10:05:00"),
    (1, 303, "buy", "2017-12-01 10:10:00"),
    (2, 101, "pv", "2017-12-01 10:00:00"),
    (2, 202, "cart", "2017-12-01 10:00:00"),
    (2, 303, "buy", "2017-12-01 10:00:00"),
    (3, 101, "buy", "2017-12-01 09:00:00"),
    (3, 101, "pv", "2017-12-01 10:00:00"),
    (3, 202, "cart", "2017-12-01 11:00:00"),
    (4, 101, "fav", "2017-12-01 09:00:00"),
    (4, 101, "pv", "2017-12-01 10:00:00"),
    (4, 303, "buy", "2017-12-01 11:00:00"),
    (5, 101, "pv", "2017-12-01 10:00:00"),
    (5, 303, "buy", "2017-12-01 10:30:00"),
    (6, 202, "cart", "2017-12-01 09:00:00"),
    (6, 303, "buy", "2017-12-01 09:30:00"),
]


def build_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE sample (user_id INTEGER, item_id INTEGER, "
        "category_id INTEGER, behavior_type TEXT, timestamp INTEGER, "
        "datetime TEXT, date TEXT, hour INTEGER)"
    )
    rows = []
    for user_id, item_id, behavior, dt in ROWS:
        ts = int(datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
                 .replace(tzinfo=timezone.utc).timestamp())
        rows.append((user_id, item_id, 1000 + item_id, behavior, ts,
                     dt, dt[:10], int(dt[11:13])))
    conn.executemany("INSERT INTO sample VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return conn


class TestSqlSequentialFunnel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = build_db()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_q3_same_second_reverse_order_and_cross_item(self):
        rows = self.conn.execute(QUERIES["Q3：时序漏斗 pv → 意向 → 购买（口径 B）"]).fetchall()
        got = {r[0]: r[1] for r in rows}
        # U1 (cross-item) + U2 (same-second) in path; U3/U4 reverse-order out.
        self.assertEqual(got["pv"], 5)
        self.assertEqual(got["pv_to_intent"], 3)
        self.assertEqual(got["intent_to_buy"], 2)

    def test_q9_strict_vs_loose_caliber(self):
        rows = self.conn.execute(
            QUERIES["Q9：意向到购买——两种口径对照"]).fetchall()
        got = {r[0]: (r[1], r[2], r[3]) for r in rows}
        self.assertEqual(got["strict_seq"], (3, 2, 0.6667))
        self.assertEqual(got["any_intent"], (5, 5, 1.0))

    def test_q9b_non_path_split(self):
        rows = self.conn.execute(
            QUERIES["Q9b：未满足完整时序路径的购买用户分流（1,510 人）"]).fetchall()
        got = {r[0]: r[1] for r in rows}
        # U5 has no intent record; U3/U4/U6 have intent but wrong order.
        self.assertEqual(got["no_intent_buyers"], 1)
        self.assertEqual(got["wrong_order_buyers"], 3)
        self.assertEqual(sum(got.values()), 4)  # 6 buyers - 2 in path


if __name__ == "__main__":
    unittest.main(verbosity=2)
