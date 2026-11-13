"""Tests for the round-3 P0/P1 fixes (synthetic data only, no sample needed).

P1 (classify_cross_item): a sequential-path buyer counts as
bought_intended_item iff some item has min(intent time) < max(buy time)
for that (user, item) — at least one cart/fav for the item strictly
precedes at least one buy of the same item. Same-second pairs do NOT
count as "before".
P0 (compute_nonpath): the non-path share denominator must be ALL buyers,
not PV buyers only (the historical bug: 1,510 / 6,811 instead of
1,510 / 6,846 on real data).
"""
import importlib.util
import sys
import unittest
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_module(rel_path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


gen = load_module("scripts/gen_dashboard_csvs.py", "gen_csvs_mod")


def events(rows):
    """Build an events frame from (user, item, behavior, timestamp) rows."""
    df = pd.DataFrame(rows, columns=["user_id", "item_id",
                                     "behavior_type", "datetime"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df


def classify(intent_rows, buy_rows, seq_buyers):
    intent = events(intent_rows)
    buy = events(buy_rows)
    cross, diag = gen.classify_cross_item(intent, buy, set(seq_buyers))
    by_group = dict(zip(cross["group_name"], cross["users"]))
    return by_group, diag


class TestCrossItemTimeLogic(unittest.TestCase):
    def test_intent_before_buy_same_item(self):
        """先意向、后购买同一商品 -> 算购买过先前意向商品。"""
        groups, _ = classify(
            [("U1", "A", "cart", "2017-12-01 10:05")],
            [("U1", "A", "buy", "2017-12-01 10:10")],
            ["U1"])
        self.assertEqual(groups["bought_intended_item"], 1)
        self.assertEqual(groups["bought_only_unintended"], 0)

    def test_buy_before_fav_same_item(self):
        """先购买、后收藏同一商品 -> 不算（意向发生在购买之后）。"""
        groups, _ = classify(
            [("U2", "A", "fav", "2017-12-01 10:10")],
            [("U2", "A", "buy", "2017-12-01 10:05")],
            ["U2"])
        self.assertEqual(groups["bought_intended_item"], 0)
        self.assertEqual(groups["bought_only_unintended"], 1)

    def test_user_buys_multiple_items(self):
        """用户购买多个不同商品：其中一件有先前意向即算意向购买。"""
        groups, _ = classify(
            [("U3", "A", "cart", "2017-12-01 10:05")],
            [("U3", "A", "buy", "2017-12-01 10:10"),
             ("U3", "B", "buy", "2017-12-01 10:15")],
            ["U3"])
        self.assertEqual(groups["bought_intended_item"], 1)
        self.assertEqual(groups["bought_only_unintended"], 0)

    def test_same_item_multiple_intents_and_buys_intent_between(self):
        """同一商品多次意向与多次购买：意向夹在两次购买之间 -> 算。"""
        groups, _ = classify(
            [("U4a", "A", "cart", "2017-12-01 10:10")],
            [("U4a", "A", "buy", "2017-12-01 10:05"),
             ("U4a", "A", "buy", "2017-12-01 10:15")],
            ["U4a"])
        self.assertEqual(groups["bought_intended_item"], 1)

    def test_same_item_multiple_intents_and_buys_intent_after_all(self):
        """同一商品多次意向与多次购买：全部意向都在最后一次购买之后 -> 不算。"""
        groups, _ = classify(
            [("U4b", "A", "cart", "2017-12-01 10:10"),
             ("U4b", "A", "cart", "2017-12-01 10:11")],
            [("U4b", "A", "buy", "2017-12-01 10:05"),
             ("U4b", "A", "buy", "2017-12-01 10:06")],
            ["U4b"])
        self.assertEqual(groups["bought_intended_item"], 0)
        self.assertEqual(groups["bought_only_unintended"], 1)

    def test_same_second_timestamps(self):
        """同秒时间戳无法确定先后 -> 不计入意向在前。"""
        groups, _ = classify(
            [("U5", "A", "cart", "2017-12-01 10:10")],
            [("U5", "A", "buy", "2017-12-01 10:10")],
            ["U5"])
        self.assertEqual(groups["bought_intended_item"], 0)
        self.assertEqual(groups["bought_only_unintended"], 1)

    def test_buy_with_no_intent(self):
        """无意向行为直接购买 -> 只买过未意向商品。"""
        groups, _ = classify(
            [],
            [("U6", "A", "buy", "2017-12-01 10:10")],
            ["U6"])
        self.assertEqual(groups["bought_intended_item"], 0)
        self.assertEqual(groups["bought_only_unintended"], 1)

    def test_pre_pv_intent_counts(self):
        """意向在首次浏览之前、购买在后 -> 算购买过先前意向商品。

        覆盖 Codex 审查发现的事件范围问题：商品级核对使用窗口内全部事件，
        不得沿用用户级路径的“意向须在首次浏览之后”过滤（真实样本中因此
        漏算 32 人，如先加购、后浏览、再购买的用户）。
        """
        df = events([
            ("U9", "A", "cart", "2017-11-25 16:37"),
            ("U9", "A", "pv", "2017-11-25 17:41"),
            ("U9", "B", "cart", "2017-11-25 21:48"),
            ("U9", "A", "buy", "2017-12-01 11:50"),
        ])
        _, _, sub, sub2, seq_buyers = gen.compute_nonpath(df)
        self.assertIn("U9", seq_buyers)
        intent_all = df[df.behavior_type.isin(["cart", "fav"])]
        buy_all = df[df.behavior_type == "buy"]
        groups, _ = gen.classify_cross_item(intent_all, buy_all, seq_buyers)
        by_group = dict(zip(groups["group_name"], groups["users"]))
        self.assertEqual(by_group["bought_intended_item"], 1)
        # 旧的路径过滤集会丢掉 16:37 那条意向 -> 误判为只买过未意向商品。
        groups_f, _ = gen.classify_cross_item(sub, sub2, seq_buyers)
        by_group_f = dict(zip(groups_f["group_name"], groups_f["users"]))
        self.assertEqual(by_group_f["bought_only_unintended"], 1)

    def test_non_seq_buyer_not_counted(self):
        """不在完整路径购买用户集合中的用户，即使有意向在前的购买也不计入。"""
        groups, _ = classify(
            [("U8", "A", "cart", "2017-12-01 10:05")],
            [("U8", "A", "buy", "2017-12-01 10:10")],
            ["U9"])
        self.assertEqual(groups["bought_intended_item"], 0)
        self.assertEqual(groups["bought_only_unintended"], 1)

    def test_same_second_diagnostic_counts_exact_matches(self):
        """同秒诊断按精确时间戳配对计数，而非仅比较极值。

        意向 10:00 与 10:10、购买 10:10：存在一对同秒记录（10:10），
        但 min(意向)=10:00 < max(购买)=10:10，仍算意向在前。
        """
        groups, diag = classify(
            [("U10", "A", "cart", "2017-12-01 10:00"),
             ("U10", "A", "cart", "2017-12-01 10:10")],
            [("U10", "A", "buy", "2017-12-01 10:10")],
            ["U10"])
        self.assertEqual(groups["bought_intended_item"], 1)
        self.assertEqual(diag["same_second_pairs"], 1)
        self.assertEqual(diag["users_flipping_under_loose"], 0)

    def test_classification_conservation(self):
        """分类人数加总一致性：互斥、无重复、总人数守恒。

        U1 意向在前、U2 购买在前、U3 多商品、U4a 意向夹在两次购买之间、
        U4b 全部意向在购买之后、U5 同秒、U6 无意向、U7 无任何事件。
        旧集合交集规则会把 U2/U4b/U5 也算进意向组（共 6 人），新规则只算 3 人。
        """
        intent_rows = [
            ("U1", "A", "cart", "2017-12-01 10:05"),
            ("U2", "A", "fav", "2017-12-01 10:10"),
            ("U3", "A", "cart", "2017-12-01 10:05"),
            ("U4a", "A", "cart", "2017-12-01 10:10"),
            ("U4b", "A", "cart", "2017-12-01 10:10"),
            ("U4b", "A", "cart", "2017-12-01 10:11"),
            ("U5", "A", "cart", "2017-12-01 10:10"),
        ]
        buy_rows = [
            ("U1", "A", "buy", "2017-12-01 10:10"),
            ("U2", "A", "buy", "2017-12-01 10:05"),
            ("U3", "A", "buy", "2017-12-01 10:10"),
            ("U3", "B", "buy", "2017-12-01 10:15"),
            ("U4a", "A", "buy", "2017-12-01 10:05"),
            ("U4a", "A", "buy", "2017-12-01 10:15"),
            ("U4b", "A", "buy", "2017-12-01 10:05"),
            ("U4b", "A", "buy", "2017-12-01 10:06"),
            ("U5", "A", "buy", "2017-12-01 10:10"),
            ("U6", "A", "buy", "2017-12-01 10:10"),
        ]
        seq = ["U1", "U2", "U3", "U4a", "U4b", "U5", "U6", "U7"]
        groups, diag = classify(intent_rows, buy_rows, seq)
        self.assertEqual(groups["bought_intended_item"], 3)
        self.assertEqual(groups["bought_only_unintended"], 5)
        self.assertEqual(groups["bought_intended_item"]
                         + groups["bought_only_unintended"], len(seq))
        self.assertEqual(diag["overlap_all_events"], 6)
        self.assertEqual(diag["same_second_pairs"], 1)
        self.assertEqual(diag["users_flipping_under_loose"], 1)


class TestNonpathShareDenominator(unittest.TestCase):
    def setUp(self):
        # U1: PV 买家，无意向 -> 非路径（无意向）
        # U2: PV -> 意向 -> 购买 -> 完整路径
        # U3: 无 PV，有意向、有购买 -> 非路径（顺序不满足）
        # U4: PV 买家，无意向 -> 非路径（无意向）
        self.df = events([
            ("U1", "A", "pv", "2017-12-01 10:00"),
            ("U1", "B", "buy", "2017-12-01 10:10"),
            ("U2", "A", "pv", "2017-12-01 10:00"),
            ("U2", "B", "cart", "2017-12-01 10:05"),
            ("U2", "C", "buy", "2017-12-01 10:10"),
            ("U3", "A", "cart", "2017-12-01 09:00"),
            ("U3", "A", "buy", "2017-12-01 09:30"),
            ("U4", "A", "pv", "2017-12-01 10:00"),
            ("U4", "B", "buy", "2017-12-01 10:05"),
        ])

    def test_share_uses_all_buyers(self):
        """回归：分子（全部购买用户）与分母（全部购买用户）同一总体。

        本合成数据上旧的错误算法给出 3/3 = 100%，正确值是 3/4 = 75%。
        """
        _, totals, _, _, _ = gen.compute_nonpath(self.df)
        self.assertEqual(totals["total_buy_users"], 4)
        self.assertEqual(totals["pv_buy_users"], 3)
        self.assertEqual(totals["nonpath_total"], 3)
        self.assertEqual(totals["nonpath_pv_users"], 2)
        self.assertEqual(totals["nonpath_no_pv_users"], 1)
        self.assertAlmostEqual(totals["nonpath_share_all"], 0.75)
        self.assertAlmostEqual(totals["nonpath_share_pv"], 2 / 3, places=5)
        buggy = totals["nonpath_total"] / totals["pv_buy_users"]
        self.assertNotAlmostEqual(totals["nonpath_share_all"], buggy)

    def test_nonpath_population_conservation(self):
        """总体守恒：全部购买用户 = 完整路径购买用户 + 非路径购买用户；
        有 PV 购买用户 = 完整路径购买用户 + 非路径有 PV 购买用户；
        非路径有 PV + 非路径无 PV = 非路径总数。"""
        split, totals, _, _, seq_buyers = gen.compute_nonpath(self.df)
        self.assertEqual(totals["total_buy_users"],
                         len(seq_buyers) + totals["nonpath_total"])
        self.assertEqual(totals["pv_buy_users"],
                         len(seq_buyers) + totals["nonpath_pv_users"])
        self.assertEqual(totals["nonpath_pv_users"]
                         + totals["nonpath_no_pv_users"],
                         totals["nonpath_total"])
        self.assertEqual(int(split["users"].sum()), totals["nonpath_total"])
        by_group = dict(zip(split["group_name"], split["users"]))
        self.assertEqual(by_group["no_intent_buyers"], 2)
        self.assertEqual(by_group["wrong_order_buyers"], 1)


class TestCommittedCsvConsistency(unittest.TestCase):
    def test_dashboard_csvs_agree(self):
        """已提交小表内部一致：分母总体、份额重算、跨商品加总均对得上。"""
        out = REPO_ROOT / "outputs"
        split = pd.read_csv(out / "funnel_nonpath_split.csv")
        totals = pd.read_csv(out / "funnel_nonpath_totals.csv").iloc[0]
        cross = pd.read_csv(out / "funnel_cross_item.csv")
        sequential = pd.read_csv(out / "funnel_sequential_summary.csv")
        funnel = pd.read_csv(out / "funnel_summary.csv")
        self.assertEqual(int(split["users"].sum()), int(totals["nonpath_total"]))
        self.assertAlmostEqual(
            totals["nonpath_share_all"],
            totals["nonpath_total"] / totals["total_buy_users"], places=5)
        self.assertAlmostEqual(
            totals["nonpath_share_pv"],
            totals["nonpath_pv_users"] / totals["pv_buy_users"], places=5)
        self.assertEqual(int(totals["pv_buy_users"]),
                         int(funnel.loc[funnel["stage"] == "buy", "users"].iloc[0]))
        seq_buyers = int(sequential.loc[
            sequential["step"] == "intent_to_buy", "users"].iloc[0])
        # 全部购买用户 = 完整路径购买用户 + 非路径购买用户；
        # 有 PV 购买用户 = 完整路径购买用户 + 非路径有 PV 购买用户。
        self.assertEqual(int(totals["total_buy_users"]),
                         seq_buyers + int(totals["nonpath_total"]))
        self.assertEqual(int(totals["pv_buy_users"]),
                         seq_buyers + int(totals["nonpath_pv_users"]))
        self.assertEqual(int(totals["nonpath_pv_users"])
                         + int(totals["nonpath_no_pv_users"]),
                         int(totals["nonpath_total"]))
        self.assertEqual(int(cross["users"].sum()), seq_buyers)


if __name__ == "__main__":
    unittest.main()
