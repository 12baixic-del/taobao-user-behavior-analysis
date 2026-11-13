"""02 - 漏斗（覆盖口径 vs 时序口径）+ 次日留存 + RFM 业务分箱分层

在项目根目录运行：
    python notebooks/02_funnel_rfm_analysis.py
    需先运行 01，生成 outputs/taobao_clean_sample.csv

核心指标口径说明：
    1. 漏斗区分两种口径，且严格区分独立用户数 (users) 与行为次数 (behaviors)：
       - 普通行为覆盖漏斗 (funnel_summary.csv)：统计各行为（含 cart_or_fav 并列意向）的独立覆盖人数与次数。
       - 时序转化漏斗 (funnel_sequential_summary.csv)：在用户粒度按 datetime 严格校验先后顺序
         （pv -> pv 后 cart/fav -> intent 后 buy），反映真实路径转化。
    2. RFM 分层（无价格列故无 M）：
       - R = 距观察点日期的纯日历天数差（当天活跃为 0，无负数），按业务天数区间打 1-5 分。
       - F = 购买次数，按业务阈值离散打分（0次=1分, 1次=2分, 2次=3分, 3-4次=4分, >=5次=5分），
         彻底解决大量零购买用户导致 rank(pct=True) 分数挤压的问题。
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 不开图形窗口，直接保存图片
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
OUT_DIR = BASE / "outputs"
FIG_DIR = OUT_DIR / "figures"
SAMPLE_FILE = OUT_DIR / "taobao_clean_sample.csv"

OBSERVE_DATE = pd.Timestamp("2017-12-03").date()


def check_input() -> None:
    """01 的产出没有的话，提醒先跑 01。"""
    if SAMPLE_FILE.exists():
        return
    print("缺少输入文件：outputs/taobao_clean_sample.csv")
    print("请先运行：python notebooks/01_data_cleaning_eda.py")
    sys.exit(2)


def safe_div(numerator: pd.Series | float, denominator: pd.Series | float) -> pd.Series | float:
    """安全除法：分母为 0 或缺失时统一返回 0.0，避免 ZeroDivisionError / NaN / inf。"""
    if isinstance(numerator, pd.Series) or isinstance(denominator, pd.Series):
        idx = numerator.index if isinstance(numerator, pd.Series) else denominator.index
        num = pd.Series(numerator, index=idx, dtype="float64")
        den = pd.Series(denominator, index=idx, dtype="float64")
        valid = (den > 0) & den.notna() & num.notna()
        return pd.Series(
            np.where(valid, num / den.where(valid, 1.0), 0.0),
            index=idx,
            dtype="float64",
        )
    if denominator is None or pd.isna(denominator) or denominator <= 0 or numerator is None or pd.isna(numerator):
        return 0.0
    return float(numerator) / float(denominator)


def compute_days_since_last_active(last_active: pd.Series, observe_date) -> pd.Series:
    """计算距观察日（纯日历日）的整数天数差，下限截断为 0，避免出现负数。"""
    obs_day = pd.Timestamp(observe_date).normalize()
    last_day = pd.to_datetime(last_active).dt.normalize()
    return (obs_day - last_day).dt.days.clip(lower=0)


def score_recency_days(days: pd.Series) -> pd.Series:
    """R 业务分箱（距观察日天数越近分数越高）：0-1天=5, 2-3天=4, 4-5天=3, 6-7天=2, >=8天=1。"""
    d = pd.to_numeric(days, errors="coerce").fillna(999).clip(lower=0)
    return pd.Series(
        np.select(
            [d <= 1, d <= 3, d <= 5, d <= 7],
            [5, 4, 3, 2],
            default=1,
        ),
        index=days.index,
        dtype="int64",
    )


def score_frequency_buys(buy_count: pd.Series) -> pd.Series:
    """F 业务阈值分箱（显式处理大量零购买用户）：0次=1, 1次=2, 2次=3, 3-4次=4, >=5次=5。"""
    f = pd.to_numeric(buy_count, errors="coerce").fillna(0).clip(lower=0)
    return pd.Series(
        np.select(
            [f == 0, f == 1, f == 2, f <= 4],
            [1, 2, 3, 4],
            default=5,
        ),
        index=buy_count.index,
        dtype="int64",
    )


def compute_coverage_funnel(df: pd.DataFrame) -> pd.DataFrame:
    """口径 A：普通行为覆盖漏斗（区分独立用户数与行为次数，cart 与 fav 并列展示）。

    口径规则（2026-11-14 复审整改）：`users` 与 `user_coverage_vs_pv`
    只统计浏览用户集合（发生过 pv 的用户）——分子分母同一集合；45 名
    无浏览记录的用户（其中 35 人购买、42 人加购、24 人收藏）不计入覆盖
    人数。`behaviors` 与 `behavior_ratio_vs_pv` 仍为全样本事件口径
    （购买事件 / 浏览事件），与 Q6/Q7 的事件转化率一致，见 REPORT §2.1。
    """
    pv_user_ids = set(df.loc[df["behavior_type"] == "pv", "user_id"])
    stages = [
        ("pv", ["pv"]),
        ("cart", ["cart"]),
        ("fav", ["fav"]),
        ("cart_or_fav", ["cart", "fav"]),
        ("buy", ["buy"]),
    ]
    rows = []
    for stage_name, b_list in stages:
        sub = df[df["behavior_type"].isin(b_list)] if not df.empty else df
        # users：仅计浏览用户集合内的独立用户；behaviors：全样本事件次数
        sub_users = sub[sub["user_id"].isin(pv_user_ids)] if not sub.empty else sub
        rows.append({
            "stage": stage_name,
            "users": int(sub_users["user_id"].nunique()) if not sub_users.empty else 0,
            "behaviors": int(len(sub)),
        })
    funnel = pd.DataFrame(rows)
    pv_users = float(funnel.loc[0, "users"])
    pv_behaviors = float(funnel.loc[0, "behaviors"])
    funnel["user_coverage_vs_pv"] = safe_div(funnel["users"], pv_users)
    funnel["behavior_ratio_vs_pv"] = safe_div(funnel["behaviors"], pv_behaviors)
    return funnel


def compute_sequential_funnel(df: pd.DataFrame) -> pd.DataFrame:
    """口径 B：按时间顺序定义的转化漏斗（pv -> pv 后 cart/fav -> intent 后 buy）。"""
    cols = ["step", "users", "behaviors", "step_conv_from_prev", "overall_conv_from_pv"]
    if df.empty:
        return pd.DataFrame([
            {"step": "pv", "users": 0, "behaviors": 0, "step_conv_from_prev": 0.0, "overall_conv_from_pv": 0.0},
            {"step": "pv_to_intent(cart|fav)", "users": 0, "behaviors": 0, "step_conv_from_prev": 0.0, "overall_conv_from_pv": 0.0},
            {"step": "intent_to_buy", "users": 0, "behaviors": 0, "step_conv_from_prev": 0.0, "overall_conv_from_pv": 0.0},
        ], columns=cols)

    work = df[["user_id", "behavior_type", "datetime"]].copy()
    work["datetime"] = pd.to_datetime(work["datetime"])

    # Step 1：首次浏览时间
    pv_df = work[work["behavior_type"] == "pv"]
    first_pv = pv_df.groupby("user_id")["datetime"].min()
    u_pv = set(first_pv.index)

    # Step 2：在首次浏览之后（>=）发生加购或收藏的用户与行为
    intent_df = work[work["behavior_type"].isin(["cart", "fav"]) & work["user_id"].isin(u_pv)].copy()
    if not intent_df.empty:
        intent_df["t_pv"] = intent_df["user_id"].map(first_pv)
        intent_valid = intent_df[intent_df["datetime"] >= intent_df["t_pv"]]
        first_intent = intent_valid.groupby("user_id")["datetime"].min()
    else:
        intent_valid = intent_df
        first_intent = pd.Series(dtype="datetime64[ns]")
    u_intent = set(first_intent.index)

    # Step 3：在首次意向之后（>=）发生购买的用户与行为
    buy_df = work[(work["behavior_type"] == "buy") & work["user_id"].isin(u_intent)].copy()
    if not buy_df.empty:
        buy_df["t_intent"] = buy_df["user_id"].map(first_intent)
        buy_valid = buy_df[buy_df["datetime"] >= buy_df["t_intent"]]
        u_buy = set(buy_valid["user_id"].unique())
    else:
        buy_valid = buy_df
        u_buy = set()

    counts = [
        ("pv", len(u_pv), int(len(pv_df))),
        ("pv_to_intent(cart|fav)", len(u_intent), int(len(intent_valid))),
        ("intent_to_buy", len(u_buy), int(len(buy_valid))),
    ]
    seq_df = pd.DataFrame(counts, columns=["step", "users", "behaviors"])
    pv_u = float(seq_df.loc[0, "users"])
    prev_u = seq_df["users"].shift(1).fillna(seq_df["users"])
    seq_df["step_conv_from_prev"] = safe_div(seq_df["users"], prev_u)
    seq_df.loc[0, "step_conv_from_prev"] = 1.0 if pv_u > 0 else 0.0
    seq_df["overall_conv_from_pv"] = safe_div(seq_df["users"], pv_u)
    return seq_df[cols]


def compute_retention(df: pd.DataFrame) -> pd.DataFrame:
    """计算连续日期的次日留存。"""
    cols = ["date", "active_users", "retained_next_day", "retention_rate"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    active_by_day = df.groupby(pd.to_datetime(df["date"]).dt.date)["user_id"].apply(set)
    retention_rows = []
    dates = sorted(active_by_day.index)
    for i in range(len(dates) - 1):
        today, tomorrow = dates[i], dates[i + 1]
        if (tomorrow - today).days != 1:
            continue
        today_users = active_by_day[today]
        retained = len(today_users & active_by_day[tomorrow])
        retention_rows.append({
            "date": today,
            "active_users": len(today_users),
            "retained_next_day": retained,
            "retention_rate": safe_div(retained, len(today_users)),
        })
    return pd.DataFrame(retention_rows, columns=cols)


def compute_rfm(df: pd.DataFrame, observe_date=OBSERVE_DATE) -> tuple[pd.DataFrame, pd.DataFrame]:
    """向量化计算 RFM 指标与 5 类业务分层（解决零购买用户扎堆导致的分层退化）。"""
    rfm_cols = [
        "user_id", "last_active_date", "days_since_last_active",
        "buy_count", "pv_count", "cart_count", "fav_count",
        "total_behaviors", "R_score", "F_score", "segment",
    ]
    seg_cols = ["segment", "users", "avg_buy_count", "avg_days_since_active", "user_share"]
    if df.empty:
        return pd.DataFrame(columns=rfm_cols), pd.DataFrame(columns=seg_cols)

    b_counts = (
        df.groupby(["user_id", "behavior_type"], observed=False)
        .size()
        .unstack(fill_value=0)
    )
    user_agg = pd.DataFrame(index=b_counts.index)
    user_agg["last_active"] = df.groupby("user_id")["datetime"].max()
    user_agg["buy_count"] = b_counts["buy"].astype(int) if "buy" in b_counts.columns else 0
    user_agg["pv_count"] = b_counts["pv"].astype(int) if "pv" in b_counts.columns else 0
    user_agg["cart_count"] = b_counts["cart"].astype(int) if "cart" in b_counts.columns else 0
    user_agg["fav_count"] = b_counts["fav"].astype(int) if "fav" in b_counts.columns else 0
    user_agg["total_behaviors"] = (
        user_agg["buy_count"] + user_agg["pv_count"] + user_agg["cart_count"] + user_agg["fav_count"]
    )
    user_agg = user_agg.reset_index()

    user_agg["last_active_date"] = pd.to_datetime(user_agg["last_active"]).dt.date
    user_agg["days_since_last_active"] = compute_days_since_last_active(
        user_agg["last_active"], observe_date=observe_date
    )
    user_agg["R_score"] = score_recency_days(user_agg["days_since_last_active"])
    user_agg["F_score"] = score_frequency_buys(user_agg["buy_count"])

    r = user_agg["R_score"]
    f = user_agg["F_score"]
    user_agg["segment"] = np.select(
        [
            (f >= 3) & (r >= 4),
            (f >= 3) & (r <= 3),
            (f == 2) & (r >= 4),
            (f == 1) & (r >= 4),
        ],
        [
            "重要价值用户",
            "流失风险的高价值用户",
            "潜力新客（首单待复购）",
            "一般活跃未购用户",
        ],
        default="低频/沉睡用户",
    )

    rfm = user_agg[rfm_cols].sort_values(["R_score", "F_score"], ascending=False)
    seg_summary = (
        rfm.groupby("segment")
        .agg(
            users=("user_id", "count"),
            avg_buy_count=("buy_count", "mean"),
            avg_days_since_active=("days_since_last_active", "mean"),
        )
        .reset_index()
    )
    seg_summary["user_share"] = safe_div(seg_summary["users"], float(seg_summary["users"].sum()))
    seg_summary = seg_summary.sort_values("users", ascending=False)
    return rfm, seg_summary[seg_cols]


def main() -> None:
    check_input()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    FIG_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(
        SAMPLE_FILE,
        usecols=["user_id", "behavior_type", "datetime"],
        dtype={"user_id": "int64", "behavior_type": "category"},
        parse_dates=["datetime"],
    )
    if df.empty:
        print("输入样本为空，跳过分析。")
        return

    df["date"] = df["datetime"].dt.normalize()
    print(f"读入抽样数据：{len(df):,} 行，{df['user_id'].nunique():,} 人")
    print(f"时间范围：{df['date'].min().date()} 至 {df['date'].max().date()}")

    cov_funnel = compute_coverage_funnel(df)
    seq_funnel = compute_sequential_funnel(df)
    cov_funnel.to_csv(OUT_DIR / "funnel_summary.csv", index=False)
    seq_funnel.to_csv(OUT_DIR / "funnel_sequential_summary.csv", index=False)
    print("\n[口径 A] 普通行为覆盖漏斗（独立用户数 vs 行为次数）：")
    print(cov_funnel.round(4))
    print("\n[口径 B] 时序转化漏斗（pv -> pv后cart/fav -> intent后buy）：")
    print(seq_funnel.round(4))

    retention = compute_retention(df)
    retention.to_csv(OUT_DIR / "retention_summary.csv", index=False)
    print("\n次日留存：")
    print(retention.round(4))
    if not retention.empty:
        print(f"平均次日留存率：{retention['retention_rate'].mean():.2%}")

    observe_date = OBSERVE_DATE  # 锁定为常量，防止样本最大日期变化导致 R 静默漂移
    rfm, seg_summary = compute_rfm(df, observe_date=observe_date)
    rfm.to_csv(OUT_DIR / "rfm_users.csv", index=False)
    seg_summary.to_csv(OUT_DIR / "rfm_summary.csv", index=False)
    print("\nRFM 业务分层（R=距观察日天数分箱，F=购买次数阈值分箱，无 M）：")
    print(seg_summary.round(3))

    SEGMENT_EN = {
        "重要价值用户": "High-value",
        "流失风险的高价值用户": "High-value at risk",
        "潜力新客（首单待复购）": "Potential new (1 buy)",
        "一般活跃未购用户": "Active non-buyer",
        "低频/沉睡用户": "Low-freq / dormant",
    }
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    # 图 1：时序转化漏斗（人数口径）
    fig, ax = plt.subplots(figsize=(8, 4))
    seq_funnel.set_index("step")["users"].plot.bar(ax=ax)
    ax.set_title("Sequential conversion funnel by users (user-sampled)")
    ax.set_ylabel("Users")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "funnel_users.png", dpi=150)
    plt.close(fig)

    # 图 2：次日留存
    if not retention.empty:
        fig, ax = plt.subplots(figsize=(10, 4))
        retention.set_index("date")["retention_rate"].plot(ax=ax, marker="o")
        ax.set_title("Next-day retention rate (user-sampled)")
        ax.set_xlabel("Date")
        ax.set_ylabel("Retention rate")
        ax.set_ylim(0, 1)
        fig.tight_layout()
        fig.savefig(FIG_DIR / "retention.png", dpi=150)
        plt.close(fig)

    # 图 3：RFM 分层人数
    fig, ax = plt.subplots(figsize=(10, 5))
    seg_chart = seg_summary.copy()
    seg_chart["segment_en"] = seg_chart["segment"].map(SEGMENT_EN).fillna(seg_chart["segment"])
    ordered = seg_chart.set_index("segment_en")["users"].sort_values()
    ordered.plot.barh(ax=ax)
    ax.set_title("RFM segments by user count (user-sampled)")
    ax.set_xlabel("Users")
    ax.set_ylabel("Segment")
    max_users = max(1, int(ordered.max()))
    for i, v in enumerate(ordered):
        ax.text(v + max_users * 0.015, i, f"{v:,}", va="center", fontsize=10)
    ax.set_xlim(0, max_users * 1.18)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "rfm_segments.png", dpi=150)
    plt.close(fig)

    print("已保存图表到 outputs/figures/：funnel_users、retention、rfm_segments")


if __name__ == "__main__":
    main()
