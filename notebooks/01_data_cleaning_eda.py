"""01 - 清洗 + EDA（单遍流式向量化清洗 + 自适应 user_id 整群抽样版）

在项目根目录运行：
    python notebooks/01_data_cleaning_eda.py

性能优化要点（不牺牲内存安全性）：
    1. 单遍分块流式读取（Single-Pass chunksize），消除两遍扫描的重复 CSV 解析开销；
    2. 窗口过滤直接采用整数 Unix 秒级边界比对，仅对最终抽样结果执行一次 tz_convert，
       大幅减少 datetime 对象创建与时区转换耗时；
    3. 采用向量化 32-bit 乘法哈希与自适应阈值淘汰，峰值内存恒定受控于 chunksize + ~1.25x target_rows，
       同时确保最终入选 user_id 的跨分块完整行为序列 100% 保留。
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 不开图形窗口，直接保存图片
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data" / "raw"
OUT_DIR = BASE / "outputs"
FIG_DIR = OUT_DIR / "figures"

DATA_FILE = DATA_DIR / "UserBehavior.csv"
COLUMNS = ["user_id", "item_id", "category_id", "behavior_type", "timestamp"]
BEHAVIOR_CAT_DTYPE = pd.CategoricalDtype(categories=["pv", "buy", "cart", "fav"])
READ_DTYPES = {
    "behavior_type": "str",
}
VALID_BEHAVIORS = {"pv", "buy", "cart", "fav"}
LOCAL_TZ = "Asia/Shanghai"

# 有效时间窗：2017-11-25 到 2017-12-03（北京时间 UTC+8）
START = pd.Timestamp("2017-11-25 00:00:00")
END = pd.Timestamp("2017-12-03 23:59:59")

# 目标抽样行数、固定种子与默认分块大小
SAMPLE_N = 1_000_000
SEED = 42
DEFAULT_CHUNKSIZE = 500_000


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
    print("  3. 详细步骤见 data/README.md")
    sys.exit(2)


def _window_epoch_bounds(start: pd.Timestamp, end: pd.Timestamp, tz: str) -> tuple[int, int]:
    """将本地时区起止时间转为 Unix 秒级整型闭区间 [start_epoch, end_epoch]。"""
    s_utc = start.tz_localize(tz).tz_convert("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    e_utc = end.tz_localize(tz).tz_convert("UTC") if end.tzinfo is None else end.tz_convert("UTC")
    return int(s_utc.timestamp()), int(e_utc.timestamp())


def _vectorized_user_hash(user_ids: np.ndarray, seed: int) -> np.ndarray:
    """向量化 32-bit 确定性用户哈希，返回 [0.0, 1.0) 浮点值（无 Python 循环）。"""
    u = np.asarray(user_ids, dtype=np.uint64)
    s = np.uint64(seed & 0xFFFFFFFF)
    mixed = ((u ^ (s * np.uint64(0x9E3779B1))) * np.uint64(2654435761)) & np.uint64(0xFFFFFFFF)
    return mixed.astype(np.float64) / 4294967296.0


def _epoch_to_local_naive(ts_series: pd.Series, tz: str = LOCAL_TZ) -> pd.Series:
    """将有效 Unix 秒级时间戳向量化转换为本地时区 naive datetime64[ns]。"""
    dt_utc = pd.to_datetime(ts_series, unit="s", utc=True, errors="coerce")
    return dt_utc.dt.tz_convert(tz).dt.tz_localize(None)


def clean_behavior_df(
    df: pd.DataFrame,
    start: pd.Timestamp = START,
    end: pd.Timestamp = END,
    tz: str = LOCAL_TZ,
    verbose: bool = True,
) -> pd.DataFrame:
    """清洗行为表：去重、去空、行为类型过滤，并按北京时间窗口过滤。"""
    if df.empty:
        out = df.copy()
        out["datetime"] = pd.Series(dtype="datetime64[ns]")
        return out

    if verbose:
        print(f"原始数据：{len(df):,} 行，{df.shape[1]} 列")
        print(f"去重前完全重复行：{int(df.duplicated().sum()):,} 行")

    start_epoch, end_epoch = _window_epoch_bounds(start, end, tz)
    ts_num = pd.to_numeric(df["timestamp"], errors="coerce")
    valid_mask = (
        df["user_id"].notna()
        & df["item_id"].notna()
        & df["category_id"].notna()
        & df["behavior_type"].isin(VALID_BEHAVIORS)
        & ts_num.notna()
        & (ts_num >= start_epoch)
        & (ts_num <= end_epoch)
    )

    cleaned = df.loc[valid_mask].drop_duplicates().copy()
    if cleaned.empty:
        cleaned["datetime"] = pd.Series(dtype="datetime64[ns]")
        cleaned["behavior_type"] = cleaned["behavior_type"].astype(BEHAVIOR_CAT_DTYPE)
        return cleaned

    cleaned["timestamp"] = ts_num.loc[cleaned.index].astype("int64")
    cleaned["datetime"] = _epoch_to_local_naive(cleaned["timestamp"], tz=tz)
    cleaned["behavior_type"] = cleaned["behavior_type"].astype(BEHAVIOR_CAT_DTYPE)
    return cleaned


def sample_by_user_id(
    df: pd.DataFrame,
    target_rows: int = SAMPLE_N,
    seed: int = SEED,
    verbose: bool = True,
) -> pd.DataFrame:
    """对内存中的 DataFrame 按 user_id 确定性整群抽样（全向量化）。"""
    if df.empty or len(df) <= target_rows:
        if verbose:
            print(f"行数不足 {target_rows:,} 行，不抽样，全量分析")
        return df.copy()

    unique_users = df["user_id"].drop_duplicates().to_numpy(dtype=np.int64)
    hashes = _vectorized_user_hash(unique_users, seed)
    ratio = target_rows / len(df)
    k = max(1, min(len(unique_users), round(len(unique_users) * ratio)))
    # 按向量化哈希选出前 k 名用户
    idx = np.argpartition(hashes, k - 1)[:k]
    selected_users = set(unique_users[idx].tolist())
    sampled = df[df["user_id"].isin(selected_users)].copy()

    if verbose:
        print(
            f"按 user_id 整群抽样：固定种子 {seed}，抽取 {len(selected_users):,}/{len(unique_users):,} 名用户，"
            f"共保留 {len(sampled):,} 条完整行为记录"
        )
    return sampled


def _empty_cleaned_df() -> pd.DataFrame:
    """构造与清洗结果结构、列类型一致的空 DataFrame。"""
    return pd.DataFrame({
        "user_id": pd.Series(dtype="int64"),
        "item_id": pd.Series(dtype="int64"),
        "category_id": pd.Series(dtype="int64"),
        "behavior_type": pd.Series(dtype=BEHAVIOR_CAT_DTYPE),
        "timestamp": pd.Series(dtype="int64"),
        "datetime": pd.Series(dtype="datetime64[ns]"),
    })


def _tighten_threshold(m_hash: np.ndarray, current_thresh: float, target_rows: int) -> float:
    """安全单调收紧哈希阈值：保证至少保留 1 名用户且在多用户时避免死循环。"""
    uniq_hashes = np.unique(m_hash)
    if len(uniq_hashes) <= 1:
        return current_thresh

    quantile_ratio = min(0.95, max(1e-6, target_rows / len(m_hash)))
    cand = float(np.quantile(m_hash, quantile_ratio))
    # 若单一大户导致分位数落到最小哈希处，提升至第二小哈希以至少保留 1 名用户
    if cand <= float(uniq_hashes[0]):
        cand = float(uniq_hashes[1])

    if cand < current_thresh:
        return cand

    # 若分位数未能严格低于 current_thresh，回退至当前阈值之下最大的唯一哈希
    below = uniq_hashes[uniq_hashes < current_thresh]
    if len(below) >= 2:
        return float(below[-1])
    return current_thresh


def stream_clean_and_sample(
    csv_path: Path,
    target_rows: int = SAMPLE_N,
    seed: int = SEED,
    chunksize: int = DEFAULT_CHUNKSIZE,
    start: pd.Timestamp = START,
    end: pd.Timestamp = END,
    tz: str = LOCAL_TZ,
    verbose: bool = True,
) -> pd.DataFrame:
    """单遍流式分块清洗与自适应 user_id 整群抽样（避免两遍扫描与全量 OOM）。"""
    if chunksize <= 0:
        raise ValueError(f"chunksize 必须为正整数，当前为 {chunksize}")
    if target_rows <= 0:
        return _empty_cleaned_df()

    start_epoch, end_epoch = _window_epoch_bounds(start, end, tz)
    thresh = 1.0
    high_water = max(target_rows + chunksize, int(target_rows * 1.25))
    kept_chunks: list[pd.DataFrame] = []
    kept_rows = 0
    raw_rows = 0

    reader = pd.read_csv(
        csv_path,
        header=None,
        names=COLUMNS,
        usecols=COLUMNS,
        dtype=READ_DTYPES,
        chunksize=chunksize,
    )

    for chunk in reader:
        raw_rows += len(chunk)
        # 与 clean_behavior_df 等价：数值列先容错转换，过滤缺失/非法值与时间窗
        uid_num = pd.to_numeric(chunk["user_id"], errors="coerce")
        iid_num = pd.to_numeric(chunk["item_id"], errors="coerce")
        cid_num = pd.to_numeric(chunk["category_id"], errors="coerce")
        ts_num = pd.to_numeric(chunk["timestamp"], errors="coerce")
        valid_mask = (
            uid_num.notna()
            & iid_num.notna()
            & cid_num.notna()
            & ts_num.notna()
            & chunk["behavior_type"].isin(VALID_BEHAVIORS)
            & (ts_num >= start_epoch)
            & (ts_num <= end_epoch)
        )
        if not valid_mask.any():
            continue

        sub = pd.DataFrame({
            "user_id": uid_num.loc[valid_mask].astype("int64"),
            "item_id": iid_num.loc[valid_mask].astype("int64"),
            "category_id": cid_num.loc[valid_mask].astype("int64"),
            "behavior_type": chunk.loc[valid_mask, "behavior_type"].astype(BEHAVIOR_CAT_DTYPE),
            "timestamp": ts_num.loc[valid_mask].astype("int64"),
        })

        # 按当前自适应哈希阈值直接过滤，保证后续 chunk 不保留已淘汰用户
        if thresh < 1.0:
            u_hash = _vectorized_user_hash(sub["user_id"].to_numpy(dtype=np.int64), seed)
            sub = sub.loc[u_hash < thresh].copy()
            if sub.empty:
                continue

        sub = sub.drop_duplicates()
        kept_chunks.append(sub)
        kept_rows += len(sub)

        # 当暂存行数超过高水位时，收紧哈希阈值并淘汰超阈值用户，封顶内存占用
        if kept_rows > high_water:
            merged = pd.concat(kept_chunks, ignore_index=True)
            m_hash = _vectorized_user_hash(merged["user_id"].to_numpy(dtype=np.int64), seed)
            new_thresh = _tighten_threshold(m_hash, thresh, target_rows)
            if new_thresh < thresh:
                thresh = new_thresh
                merged = merged.loc[m_hash < thresh].copy()
            kept_chunks = [merged]
            kept_rows = len(merged)

    if not kept_chunks:
        return _empty_cleaned_df()

    result = pd.concat(kept_chunks, ignore_index=True)
    # 最终若略高于 target_rows，按同一哈希单调收紧一次至接近 target_rows
    if len(result) > int(target_rows * 1.05):
        r_hash = _vectorized_user_hash(result["user_id"].to_numpy(dtype=np.int64), seed)
        final_thresh = _tighten_threshold(r_hash, thresh, target_rows)
        if final_thresh < thresh:
            result = result.loc[r_hash < final_thresh].copy()

    # 仅对最终留存的抽样结果执行一次跨块去重与北京时区转换
    result = result.drop_duplicates().reset_index(drop=True)
    result["datetime"] = _epoch_to_local_naive(result["timestamp"], tz=tz)
    result["behavior_type"] = result["behavior_type"].astype(BEHAVIOR_CAT_DTYPE)

    if verbose:
        print(
            f"单遍流式处理完成（chunksize={chunksize:,}）：共扫描 {raw_rows:,} 原始行，"
            f"保留 {result['user_id'].nunique():,} 名用户的 {len(result):,} 条完整行为记录"
        )
    return result


def compute_daily_summary(df: pd.DataFrame) -> pd.DataFrame:
    """计算每日 PV、UV、各行为数及转化率（含零分母与缺失行为防护）。"""
    if df.empty:
        cols = ["pv", "buy", "cart", "fav", "total", "uv", "buy_users", "buy_rate_pv", "buy_rate_uv"]
        empty_df = pd.DataFrame(columns=cols)
        empty_df.index.name = "date"
        return empty_df

    counts = df.groupby(["date", "behavior_type"], observed=False).size().unstack(fill_value=0)
    daily = pd.DataFrame(index=counts.index)
    for col in ("pv", "buy", "cart", "fav"):
        daily[col] = counts[col].astype(int) if col in counts.columns else 0
    daily["total"] = daily[["pv", "buy", "cart", "fav"]].sum(axis=1)
    daily["uv"] = df.groupby("date")["user_id"].nunique()
    buy_users = df[df["behavior_type"] == "buy"].groupby("date")["user_id"].nunique()
    daily["buy_users"] = buy_users.reindex(daily.index, fill_value=0).astype(int)
    daily["buy_rate_pv"] = (daily["buy"] / daily["pv"].replace(0, pd.NA)).astype("Float64")
    daily["buy_rate_uv"] = (daily["buy_users"] / daily["uv"].replace(0, pd.NA)).astype("Float64")
    daily.index.name = "date"
    return daily


def main() -> None:
    check_file()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    FIG_DIR.mkdir(parents=True, exist_ok=True)

    df = stream_clean_and_sample(
        DATA_FILE,
        target_rows=SAMPLE_N,
        seed=SEED,
        chunksize=DEFAULT_CHUNKSIZE,
        start=START,
        end=END,
        tz=LOCAL_TZ,
        verbose=True,
    )

    if df.empty:
        print("清洗后无有效数据，跳过汇总与绘图。")
        return

    df["date"] = df["datetime"].dt.date
    df["hour"] = df["datetime"].dt.hour

    print(f"\n抽样后保留：{len(df):,} 行，用户数 {df['user_id'].nunique():,} 人，"
          f"商品数 {df['item_id'].nunique():,} 个，类目数 {df['category_id'].nunique():,} 个")
    print("（注：样本数据仅用于比例与结构分析，不可直接等同于全量总体规模）")

    df.to_csv(OUT_DIR / "taobao_clean_sample.csv", index=False)
    print("\n已保存：outputs/taobao_clean_sample.csv")

    daily = compute_daily_summary(df)
    daily.to_csv(OUT_DIR / "daily_summary.csv")
    print("\n每日汇总（PV / UV / 购买转化率，北京时间）：")
    print(daily.round(4))

    plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=(10, 4))
    daily["pv"].plot(ax=ax, label="PV")
    daily["uv"].plot(ax=ax, label="UV")
    ax.set_title("Daily PV and UV (user-sampled, CST)")
    ax.set_xlabel("Date")
    ax.set_ylabel("Count")
    ax.legend()
    fig.tight_layout()
    fig.savefig(FIG_DIR / "daily_pv_uv.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4))
    df["behavior_type"].value_counts().plot.bar(ax=ax)
    ax.set_title("Behavior type counts (user-sampled)")
    ax.set_ylabel("Count")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "behavior_type.png", dpi=150)
    plt.close(fig)

    top_cat = df["category_id"].value_counts().head(10)
    fig, ax = plt.subplots(figsize=(10, 5))
    top_cat.sort_values().plot.barh(ax=ax)
    ax.set_title("Top 10 categories by behavior count (category_id)")
    ax.set_xlabel("Behaviors")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "top_categories.png", dpi=150)
    plt.close(fig)

    hourly = df.groupby(["hour", "behavior_type"], observed=False).size().unstack(fill_value=0)
    fig, ax = plt.subplots(figsize=(10, 4))
    if "pv" in hourly:
        hourly["pv"].plot(ax=ax, label="pv")
    if "buy" in hourly:
        hourly["buy"].plot(ax=ax, label="buy")
    ax.set_title("Hourly trend: pv vs buy (user-sampled, CST)")
    ax.set_xlabel("Hour of day (UTC+8)")
    ax.set_ylabel("Count")
    if ax.get_legend_handles_labels()[0]:
        ax.legend()
    fig.tight_layout()
    fig.savefig(FIG_DIR / "hourly_trend.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 4))
    daily["buy_rate_pv"].astype(float).fillna(0).plot(ax=ax, marker="o")
    ax.set_title("Daily buy conversion rate (buy / pv, user-sampled, CST)")
    ax.set_xlabel("Date")
    ax.set_ylabel("Buy / PV")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "daily_buy_conversion.png", dpi=150)
    plt.close(fig)

    print("已保存图表到 outputs/figures/，下一步：python notebooks/02_funnel_rfm_analysis.py")


if __name__ == "__main__":
    main()
