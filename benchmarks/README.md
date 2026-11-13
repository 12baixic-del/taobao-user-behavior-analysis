# 性能基准测试说明（Benchmarks）

本目录保存 v2 单遍流式清洗抽样方案与历史方案的公平对比脚本及实测记录，用于在无本地全量 `UserBehavior.csv` 时验证内存安全性与计算吞吐扩展性。

## 1. 测试环境与测量口径

- **测试机器**：Windows 11 (`Windows-11-10.0.26200-SP0`)，22 核逻辑 CPU，15.5 GB RAM
- **Python 版本**：Python `3.13.14`
- **数据来源与采样方法**：
  - 使用 `benchmark_streaming.py` 以固定随机种子 `seed=42` 分块生成合成 5 列 CSV（`user_id, item_id, category_id, behavior_type, timestamp`），时间范围覆盖 `2017-11-25 00:00:00 ~ 2017-12-03 23:59:59 (UTC+8)`，行为比例约为 `pv:cart:fav:buy = 0.89:0.055:0.03:0.025`；
  - **严格声明**：以下全部基准数据均为**合成数据**测得，**不得**描述为一亿行真实淘宝 `UserBehavior.csv` 的性能。
- **测量口径**：
  - 每轮测试均在**独立子进程**中运行，隔离 Python 进程缓存干扰；
  - 耗时 `elapsed_s` 覆盖 CSV 读取、清洗、抽样、下游双口径漏斗与 RFM 计算；
  - 内存通过 `psutil.Process().memory_info().peak_wset`（Windows 进程生命周期峰值工作集）与 `delta_peak_mb`（峰值相对基线增量）统计；
  - 10 万行横向对比每组重复运行 3 次取均值；分级压测按 `10 万`、`100 万`、`1000 万` 行执行，并通过 `verify_user_completeness()` 重扫完整 CSV 校验入选 `user_id` 序列 `100%` 完整保留（`completeness_diff_rows == 0`）。

## 2. 实测结果摘要（`benchmark_phase5_results.json`）

### A. 10 万行横向对比（`target_rows=20,000`, 3 次均值）

| 方案 | 平均耗时 | 平均峰值工作集 | 平均内存增量 | 留存行数 / 用户数 |
| :--- | :---: | :---: | :---: | :---: |
| `legacy_full`（全量读入 + 按用户抽样） | 0.0849 s | 127.62 MB | +28.29 MB | 20,125 行 / 1,000 人 |
| `phase4_twopass`（两遍流式 + `crc32`） | 0.1568 s | 130.49 MB | +30.91 MB | 18,671 行 / 936 人 |
| **`phase5_singlepass`（单遍向量化流式）** | **0.0964 s** | **123.20 MB** | **+23.60 MB** | 19,985 行 / 993 人 |

### B. 分级规模压测（`target_rows=100,000`, `chunksize=500,000`）

| 数据规模 (CSV 大小) | 方案 | 耗时 | 峰值工作集 | 内存增量 | 入选用户序列完整性 |
| :--- | :--- | :---: | :---: | :---: | :---: |
| 10 万行 (2.92 MB) | `phase5_singlepass` | 0.0801 s | 127.80 MB | +28.45 MB | 100% 完整 (`diff=0`) |
| 100 万行 (30.15 MB) | `phase4_twopass` | 1.0955 s | 207.37 MB | +108.21 MB | — |
| 100 万行 (30.15 MB) | **`phase5_singlepass`** | **0.6678 s** | 241.45 MB | +141.24 MB | 100% 完整 (`diff=0`) |
| 1000 万行 (310.99 MB) | **`phase5_singlepass`** | **5.3018 s** | **249.89 MB** | **+149.90 MB** | 100% 完整 (`diff=0`) |

## 3. 复现命令

```bash
python benchmarks/benchmark_streaming.py
```
