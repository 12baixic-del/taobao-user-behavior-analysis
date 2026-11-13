# 抽样方法与 SQL 复现指南

本文档说明如何从原始数据独立复现样本与全部 11 条 SQL 查询（Q1–Q10、Q9b）。
复审时按以下步骤执行即可，不依赖任何私有文件或特定机器路径。

## 1. 原始数据

- 来源：Kaggle 公开镜像 `marwa80/userbehavior`（与阿里云天池 649 同源，
  免登录获取：数据集页 `totalBytes: 3672347465`，描述与天池逐字一致）。
- 全量：100,150,807 行，987,994 用户，4,162,024 商品，5 列
  （`user_id, item_id, category_id, behavior_type, timestamp`）。
- 行为分布：pv 89,716,264 / cart 5,530,446 / buy 2,015,839 / fav 2,888,258。
- 许可证：CC BY-NC-SA 4.0。**原始 CSV 不入库、不分发**（见 `.gitignore`）；
  仓库只提交由样本生成的汇总 CSV 与图表。
- 校验：下载后对照 `~/workspace/job-competitiveness/data/raw/DATA_VERIFICATION.md`
  核对行数、行为分布与时间戳口径（时间戳为北京时间 CST；
  窗内 100,095,231 行，窗外 55,576 行、0.055% 为脏行，已丢弃）。

## 2. 清洗与整群抽样

实现：`notebooks/01_data_cleaning_eda.py`

- 单遍流式分块读取（`stream_clean_and_sample`，`chunksize=500_000`），
  跨 chunk 去重，不一次性载入内存；全量 100,150,807 行约 57 秒。
- 时区：Unix 时间戳按北京时间（`Asia/Shanghai`，UTC+8）解析；
  有效窗口 `[2017-11-25 00:00:00, 2017-12-03 23:59:59] CST`。
- 抽样：按 `user_id` 的确定性哈希做**用户级整群抽样**
  （`_vectorized_user_hash`，固定种子 `SEED = 42`），整群保留入选用户的
  全部行为记录，不按行抽样（按行抽样会撕裂用户序列，是 v1 数字失真的根源）。
- 目标行数 `SAMPLE_N = 1_000_000`（自适应阈值，实际保留 1,009,774 行）。

复现命令（仓库根目录，原始 CSV 置于 `data/raw/UserBehavior.csv`）：

```bash
python notebooks/01_data_cleaning_eda.py
```

产物：`outputs/taobao_clean_sample.csv`（1,009,774 行，10,053 用户，
约 71MB，`.gitignore` 忽略，需本地生成；看板不读它，见 §4）。

## 3. 漏斗 / 留存 / R+F 计算

```bash
python notebooks/02_funnel_rfm_analysis.py   # 生成 funnel / sequential / retention / rfm 汇总
python scripts/gen_dashboard_csvs.py          # 生成 top_categories / nonpath_split / nonpath_totals / cross_item / dataset_summary
```

## 4. SQL 11 条查询复现

```bash
python sql/run_sqlite.py
```

- 仅用 Python 标准库：将 `outputs/taobao_clean_sample.csv` 载入内存 SQLite，
  逐条执行 `sql/queries.sql`（Q1–Q10、Q9b），打印制表符分隔结果。
- 无网络、无第三方依赖。`tests/test_sql_funnel.py` 另用合成小数据对
  Q3/Q9/Q9b 的时序规则与 Q2 的口径做单元级验证（见 §5）。

### 预期结果（Q2 覆盖口径，2026-11-14 修正版）

分子分母同一集合：`users` 与 `user_coverage_vs_pv` 只统计浏览用户集合
（10,008 人）；45 名无浏览记录的用户（35 人购买、42 人加购、24 人收藏）
不计入覆盖人数。`behaviors` 为全样本事件次数，与 Q6/Q7 的事件转化率一致。

| stage | users | behaviors | user_coverage_vs_pv |
|---|---|---|---|
| pv | 10,008 | 906,054 | 1.0000 |
| cart | 7,406 | 55,215 | 0.7400 |
| fav | 3,897 | 28,487 | 0.3894 |
| cart_or_fav | 8,626 | 83,702 | 0.8619 |
| buy | 6,811 | 20,018 | 0.6806 |

购买覆盖率 68.1%（6,811/10,008）。时序口径（Q3）不受本次修正影响：
pv → 意向 8,583（85.8%）→ 购买 5,336（意向→购买 62.2%，
用户级时序路径完成率 53.3%）；1,510 名购买用户未满足完整路径
（767 人无意向记录，743 人顺序不满足）；商品级跨商品核对 2,747 / 2,589 人
（同一商品意向须严格早于购买，使用窗口内全部事件，意向可在首次浏览之前；
旧集合交集规则给出 2,797 人，时间规则在过滤集上先剔除 82 人得 2,715 人，
放宽事件范围又加回 32 人，净变化 -50 人）。非路径购买占比
的分母是全部购买用户：1,510 / 6,846 = 22.1%（另有 PV 口径 1,475 / 6,811 = 21.7%
供参考），见 `outputs/funnel_nonpath_totals.csv`。同秒诊断按精确时间戳配对
计数（同一用户、同一商品、同一秒既有意向又有购买），真实样本为 0 对。

## 5. 自动化测试

```bash
python -m unittest discover -s tests -v   # 37 项：test_analysis.py 17 项 + test_sql_funnel.py 4 项 + test_cross_item.py 14 项 + test_dashboard.py 2 项
```

新增的 Q2 口径测试（`test_q2_coverage_restricted_to_pv_users` 与
`test_coverage_funnel_excludes_non_pv_users`）用同一组合成 6 人数据
（同秒 U2、反序 U3/U4、无意向 U5、无浏览 U6、跨商品 U1）验证：
无浏览用户不计入覆盖人数，但其事件仍计入 `behaviors`；
Q9b 的同秒、反序、无浏览、跨商品四类边界测试保留，全部通过。

新增的第三轮测试（`tests/test_cross_item.py`，14 项）用合成小数据验证：
P1 的 7 类场景（先意向后购买、先购买后收藏、多商品、同一商品多次意向与
多次购买的两种子情形、同秒、无意向直接购买、分类加总守恒）与 P0 的分母
回归（合成数据上旧错误算法给 3/3 = 100%，正确值 3/4 = 75%），以及已提交
小表内部一致性（分母总体、份额重算、跨商品加总 = 5,336 人）；另有意向在
首次浏览之前的范围回归、同秒诊断精确配对计数、非路径购买用户不计入
商品级分类三项补充测试。`tests/test_dashboard.py`（2 项）用 Streamlit
AppTest 直接渲染看板，断言显示的 22.1% / 51.5% 与数据集口径，守护
app.py 自身的分母与口径（生成器单测覆盖不到看板）。
