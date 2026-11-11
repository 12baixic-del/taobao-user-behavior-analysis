"""02 - 淘宝用户行为：转化漏斗 + 次日留存 + RFM 分层

运行（在 project-data-taobao/ 根目录）：
    python notebooks/02_funnel_rfm_analysis.py
    先跑 01_data_cleaning_eda.py，生成 outputs/taobao_clean_sample.csv

漏斗口径（先说清楚，避免把数字讲错）：
    漏斗按“行为数”和“人数”各算一遍。pv 次数远大于购买次数，
    因为一个用户一天可以浏览很多次、只买一次，这是正常现象，
    不是数据错了。转化率 = 下一环节 / 上一环节。

留存口径：
    次日留存 = 第 D 天活跃的用户中，第 D+1 天又活跃的占比。
    数据只有 9 天（2017-11-25 至 2017-12-03），12-03 当天没有次日可算。

RFM 口径（没有价格列，所以没有 M）：
    R = 距上次活跃天数（观察点 = 样本中最晚一天 2017-12-03）
    F = 购买次数（buy 行为数）
    没有 M：数据集没有价格和订单金额，不编金额、不编 GMV。
    R、F 用百分位排名打 1-5 分，避免 qcut 在大量并列值上报错。

产出（outputs/）：
    funnel_summary.csv   漏斗各环节人数、行为数、转化率
    retention_summary.csv  每日次日留存率
    rfm_users.csv        每个用户的 R / F 值、得分与分层
    figures/*.png        漏斗图、留存图、RFM 分层图
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 不开图形窗口，直接保存图片
import matplotlib.pyplot as plt
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
OUT_DIR = BASE / "outputs"
FIG_DIR = OUT_DIR / "figures"
SAMPLE_FILE = OUT_DIR / "taobao_clean_sample.csv"

# 观察点：样本中最晚一天（数据集最晚 2017-12-03）
OBSERVE_DATE = pd.Timestamp("2017-12-03").date()


def check_input() -> None:
    """01 的产出不存在时，提示先跑 01，不报看不懂的错。"""
    if SAMPLE_FILE.exists():
        return
    print("缺少输入文件：outputs/taobao_clean_sample.csv")
    print("请先运行：python notebooks/01_data_cleaning_eda.py")
    sys.exit(2)


def score_1_to_5(rank_pct: pd.Series, higher_is_better: bool) -> pd.Series:
    """百分位排名转 1-5 分。rank_pct 是 0-1 的排名，越高表示值越大。

    higher_is_better=True：值越大分越高（如购买次数）。
    higher_is_better=False：值越小分越高（如距上次活跃天数，越近越好）。
    """
    if higher_is_better:
        return (rank_pct * 5).clip(lower=0, upper=4.999).astype(int) + 1
    return ((1 - rank_pct) * 5).clip(lower=0, upper=4.999).astype(int) + 1


def main() -> None:
    check_input()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    FIG_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(SAMPLE_FILE, parse_dates=["datetime"])
    df["date"] = pd.to_datetime(df["date"])
    print(f"读入抽样数据：{len(df):,} 行，{df['user_id'].nunique():,} 人")
    print(f"时间范围：{df['date'].min().date()} 至 {df['date'].max().date()}")

    # ---------- 转化漏斗 ----------
    # 人数口径：做过该行为的用户数；行为数口径：该行为的总次数
    stages = ["pv", "cart", "fav", "buy"]
    funnel_rows = []
    for stage in stages:
        sub = df[df["behavior_type"] == stage]
        funnel_rows.append({
            "stage": stage,
            "users": int(sub["user_id"].nunique()),
            "behaviors": int(len(sub)),
        })
    funnel = pd.DataFrame(funnel_rows)
    funnel["user_rate_vs_pv"] = funnel["users"] / funnel.loc[0, "users"]
    funnel["behavior_rate_vs_pv"] = funnel["behaviors"] / funnel.loc[0, "behaviors"]
    # 环节转化率：本环节 / 上一环节（人数口径）
    funnel["user_conv_from_prev"] = funnel["users"] / funnel["users"].shift(1)
    funnel.loc[0, "user_conv_from_prev"] = 1.0
    funnel.to_csv(OUT_DIR / "funnel_summary.csv", index=False)
    print("\n转化漏斗（人数口径 + 行为数口径）：")
    print(funnel.round(4))
    buy_users = int(funnel.loc[funnel["stage"] == "buy", "users"].iloc[0])
    pv_users = int(funnel.loc[funnel["stage"] == "pv", "users"].iloc[0])
    print(f"\n整体购买转化率（购买人数 / 浏览人数）：{buy_users / pv_users:.2%}")
    print("注意：漏斗是按 9 天全时段汇总的，不是单个用户的单次访问路径。")

    # ---------- 次日留存 ----------
    active_by_day = df.groupby(df["date"].dt.date)["user_id"].apply(set)
    retention_rows = []
    dates = sorted(active_by_day.index)
    for i in range(len(dates) - 1):
        today, tomorrow = dates[i], dates[i + 1]
        # 只有相邻两天才算次日留存；如果中间缺一天，跳过不算
        if (tomorrow - today).days != 1:
            continue
        today_users = active_by_day[today]
        retained = len(today_users & active_by_day[tomorrow])
        retention_rows.append({
            "date": today,
            "active_users": len(today_users),
            "retained_next_day": retained,
            "retention_rate": retained / len(today_users) if today_users else 0.0,
        })
    retention = pd.DataFrame(retention_rows)
    retention.to_csv(OUT_DIR / "retention_summary.csv", index=False)
    print("\n次日留存：")
    print(retention.round(4))
    if not retention.empty:
        print(f"平均次日留存率：{retention['retention_rate'].mean():.2%}")

    # ---------- RFM 分层（无 M） ----------
    user_agg = df.groupby("user_id").agg(
        last_active=("datetime", "max"),
        buy_count=("behavior_type", lambda s: int((s == "buy").sum())),
        pv_count=("behavior_type", lambda s: int((s == "pv").sum())),
        cart_count=("behavior_type", lambda s: int((s == "cart").sum())),
        fav_count=("behavior_type", lambda s: int((s == "fav").sum())),
        total_behaviors=("behavior_type", "size"),
    ).reset_index()
    user_agg["last_active_date"] = user_agg["last_active"].dt.date
    user_agg["days_since_last_active"] = (
        pd.Timestamp(OBSERVE_DATE) - user_agg["last_active"]
    ).dt.days
    # R：天数越小越好；F：购买次数越多越好
    user_agg["R_score"] = score_1_to_5(
        user_agg["days_since_last_active"].rank(pct=True), higher_is_better=False
    )
    user_agg["F_score"] = score_1_to_5(
        user_agg["buy_count"].rank(pct=True), higher_is_better=True
    )

    def segment(row) -> str:
        """按 R、F 得分分层，名字业务化。"""
        if row["F_score"] >= 4 and row["R_score"] >= 4:
            return "重要价值用户"
        if row["F_score"] >= 4 and row["R_score"] <= 2:
            return "流失风险的高价值用户"
        if row["F_score"] <= 2 and row["R_score"] >= 4:
            return "新客 / 低频活跃用户"
        if row["F_score"] <= 2 and row["R_score"] <= 2:
            return "流失风险的低频用户"
        return "一般用户"

    user_agg["segment"] = user_agg.apply(segment, axis=1)
    rfm = user_agg[[
        "user_id", "last_active_date", "days_since_last_active",
        "buy_count", "pv_count", "cart_count", "fav_count",
        "total_behaviors", "R_score", "F_score", "segment",
    ]].sort_values(["R_score", "F_score"], ascending=False)
    rfm.to_csv(OUT_DIR / "rfm_users.csv", index=False)

    seg_summary = (
        rfm.groupby("segment")
        .agg(users=("user_id", "count"),
             avg_buy_count=("buy_count", "mean"),
             avg_days_since_active=("days_since_last_active", "mean"))
        .reset_index()
    )
    seg_summary["user_share"] = seg_summary["users"] / seg_summary["users"].sum()
    seg_summary = seg_summary.sort_values("users", ascending=False)
    seg_summary.to_csv(OUT_DIR / "rfm_summary.csv", index=False)
    print("\nRFM 分层（R=距上次活跃天数，F=购买次数，无 M）：")
    print(seg_summary.round(3))
    buyers = int((rfm["buy_count"] > 0).sum())
    print(f"\n有购买行为的用户：{buyers:,} 人，占全部 {buyers / len(rfm):.2%}")
    print("提醒：没有价格列，无法算消费金额和 GMV，报告里不要写金额结论。")

    # ---------- 图表 ----------
    # 注意：matplotlib 默认字体 DejaVu Sans 没有中文字，
    # RFM 分层名是中文，直接画图会显示成方框（Glyph missing 警告）。
    # 所以图表只用英文标签，中文分层名保留在 rfm_users.csv 和 REPORT.md 里。
    SEGMENT_EN = {
        "重要价值用户": "High-value",
        "流失风险的高价值用户": "High-value at risk",
        "新客 / 低频活跃用户": "New / low-frequency",
        "流失风险的低频用户": "Low-frequency at risk",
        "一般用户": "Regular",
    }
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    # 图 1：漏斗（人数口径）
    fig, ax = plt.subplots(figsize=(8, 4))
    funnel.set_index("stage")["users"].reindex(stages).plot.bar(ax=ax)
    ax.set_title("Conversion funnel by users (sampled)")
    ax.set_ylabel("Users")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "funnel_users.png", dpi=150)
    plt.close(fig)

    # 图 2：次日留存
    if not retention.empty:
        fig, ax = plt.subplots(figsize=(10, 4))
        retention.set_index("date")["retention_rate"].plot(ax=ax, marker="o")
        ax.set_title("Next-day retention rate (sampled)")
        ax.set_xlabel("Date")
        ax.set_ylabel("Retention rate")
        ax.set_ylim(0, 1)
        fig.tight_layout()
        fig.savefig(FIG_DIR / "retention.png", dpi=150)
        plt.close(fig)

    # 图 3：RFM 分层人数（图表用英文标签，避免中文字体缺字）
    # Regular 占 97.2%，另两类只有 1.5% / 1.2%，直接画条形图小类几乎看不见，
    # 所以每根条后面都标上具体人数，y 轴标签用 Segment，不用内部列名 segment_en。
    fig, ax = plt.subplots(figsize=(10, 5))
    seg_chart = seg_summary.copy()
    seg_chart["segment_en"] = seg_chart["segment"].map(SEGMENT_EN).fillna(seg_chart["segment"])
    seg_chart.set_index("segment_en")["users"].sort_values().plot.barh(ax=ax)
    ax.set_title("RFM segments by user count (sampled)")
    ax.set_xlabel("Users")
    ax.set_ylabel("Segment")
    for i, v in enumerate(seg_chart.set_index("segment_en")["users"].sort_values()):
        ax.text(v + 8000, i, f"{v:,}", va="center", fontsize=10)
    ax.set_xlim(0, 580000)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "rfm_segments.png", dpi=150)
    plt.close(fig)

    print("已保存图表到 outputs/figures/：funnel_users、retention、rfm_segments")
    print("\n完成。下一步：按 outputs/ 里的数字写一页结论（3 个发现 + 3 条建议），"
          "每条建议绑定一个数字证据。")


if __name__ == "__main__":
    main()
