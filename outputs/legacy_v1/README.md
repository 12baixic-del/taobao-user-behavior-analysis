# 历史版本输出归档（v1）

> [!WARNING]
> 本目录保留的是 **v1 历史版本**（`e3350d5` 提交之前）的汇总 CSV 与可视化图表，仅用于历史版本追溯。
>
> **v1 存在已确认的计算口径偏差，不可作为新版（v2）分析结论：**
> 1. 采用按行随机抽样（`df.sample`），导致单个用户的行为序列被截断，漏斗与留存统计存在结构性偏差；
> 2. 时间解析未由 UTC 显式转换至北京时间（`Asia/Shanghai`, `UTC+8`），导致跨天日活、次日留存与 `days_since_last_active` 计算出现偏差（曾出现负数）；
> 3. RFM 中 `F_score` 使用 `rank(pct=True)`，导致 96%+ 零购买用户扎堆同分，5 类业务分层退化为 3 类；
> 4. 漏斗仅计算未限定时间顺序的行为覆盖人数（如旧版 `527,696` 人、`3.99%` 转化率等）。
>
> 新版 v2 请在获取原始 `UserBehavior.csv` 后重新运行 `notebooks/01_data_cleaning_eda.py` 与 `notebooks/02_funnel_rfm_analysis.py` 生成最新 `outputs/` 结果。
