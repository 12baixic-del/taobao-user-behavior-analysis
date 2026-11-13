"""第五阶段公平基准测试与分级压测脚本（10万 / 100万 / 1000万行合成数据）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
import psutil

ROOT = Path(__file__).resolve().parent
FIXED_NB_DIR = ROOT.parent / "notebooks"
sys.path.insert(0, str(FIXED_NB_DIR))

import importlib.util


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


eda = _load_module("eda", FIXED_NB_DIR / "01_data_cleaning_eda.py")
rfm_mod = _load_module("rfm_mod", FIXED_NB_DIR / "02_funnel_rfm_analysis.py")


def generate_synthetic_csv(csv_path: Path, n_rows: int, seed: int = 42, chunk_rows: int = 500_000) -> None:
    """使用固定随机种子分块生成合成 UserBehavior.csv（无表头，5 列）。"""
    rng = np.random.default_rng(seed)
    n_users = max(1_000, n_rows // 20)
    # 时间范围覆盖 2017-11-25 00:00:00 ~ 2017-12-03 23:59:59 (UTC+8)
    start_ts = 1511539200  # 2017-11-25 00:00:00 UTC+8
    end_ts = 1512316799    # 2017-12-03 23:59:59 UTC+8
    behaviors = np.array(["pv", "cart", "fav", "buy"])
    probs = [0.89, 0.055, 0.03, 0.025]

    remaining = n_rows
    first = True
    while remaining > 0:
        cur = min(remaining, chunk_rows)
        df_chunk = pd.DataFrame({
            "user_id": rng.integers(1, n_users + 1, size=cur, dtype=np.int64),
            "item_id": rng.integers(1, 100_000, size=cur, dtype=np.int64),
            "category_id": rng.integers(1, 5_000, size=cur, dtype=np.int64),
            "behavior_type": rng.choice(behaviors, size=cur, p=probs),
            "timestamp": rng.integers(start_ts, end_ts + 1, size=cur, dtype=np.int64),
        })
        df_chunk.to_csv(csv_path, mode="w" if first else "a", header=False, index=False)
        first = False
        remaining -= cur


def phase4_two_pass_stream(csv_path: Path, target_rows: int = 50_000, seed: int = 42, chunksize: int = 100_000) -> pd.DataFrame:
    """复现第四阶段的两遍流式 + crc32 逐元素抽样，用于公平对比。"""
    pass1_chunks = []
    total_valid = 0
    for chunk in pd.read_csv(
        csv_path,
        names=eda.COLUMNS,
        header=None,
        dtype=eda.READ_DTYPES,
        chunksize=chunksize,
    ):
        cleaned = eda.clean_behavior_df(chunk, verbose=False)
        if not cleaned.empty:
            total_valid += len(cleaned)
            pass1_chunks.append(cleaned[["user_id"]])
    if total_valid == 0:
        return pd.DataFrame(columns=[*eda.COLUMNS, "datetime", "date", "hour"])

    all_uids = pd.concat(pass1_chunks, ignore_index=True)["user_id"].unique()
    ratio = min(1.0, target_rows / total_valid)
    selected = set()
    for uid in all_uids:
        h = zlib.crc32(f"{seed}:{int(uid)}".encode("utf-8")) / 0xFFFFFFFF
        if h <= ratio:
            selected.add(int(uid))

    out_chunks = []
    for chunk in pd.read_csv(
        csv_path,
        names=eda.COLUMNS,
        header=None,
        dtype=eda.READ_DTYPES,
        chunksize=chunksize,
    ):
        sub = chunk[chunk["user_id"].isin(selected)]
        if not sub.empty:
            out_chunks.append(eda.clean_behavior_df(sub, verbose=False))
    if not out_chunks:
        return pd.DataFrame(columns=[*eda.COLUMNS, "datetime", "date", "hour"])
    out = pd.concat(out_chunks, ignore_index=True)
    return out.drop_duplicates().reset_index(drop=True)


def run_worker(mode: str, csv_path: str, target_rows: int, chunksize: int) -> dict:
    """在独立子进程中执行单次基准测试并测量真实峰值 RSS。"""
    proc = psutil.Process(os.getpid())
    base_rss = proc.memory_info().rss
    peak_rss = base_rss
    t0 = time.perf_counter()

    p = Path(csv_path)
    if mode == "legacy_full":
        raw = pd.read_csv(p, names=eda.COLUMNS, header=None, dtype=eda.READ_DTYPES)
        cleaned = eda.clean_behavior_df(raw, verbose=False)
        res = eda.sample_by_user_id(cleaned, target_rows=target_rows, seed=42, verbose=False)
    elif mode == "phase4_twopass":
        res = phase4_two_pass_stream(p, target_rows=target_rows, seed=42, chunksize=chunksize)
    elif mode == "phase5_singlepass":
        res = eda.stream_clean_and_sample(p, target_rows=target_rows, seed=42, chunksize=chunksize, verbose=False)
    else:
        raise ValueError(f"Unknown mode: {mode}")

    elapsed = time.perf_counter() - t0
    peak_rss = max(peak_rss, proc.memory_info().rss)
    # Windows 上可用 peak_wset 更准确反映子进程生命周期内的峰值工作集
    mem_info = proc.memory_info()
    peak_wset = getattr(mem_info, "peak_wset", peak_rss)

    # 运行下游漏斗与 RFM，验证端到端计算
    cov = rfm_mod.compute_coverage_funnel(res)
    seq = rfm_mod.compute_sequential_funnel(res)
    obs_date = res["datetime"].dt.date.max() if not res.empty else rfm_mod.OBSERVE_DATE
    rfm, seg = rfm_mod.compute_rfm(res, observe_date=obs_date)

    return {
        "mode": mode,
        "elapsed_s": round(elapsed, 4),
        "base_rss_mb": round(base_rss / (1024 * 1024), 2),
        "end_rss_mb": round(mem_info.rss / (1024 * 1024), 2),
        "peak_wset_mb": round(peak_wset / (1024 * 1024), 2),
        "delta_peak_mb": round((peak_wset - base_rss) / (1024 * 1024), 2),
        "out_rows": int(len(res)),
        "out_users": int(res["user_id"].nunique()) if not res.empty else 0,
        "cov_buy_users": int(cov.loc[cov["stage"] == "buy", "users"].iloc[0]) if not cov.empty else 0,
        "seq_buy_users": int(seq.loc[seq["step"] == "intent_to_buy", "users"].iloc[0]) if not seq.empty else 0,
        "rfm_segments": int(len(seg)),
        "rfm_min_days": int(rfm["days_since_last_active"].min()) if not rfm.empty else 0,
    }


def invoke_subprocess(mode: str, csv_path: Path, target_rows: int, chunksize: int) -> dict:
    cmd = [
        sys.executable,
        __file__,
        "--worker",
        mode,
        str(csv_path),
        str(target_rows),
        str(chunksize),
    ]
    out = subprocess.check_output(cmd, text=True)
    return json.loads(out.strip().splitlines()[-1])


def verify_user_completeness(csv_path: Path, sampled_df: pd.DataFrame) -> tuple[bool, int]:
    """核对流式抽样保留的每一位用户在全量 CSV 中的全部有效记录是否 100% 完整。"""
    if sampled_df.empty:
        return True, 0
    sel_users = set(sampled_df["user_id"].unique().tolist())
    start_ts, end_ts = eda._window_epoch_bounds(eda.START, eda.END, eda.LOCAL_TZ)
    expected_rows = []
    for chunk in pd.read_csv(csv_path, names=eda.COLUMNS, header=None, dtype=eda.READ_DTYPES, chunksize=500_000):
        sub = chunk[chunk["user_id"].isin(sel_users)]
        if sub.empty:
            continue
        sub = sub[sub["behavior_type"].isin(eda.VALID_BEHAVIORS)]
        sub = sub[(sub["timestamp"] >= start_ts) & (sub["timestamp"] <= end_ts)]
        if not sub.empty:
            expected_rows.append(sub)
    expected = pd.concat(expected_rows, ignore_index=True).drop_duplicates()
    return len(expected) == len(sampled_df), len(expected) - len(sampled_df)


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "--worker":
        res = run_worker(sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]))
        print(json.dumps(res, ensure_ascii=False))
        return

    results = {"fair_comparison_100k": {}, "tiered_scale_tests": []}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        # 1. 10 万行公平基准（3 种方法，各重复 3 次，独立子进程）
        csv_100k = tmp_dir / "synth_100k.csv"
        generate_synthetic_csv(csv_100k, 100_000, seed=42)
        for mode in ("legacy_full", "phase4_twopass", "phase5_singlepass"):
            runs = [invoke_subprocess(mode, csv_100k, target_rows=20_000, chunksize=50_000) for _ in range(3)]
            results["fair_comparison_100k"][mode] = {
                "runs": runs,
                "avg_elapsed_s": round(sum(r["elapsed_s"] for r in runs) / 3, 4),
                "avg_peak_wset_mb": round(sum(r["peak_wset_mb"] for r in runs) / 3, 2),
                "avg_delta_peak_mb": round(sum(r["delta_peak_mb"] for r in runs) / 3, 2),
            }

        # 核对 legacy_full 与 phase5_singlepass 在 100k 上的抽样规模接近度与用户记录 100% 完整性
        legacy_df = eda.sample_by_user_id(
            eda.clean_behavior_df(pd.read_csv(csv_100k, names=eda.COLUMNS, header=None, dtype=eda.READ_DTYPES), verbose=False),
            target_rows=20_000, seed=42, verbose=False,
        )
        p5_df = eda.stream_clean_and_sample(csv_100k, target_rows=20_000, seed=42, chunksize=50_000, verbose=False)
        results["sampling_comparison_100k"] = {
            "legacy_rows": len(legacy_df),
            "phase5_rows": len(p5_df),
            "legacy_users": int(legacy_df["user_id"].nunique()),
            "phase5_users": int(p5_df["user_id"].nunique()),
            "all_users_100pct_complete": verify_user_completeness(csv_100k, p5_df)[0],
        }

        # 2. 分级压测：10 万、100 万、1000 万行（在 100 万行额外对比 phase4_twopass）
        for n_rows in (100_000, 1_000_000, 10_000_000):
            csv_path = tmp_dir / f"synth_{n_rows}.csv"
            t_gen0 = time.perf_counter()
            generate_synthetic_csv(csv_path, n_rows, seed=42)
            gen_s = round(time.perf_counter() - t_gen0, 2)
            file_mb = round(csv_path.stat().st_size / (1024 * 1024), 2)

            run_info = invoke_subprocess("phase5_singlepass", csv_path, target_rows=100_000, chunksize=500_000)
            p4_info = (
                invoke_subprocess("phase4_twopass", csv_path, target_rows=100_000, chunksize=500_000)
                if n_rows == 1_000_000
                else None
            )
            sampled = eda.stream_clean_and_sample(csv_path, target_rows=100_000, seed=42, chunksize=500_000, verbose=False)
            complete_ok, diff_rows = verify_user_completeness(csv_path, sampled)

            entry = {
                "n_rows": n_rows,
                "csv_size_mb": file_mb,
                "gen_s": gen_s,
                "benchmark": run_info,
                "all_selected_users_complete": complete_ok,
                "completeness_diff_rows": diff_rows,
            }
            if p4_info is not None:
                entry["phase4_twopass_baseline"] = p4_info
            results["tiered_scale_tests"].append(entry)
            csv_path.unlink(missing_ok=True)

    out_json = ROOT / "benchmark_phase5_results.json"
    out_json.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Benchmark finished. Saved to {out_json}")
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
