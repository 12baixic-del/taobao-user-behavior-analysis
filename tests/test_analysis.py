import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Matplotlib needs a writable config dir at import time; CI/review sandboxes
# may not provide one via HOME. Point it at a temp dir first.
os.environ.setdefault("MPLCONFIGDIR", tempfile.mkdtemp(prefix="mplconfig-"))

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_module(rel_path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


eda = load_module("notebooks/01_data_cleaning_eda.py", "eda_mod")
rfm_mod = load_module("notebooks/02_funnel_rfm_analysis.py", "rfm_mod")


class TestCleaningStreamingAndTimezone(unittest.TestCase):
    def test_utc_to_beijing_window_boundary(self):
        """验证 Unix 时间戳按北京时间 (UTC+8) 解析与上/下边界窗口过滤。"""
        df = pd.DataFrame({
            "user_id": [1, 2, 3, 4],
            "item_id": [10, 20, 30, 40],
            "category_id": [100, 200, 300, 400],
            "behavior_type": ["pv", "pv", "buy", "pv"],
            # 1511539199 = 2017-11-24 23:59:59 CST (剔除)
            # 1511539200 = 2017-11-25 00:00:00 CST (保留)
            # 1512316799 = 2017-12-03 23:59:59 CST (保留)
            # 1512316800 = 2017-12-04 00:00:00 CST (剔除)
            "timestamp": [1511539199, 1511539200, 1512316799, 1512316800],
        })
        cleaned = eda.clean_behavior_df(df, verbose=False)
        self.assertEqual(cleaned["user_id"].tolist(), [2, 3])

    def test_stream_clean_and_sample_cross_chunk_consistency(self):
        """目标一：验证分块流式读取跨 chunk 去重与按 user_id 完整保留用户序列。"""
        rows = []
        for uid in range(1, 13):  # 12 名用户，每人 4 条不同行为，分散写入 CSV
            for j, b in enumerate(["pv", "cart", "fav", "buy"]):
                rows.append((uid, 100 + j, 1, b, 1511568000 + j * 60))
        # 为全部用户追加跨 chunk 的完全重复行与超范围行，确保任意抽样集合均触发跨块去重
        for uid in range(1, 13):
            rows.append((uid, 100, 1, "pv", 1511568000))
        rows.append((99, 999, 1, "pv", 1483228800))

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "UserBehavior.csv"
            pd.DataFrame(rows).to_csv(csv_path, header=False, index=False)

            sampled = eda.stream_clean_and_sample(
                csv_path, target_rows=16, seed=42, chunksize=5, verbose=False
            )
            self.assertFalse(sampled.empty)
            counts_per_user = sampled.groupby("user_id").size()
            # 每位入选用户均必须完整保留 4 条去重后行为
            self.assertTrue((counts_per_user == 4).all())

    def test_stream_clean_empty_after_filter(self):
        """边界测试：全部记录无效时分块流式返回空表且不报错。"""
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "UserBehavior.csv"
            pd.DataFrame([(1, 10, 1, "invalid", 1511568000)]).to_csv(csv_path, header=False, index=False)
            res = eda.stream_clean_and_sample(csv_path, target_rows=10, chunksize=2, verbose=False)
            self.assertTrue(res.empty)

    def test_stream_adaptive_threshold_preserves_full_user_sequences(self):
        """第五阶段回归测试：触发 high_water 阈值收紧时，仍完整保留入选 user_id 的全部行为记录。"""
        rows = []
        # 80 名用户各 5 条行为记录（共 400 行），刻意交错分布在不同 chunk 中
        for step in range(5):
            for uid in range(1, 81):
                rows.append((uid, 100 + step, 10, "pv", 1511568000 + step * 3600 + uid))

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "UserBehavior.csv"
            pd.DataFrame(rows).to_csv(csv_path, header=False, index=False)
            sampled = eda.stream_clean_and_sample(
                csv_path, target_rows=60, seed=42, chunksize=50, verbose=False
            )
            self.assertFalse(sampled.empty)
            self.assertLess(len(sampled), 400)
            # 入选用户每人均必须完整保留全部 5 条行为，绝不允许半截序列
            counts_per_user = sampled.groupby("user_id").size()
            self.assertTrue(
                (counts_per_user == 5).all(),
                f"发现未完整保留全部记录的用户: {counts_per_user[counts_per_user != 5].to_dict()}",
            )

    def test_stream_dirty_rows_and_skewed_single_user_safe(self):
        """第五阶段边界测试：脏行(缺失/非数字)容错 + 单一大户极端倾斜不删空不崩溃。"""
        rows = [
            ("", 101, 10, "pv", 1511568000),        # 缺失 user_id
            ("bad", 102, 10, "pv", 1511568000),     # 非法 user_id
            (999, 103, 10, "pv", "not_a_ts"),       # 非法 timestamp
        ]
        # 单一大户 uid=1 占 120 条记录（超过 high_water=75），另加 uid=2 占 5 条
        for i in range(120):
            rows.append((1, 1000 + i, 10, "pv", 1511568000 + i))
        for i in range(5):
            rows.append((2, 2000 + i, 10, "buy", 1511568000 + i))

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "UserBehavior.csv"
            pd.DataFrame(rows).to_csv(csv_path, header=False, index=False)
            sampled = eda.stream_clean_and_sample(
                csv_path, target_rows=20, seed=42, chunksize=30, verbose=False
            )
            self.assertFalse(sampled.empty)
            # 确认至少保留 1 名用户且其完整序列未损坏
            for uid, grp in sampled.groupby("user_id"):
                expected_cnt = 120 if uid == 1 else 5
                self.assertEqual(len(grp), expected_cnt)

    def test_stream_invalid_params_and_zero_target(self):
        """第五阶段边界测试：target_rows<=0 返回空表，chunksize<=0 显式报错。"""
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "UserBehavior.csv"
            pd.DataFrame([(1, 10, 1, "pv", 1511568000)]).to_csv(csv_path, header=False, index=False)
            empty_res = eda.stream_clean_and_sample(csv_path, target_rows=0, verbose=False)
            self.assertTrue(empty_res.empty)
            with self.assertRaises(ValueError):
                eda.stream_clean_and_sample(csv_path, target_rows=10, chunksize=0, verbose=False)

    def test_daily_summary_zero_pv_safe(self):
        """边界测试：无 pv 仅有 buy 时不产生 inf。"""
        no_pv = pd.DataFrame({
            "date": [pd.Timestamp("2017-12-03").date()],
            "user_id": [1001],
            "behavior_type": ["buy"],
        })
        daily = eda.compute_daily_summary(no_pv)
        self.assertEqual(int(daily.iloc[0]["buy"]), 1)
        self.assertTrue(pd.isna(daily.iloc[0]["buy_rate_pv"]))


class TestRFMBusinessSegments(unittest.TestCase):
    def test_days_since_last_active_no_negative_and_clip(self):
        """回归测试：当天活跃为 0，且未来时间戳下限截断为 0。"""
        s = pd.Series([
            pd.Timestamp("2017-12-03 18:30:00"),
            pd.Timestamp("2017-12-04 01:00:00"),
            pd.Timestamp("2017-12-02 23:59:59"),
        ])
        days = rfm_mod.compute_days_since_last_active(s, observe_date=pd.Timestamp("2017-12-03").date())
        self.assertEqual(days.tolist(), [0, 0, 1])

    def test_rfm_five_segments_with_heavy_zero_buyers(self):
        """目标二：验证 95%+ 零购买用户分布下，5 个业务分层均有效区分且不退化为 3 类。"""
        records = []
        # 95 名零购买用户：部分近期活跃 (12-03)，部分沉睡 (11-25)
        for uid in range(1, 48):
            records.append((uid, "pv", pd.Timestamp("2017-12-03 10:00:00")))
        for uid in range(48, 96):
            records.append((uid, "pv", pd.Timestamp("2017-11-25 10:00:00")))
        # 潜力新客：1 次购买 + 近期活跃 (12-03)
        records.append((96, "buy", pd.Timestamp("2017-12-03 12:00:00")))
        # 重要价值用户：2 次购买 + 近期活跃 (12-02)
        records.append((97, "buy", pd.Timestamp("2017-12-01 12:00:00")))
        records.append((97, "buy", pd.Timestamp("2017-12-02 12:00:00")))
        # 流失风险的高价值用户：3 次购买 + 早期活跃 (11-26)
        for h in (10, 11, 12):
            records.append((98, "buy", pd.Timestamp(f"2017-11-26 {h}:00:00")))

        df = pd.DataFrame(records, columns=["user_id", "behavior_type", "datetime"])
        rfm, seg_summary = rfm_mod.compute_rfm(df, observe_date=pd.Timestamp("2017-12-03").date())

        self.assertEqual(set(seg_summary["segment"]), {
            "重要价值用户",
            "流失风险的高价值用户",
            "潜力新客（首单待复购）",
            "一般活跃未购用户",
            "低频/沉睡用户",
        })
        # 零购买用户的 F_score 必须为 1
        zero_f = rfm.loc[rfm["buy_count"] == 0, "F_score"].unique().tolist()
        self.assertEqual(zero_f, [1])
        self.assertTrue((rfm["days_since_last_active"] >= 0).all())

    def test_score_recency_days_bins(self):
        """验证 R 业务分箱全区间打分：0-1天=5, 2-3天=4, 4-5天=3, 6-7天=2, >=8天=1。"""
        days = pd.Series([0, 1, 2, 3, 4, 5, 6, 7, 8])
        scores = rfm_mod.score_recency_days(days)
        self.assertEqual(scores.tolist(), [5, 5, 4, 4, 3, 3, 2, 2, 1])

    def test_score_frequency_buys_bins(self):
        """验证 F 业务分箱阈值打分：0次=1, 1次=2, 2次=3, 3-4次=4, >=5次=5。"""
        buys = pd.Series([0, 1, 2, 3, 4, 5])
        scores = rfm_mod.score_frequency_buys(buys)
        self.assertEqual(scores.tolist(), [1, 2, 3, 4, 4, 5])


class TestDualFunnelsAndSafety(unittest.TestCase):
    def test_coverage_vs_sequential_funnel_distinction(self):
        """目标三：验证覆盖口径与时序口径的严格差异（反序行为不得计入时序转化）。"""
        df = pd.DataFrame([
            # 用户 1：严格时序 pv(10:00) -> cart(10:05) -> buy(10:10)
            (1, "pv", pd.Timestamp("2017-12-01 10:00:00")),
            (1, "cart", pd.Timestamp("2017-12-01 10:05:00")),
            (1, "buy", pd.Timestamp("2017-12-01 10:10:00")),
            # 用户 2：先 buy(09:00) 后 pv(10:00)，无意向，时序不应计入 intent/buy
            (2, "buy", pd.Timestamp("2017-12-01 09:00:00")),
            (2, "pv", pd.Timestamp("2017-12-01 10:00:00")),
            # 用户 3：先 fav(09:00) 后 pv(10:00)，时序不应计入 pv_to_intent
            (3, "fav", pd.Timestamp("2017-12-01 09:00:00")),
            (3, "pv", pd.Timestamp("2017-12-01 10:00:00")),
        ], columns=["user_id", "behavior_type", "datetime"])

        cov = rfm_mod.compute_coverage_funnel(df).set_index("stage")
        seq = rfm_mod.compute_sequential_funnel(df).set_index("step")

        # 覆盖口径：3 人 pv, 1 人 cart, 1 人 fav, 2 人 cart_or_fav, 2 人 buy
        self.assertEqual(int(cov.loc["pv", "users"]), 3)
        self.assertEqual(int(cov.loc["cart_or_fav", "users"]), 2)
        self.assertEqual(int(cov.loc["buy", "users"]), 2)

        # 时序口径：3 人 pv -> 1 人 pv_to_intent -> 1 人 intent_to_buy
        self.assertEqual(int(seq.loc["pv", "users"]), 3)
        self.assertEqual(int(seq.loc["pv_to_intent(cart|fav)", "users"]), 1)
        self.assertEqual(int(seq.loc["intent_to_buy", "users"]), 1)
        self.assertAlmostEqual(float(cov.loc["buy", "user_coverage_vs_pv"]), 2 / 3, places=4)
        self.assertAlmostEqual(float(seq.loc["intent_to_buy", "overall_conv_from_pv"]), 1 / 3, places=4)

    def test_coverage_funnel_excludes_non_pv_users(self):
        """2026-11-14 复审整改：覆盖口径的 users 与 user_coverage_vs_pv 只统计
        浏览用户集合；无浏览记录的用户（即使有加购/购买）不计入覆盖人数。
        behaviors 列仍为全样本事件次数（与 Q6/Q7 的事件转化率一致）。

        合成 6 人与 tests/test_sql_funnel.py 的 SQL 用例逐人对应：
        U1/U2: pv+cart+buy；U3: buy→pv→cart；U4: fav→pv→buy；
        U5: pv+buy（无意向）；U6: cart+buy（无 pv）。
        真实样本对照：45 名无浏览用户（35 人购买、42 人加购、24 人收藏）
        被排除后，购买覆盖率由 68.4%（6,846/10,008）修正为 68.1%
        （6,811/10,008）。
        """
        df = pd.DataFrame([
            (1, "pv", pd.Timestamp("2017-12-01 10:00:00")),
            (1, "cart", pd.Timestamp("2017-12-01 10:05:00")),
            (1, "buy", pd.Timestamp("2017-12-01 10:10:00")),
            (2, "pv", pd.Timestamp("2017-12-01 10:00:00")),
            (2, "cart", pd.Timestamp("2017-12-01 10:00:00")),
            (2, "buy", pd.Timestamp("2017-12-01 10:00:00")),
            (3, "buy", pd.Timestamp("2017-12-01 09:00:00")),
            (3, "pv", pd.Timestamp("2017-12-01 10:00:00")),
            (3, "cart", pd.Timestamp("2017-12-01 11:00:00")),
            (4, "fav", pd.Timestamp("2017-12-01 09:00:00")),
            (4, "pv", pd.Timestamp("2017-12-01 10:00:00")),
            (4, "buy", pd.Timestamp("2017-12-01 11:00:00")),
            (5, "pv", pd.Timestamp("2017-12-01 10:00:00")),
            (5, "buy", pd.Timestamp("2017-12-01 10:30:00")),
            (6, "cart", pd.Timestamp("2017-12-01 09:00:00")),
            (6, "buy", pd.Timestamp("2017-12-01 09:30:00")),
        ], columns=["user_id", "behavior_type", "datetime"])
        cov = rfm_mod.compute_coverage_funnel(df).set_index("stage")
        # users：U6（无 pv）不计入任何一行
        self.assertEqual(int(cov.loc["pv", "users"]), 5)
        self.assertEqual(int(cov.loc["cart", "users"]), 3)
        self.assertEqual(int(cov.loc["fav", "users"]), 1)
        self.assertEqual(int(cov.loc["cart_or_fav", "users"]), 4)
        self.assertEqual(int(cov.loc["buy", "users"]), 5)
        self.assertAlmostEqual(float(cov.loc["cart", "user_coverage_vs_pv"]), 0.6, places=4)
        self.assertAlmostEqual(float(cov.loc["fav", "user_coverage_vs_pv"]), 0.2, places=4)
        self.assertAlmostEqual(float(cov.loc["cart_or_fav", "user_coverage_vs_pv"]), 0.8, places=4)
        self.assertAlmostEqual(float(cov.loc["buy", "user_coverage_vs_pv"]), 1.0, places=4)
        # behaviors：全样本事件次数，U6 的 cart/buy 事件仍计入
        self.assertEqual(int(cov.loc["cart", "behaviors"]), 4)
        self.assertEqual(int(cov.loc["buy", "behaviors"]), 6)

    def test_zero_division_no_nan_no_inf(self):
        """边界测试：空表与非空无 pv 数据下，两种漏斗均不产生 NaN、inf 或 ZeroDivisionError。"""
        empty_df = pd.DataFrame(columns=["user_id", "behavior_type", "datetime"])
        no_pv_df = pd.DataFrame([
            (1, "buy", pd.Timestamp("2017-12-01 10:00:00")),
            (2, "cart", pd.Timestamp("2017-12-01 11:00:00")),
        ], columns=["user_id", "behavior_type", "datetime"])
        for sample in (empty_df, no_pv_df):
            for fn in (rfm_mod.compute_coverage_funnel, rfm_mod.compute_sequential_funnel):
                res = fn(sample)
                num_cols = res.select_dtypes(include=[np.number]).columns
                self.assertFalse(res[num_cols].isna().any().any())
                self.assertFalse(np.isinf(res[num_cols]).any().any())


class TestSequentialFunnelTemporalRules(unittest.TestCase):
    """GPT 复审要求：Q3/Q9 的 ">=" 时序判断须有同秒、反序与跨商品测试。

    规则（Python 版与 SQL 版一致，均用 ">="）：
    - 意向与首次 pv 同秒、购买与首次意向同秒时，计入路径内；
      真实样本中锚点同秒为 0 例，该规则只影响合成边界数据。
    - 反序行为（购买在首次意向之前、意向在首次浏览之前）不得计入时序路径，
      但仍计入覆盖口径。
    - 漏斗是用户级、跨商品的行为顺序：实现只看 (user_id, behavior_type,
      datetime)，不看 item_id；同一用户在不同商品上的浏览/意向/购买
      按时间排序即算一条路径，不等于真实商品交易路径。
    """

    def test_same_second_intent_and_buy_counted(self):
        """同秒：cart 与 pv 同秒、buy 与 cart 同秒时，">=" 计入路径内。"""
        df = pd.DataFrame([
            (1, "pv", pd.Timestamp("2017-12-01 10:00:00")),
            (1, "cart", pd.Timestamp("2017-12-01 10:00:00")),
            (1, "buy", pd.Timestamp("2017-12-01 10:00:00")),
        ], columns=["user_id", "behavior_type", "datetime"])
        seq = rfm_mod.compute_sequential_funnel(df).set_index("step")
        self.assertEqual(int(seq.loc["pv", "users"]), 1)
        self.assertEqual(int(seq.loc["pv_to_intent(cart|fav)", "users"]), 1)
        self.assertEqual(int(seq.loc["intent_to_buy", "users"]), 1)

    def test_reverse_order_excluded_from_sequential_but_kept_in_coverage(self):
        """反序：购买在首次意向之前、意向在首次浏览之前，不得计入时序路径。"""
        df = pd.DataFrame([
            # 用户 1：先 buy(09:00) 后 cart(11:00)，有 pv 但购买在意向之前
            (1, "pv", pd.Timestamp("2017-12-01 10:00:00")),
            (1, "buy", pd.Timestamp("2017-12-01 09:00:00")),
            (1, "cart", pd.Timestamp("2017-12-01 11:00:00")),
            # 用户 2：先 fav(09:00) 后 pv(10:00)，意向在浏览之前
            (2, "fav", pd.Timestamp("2017-12-01 09:00:00")),
            (2, "pv", pd.Timestamp("2017-12-01 10:00:00")),
            (2, "buy", pd.Timestamp("2017-12-01 11:00:00")),
        ], columns=["user_id", "behavior_type", "datetime"])
        cov = rfm_mod.compute_coverage_funnel(df).set_index("stage")
        seq = rfm_mod.compute_sequential_funnel(df).set_index("step")
        # 覆盖口径：两人都有 pv 与 buy，用户 1 有 cart，用户 2 有 fav
        self.assertEqual(int(cov.loc["buy", "users"]), 2)
        self.assertEqual(int(cov.loc["cart_or_fav", "users"]), 2)
        # 时序口径：用户 1 计入 pv_to_intent 但不计入 intent_to_buy；
        # 用户 2 不计入 pv_to_intent（意向在 pv 之前），也不计入 intent_to_buy
        self.assertEqual(int(seq.loc["pv_to_intent(cart|fav)", "users"]), 1)
        self.assertEqual(int(seq.loc["intent_to_buy", "users"]), 0)

    def test_cross_item_path_counts_at_user_level(self):
        """跨商品：pv 商品 A → cart 商品 B → buy 商品 C，仍计入时序路径。

        实现只使用 (user_id, behavior_type, datetime)，不看 item_id，
        因此这是设计如此的用户级跨商品顺序，不是按商品的交易路径。
        """
        df = pd.DataFrame([
            (1, 101, "pv", pd.Timestamp("2017-12-01 10:00:00")),
            (1, 202, "cart", pd.Timestamp("2017-12-01 10:05:00")),
            (1, 303, "buy", pd.Timestamp("2017-12-01 10:10:00")),
        ], columns=["user_id", "item_id", "behavior_type", "datetime"])
        seq = rfm_mod.compute_sequential_funnel(df).set_index("step")
        self.assertEqual(int(seq.loc["pv_to_intent(cart|fav)", "users"]), 1)
        self.assertEqual(int(seq.loc["intent_to_buy", "users"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
