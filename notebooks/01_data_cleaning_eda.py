"""01 - 清洗 + EDA

在 project-data-taobao/ 根目录跑：
    python notebooks/01_data_cleaning_eda.py

数据是天池 649 的 UserBehavior.csv，987,994 用户，100,150,807 条记录，
2017-11-25 到 2017-12-03。字段就 5 列，没有价格列，做不了 GMV。

跑完出这些东西，放 outputs/ 里：
    taobao_clean_sample.csv  清洗 + 抽样后的记录，大概 100 万行
    daily_summary.csv        每天的 PV、UV、各行为数、购买转化率
    figures/*.png            每天 PV/UV、行为类型、Top 类目、小时趋势、购买转化
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 不开图形窗口，直接保存图片
import matplotlib.pyplot as plt
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data" / "raw"
OUT_DIR = BASE / "outputs"
FIG_DIR = OUT_DIR / "figures"

DATA_FILE = DATA_DIR / "UserBehavior.csv"
COLUMNS = ["user_id", "item_id", "category_id", "behavior_type", "timestamp"]
VALID_BEHAVIORS = {"pv", "buy", "cart", "fav"}

# 有效时间就这 9 天，2017-11-25 到 2017-12-03
START = pd.Timestamp("2017-11-25 00:00:00")
END = pd.Timestamp("2017-12-03 23:59:59")

# 1 亿行太多了，抽样再分析，种子固定住，结果才能复现
SAMPLE_N = 1_000_000
SEED = 42


def check_file() -> None:
    """数据没下的话，打印下载方法就退出。"""
    if DATA_FILE.exists():
        return
    print("数据还没下载，缺少以下文件：")
    print("  - data/raw/UserBehavior.csv")
    print()
    print("下载方法：")
    print("  1. 打开 https://tianchi.aliyun.com/dataset/649")
    print("  2. 用淘宝账号登录，下载后解压，把 UserBehavior.csv 放到 data/raw/")
    print("  3. 文件约 1 亿行、解压后约 3-4 GB，下载时不要关浏览器。")
    print("  详细步骤见 data/README.md")
    sys.exit(2)


def main() -> None:
    check_file()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    FIG_DIR.mkdir(parents=True, exist_ok=True)

    # ---------- 读入 ----------
    # 文件没表头，5 列按字符串 / 整数读，时间戳后面再转
    df = pd.read_csv(
        DATA_FILE,
        header=None,
        names=COLUMNS,
        dtype={"user_id": "int64", "item_id": "int64",
               "category_id": "int64", "behavior_type": "str"},
    )
    print(f"原始数据：{len(df):,} 行，{df.shape[1]} 列")
    print(f"去重前完全重复行：{int(df.duplicated().sum()):,} 行")
    df = df.drop_duplicates().copy()

    # 缺失率：只列缺失 > 0 的列
    missing = df.isna().mean()
    missing = missing[missing > 0].sort_values(ascending=False)
    print("\n缺失率 > 0 的列：")
    if missing.empty:
        print("  无缺失")
    else:
        for col, rate in missing.items():
            print(f"  {col}: {rate:.2%}")
    df = df.dropna().copy()

    # ---------- 清洗：行为类型 ----------
    bad_behavior = df[~df["behavior_type"].isin(VALID_BEHAVIORS)]
    print(f"\n行为类型不在 pv/buy/cart/fav 中的行：{len(bad_behavior):,} 行（已剔除）")
    if not bad_behavior.empty:
        print(bad_behavior["behavior_type"].value_counts())
    df = df[df["behavior_type"].isin(VALID_BEHAVIORS)].copy()

    # ---------- 清洗：时间戳 ----------
    # timestamp 是秒级时间戳，转不出来的记为空，扔掉
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", errors="coerce")
    bad_time = int(df["datetime"].isna().sum())
    print(f"\n时间戳无法解析的行：{bad_time:,} 行（已剔除）")
    df = df.dropna(subset=["datetime"]).copy()

    # 超出 2017-11-25 到 2017-12-03 的是异常时间戳，扔掉，单独报一下数
    out_of_range = df[(df["datetime"] < START) | (df["datetime"] > END)]
    print(f"超出有效窗口（{START.date()} 至 {END.date()}）的行："
          f"{len(out_of_range):,} 行（已剔除）")
    if not out_of_range.empty:
        print("  超范围时间戳最小值：", out_of_range["datetime"].min())
        print("  超范围时间戳最大值：", out_of_range["datetime"].max())
    df = df[(df["datetime"] >= START) & (df["datetime"] <= END)].copy()
    print(f"清洗后剩余：{len(df):,} 行")

    # ---------- 抽样 ----------
    if len(df) > SAMPLE_N:
        df = df.sample(n=SAMPLE_N, random_state=SEED).copy()
        print(f"抽样：固定随机种子 {SEED}，抽取 {SAMPLE_N:,} 行做后续分析")
    else:
        print(f"行数不足 {SAMPLE_N:,} 行，不抽样，全量分析")
    df["date"] = df["datetime"].dt.date
    df["hour"] = df["datetime"].dt.hour

    print(f"\n抽样后用户数：{df['user_id'].nunique():,} 人")
    print(f"抽样后商品数：{df['item_id'].nunique():,} 个")
    print(f"抽样后类目数：{df['category_id'].nunique():,} 个")
    print("行为类型分布：")
    print(df["behavior_type"].value_counts())
    print(df["behavior_type"].value_counts(normalize=True).round(4))

    # ---------- 保存清洗结果 ----------
    df.to_csv(OUT_DIR / "taobao_clean_sample.csv", index=False)
    print("\n已保存：outputs/taobao_clean_sample.csv")

    # ---------- 每日汇总：PV / UV / 各行为数 ----------
    daily = df.groupby("date").agg(
        pv=("behavior_type", lambda s: int((s == "pv").sum())),
        buy=("behavior_type", lambda s: int((s == "buy").sum())),
        cart=("behavior_type", lambda s: int((s == "cart").sum())),
        fav=("behavior_type", lambda s: int((s == "fav").sum())),
        total=("behavior_type", "size"),
    )
    daily["uv"] = df.groupby("date")["user_id"].nunique()
    daily["buy_users"] = (
        df[df["behavior_type"] == "buy"].groupby("date")["user_id"].nunique()
    )
    daily["buy_rate_pv"] = daily["buy"] / daily["pv"]  # 购买数 / 浏览数
    daily["buy_rate_uv"] = daily["buy_users"] / daily["uv"]  # 购买人数 / 活跃人数
    daily.index.name = "date"
    daily.to_csv(OUT_DIR / "daily_summary.csv")
    print("\n每日汇总（PV / UV / 购买转化率）：")
    print(daily.round(4))

    # ---------- EDA 图表 ----------
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    # 图 1：每日 PV 与 UV
    fig, ax = plt.subplots(figsize=(10, 4))
    daily["pv"].plot(ax=ax, label="PV")
    daily["uv"].plot(ax=ax, label="UV")
    ax.set_title("Daily PV and UV (sampled)")
    ax.set_xlabel("Date")
    ax.set_ylabel("Count")
    ax.legend()
    fig.tight_layout()
    fig.savefig(FIG_DIR / "daily_pv_uv.png", dpi=150)
    plt.close(fig)

    # 图 2：行为类型占比
    fig, ax = plt.subplots(figsize=(7, 4))
    df["behavior_type"].value_counts().plot.bar(ax=ax)
    ax.set_title("Behavior type counts (sampled)")
    ax.set_ylabel("Count")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "behavior_type.png", dpi=150)
    plt.close(fig)

    # 图 3：Top 10 类目（按行为数；类目只有 ID，没有中文名）
    top_cat = df["category_id"].value_counts().head(10)
    print("\nTop 10 类目（按行为数，类目 ID）：")
    print(top_cat)
    fig, ax = plt.subplots(figsize=(10, 5))
    top_cat.sort_values().plot.barh(ax=ax)
    ax.set_title("Top 10 categories by behavior count (category_id)")
    ax.set_xlabel("Behaviors")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "top_categories.png", dpi=150)
    plt.close(fig)

    # 图 4：小时趋势（浏览与购买分开画）
    hourly = df.groupby(["hour", "behavior_type"]).size().unstack(fill_value=0)
    fig, ax = plt.subplots(figsize=(10, 4))
    hourly["pv"].plot(ax=ax, label="pv")
    if "buy" in hourly:
        hourly["buy"].plot(ax=ax, label="buy")
    ax.set_title("Hourly trend: pv vs buy (sampled)")
    ax.set_xlabel("Hour of day")
    ax.set_ylabel("Count")
    ax.legend()
    fig.tight_layout()
    fig.savefig(FIG_DIR / "hourly_trend.png", dpi=150)
    plt.close(fig)

    # 图 5：每日购买转化率（购买数 / 浏览数）
    fig, ax = plt.subplots(figsize=(10, 4))
    daily["buy_rate_pv"].plot(ax=ax, marker="o")
    ax.set_title("Daily buy conversion rate (buy / pv, sampled)")
    ax.set_xlabel("Date")
    ax.set_ylabel("Buy / PV")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "daily_buy_conversion.png", dpi=150)
    plt.close(fig)

    print("已保存图表到 outputs/figures/：daily_pv_uv、behavior_type、"
          "top_categories、hourly_trend、daily_buy_conversion")
    print("\n完成。下一步：python notebooks/02_funnel_rfm_analysis.py")


if __name__ == "__main__":
    main()
