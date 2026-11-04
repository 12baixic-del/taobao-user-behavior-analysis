# 数据项目：淘宝用户行为分析（转化漏斗 + 留存 + RFM）

## 业务问题

一家淘宝店铺 / 平台想知道：用户从浏览到购买是怎么流失的，哪些用户最值得挽留。
用真实淘宝用户行为数据回答，不用编数字。

## 数据集

淘宝用户行为数据集（User Behavior Data from Taobao for Recommendation），阿里巴巴提供，阿里云天池 649

- 链接：<https://tianchi.aliyun.com/dataset/649>
- 规模：987,994 名用户，100,150,807 条行为记录，2017-11-25 至 2017-12-03
- 字段（只有 5 列，没有价格列）：
  `user_id` 用户 ID、`item_id` 商品 ID、`category_id` 类目 ID、
  `behavior_type` 行为类型（`pv` 浏览 / `buy` 购买 / `cart` 加购 / `fav` 收藏）、
  `timestamp` 行为时间戳（秒）
- 许可：CC BY-NC-SA 4.0（非商业、学习用途）

下载方法见 `data/README.md`，把 `UserBehavior.csv` 放到 `data/raw/`。

## 结果（已跑通，抽样约 100 万行，种子 42）

完整结论见 [REPORT.md](REPORT.md)。核心数字：

- 漏斗：浏览 493,204 人 → 加购 51,289 人（10.40%）→ 收藏 26,055 人（50.80%）→ 购买 19,686 人（75.56%），整体浏览→购买 3.99%
- 次日留存：8 天均值约 15.4%，12 月 1 日最高 17.25%
- RFM：重要价值用户 8,151 人（1.54%），流失风险的高价值用户 6,586 人（1.25%），一般用户 512,959 人（97.20%）

## 怎么做

| 步骤 | 脚本 | 内容 |
|---|---|---|
| 1. 清洗 + EDA | `notebooks/01_data_cleaning_eda.py` | 时间戳解析、超范围时间剔除、行为类型检查、抽样约 100 万行、日 PV/UV、行为分布、类目 Top、小时趋势 |
| 2. 漏斗 + 留存 + RFM | `notebooks/02_funnel_rfm_analysis.py` | 转化漏斗 pv→cart→fav→buy、次日留存、RFM 分层（R=距上次活跃天数、F=购买次数，无 M，因为没有价格列，不编金额） |

## 运行

```bash
pip install -r requirements.txt
python notebooks/01_data_cleaning_eda.py
python notebooks/02_funnel_rfm_analysis.py
```

两个脚本都用相对路径，在项目根目录运行即可。数据没下载时会停下并打印下载方法。

原始数据 1 亿条太多，脚本会固定随机种子（42）抽样约 100 万行再分析，
所有数字都是抽样后的结果，报告里要写明这一点，不要写成全量。

## 产出（在 `outputs/`）

- `taobao_clean_sample.csv`：清洗 + 抽样后的行为记录（约 100 万行，仅本地，不入库）
- `daily_summary.csv`：每日 PV、UV、各行为数、购买转化率
- `funnel_summary.csv`：漏斗各环节人数、行为数、转化率
- `retention_summary.csv`：每日次日留存率
- `rfm_users.csv`：每个用户的 R / F 值、得分与分层
- `figures/*.png`：日 PV/UV、行为类型占比、Top 类目、小时趋势、购买转化、漏斗、留存、RFM 分层

## 技术栈

Python + Pandas + NumPy + Matplotlib（Seaborn 可选）。

## 局限（写报告时要写上）

- 数据只有 9 天（2017-11-25 至 2017-12-03，双 12 预热期），看不到长期复购。
- 没有价格、没有订单金额，做不了 GMV 和 RFM 的 M，只能用购买次数代替消费金额。
- 类目只有 ID，没有类目名称，Top 类目只能报 ID，不能编中文类目名。
