# 淘宝用户行为分析

这是我做的一个数据分析练习，用的天池上的淘宝用户行为数据。主要是想看看用户从浏览到下单，中间都在哪一步流失了，再顺便练一下漏斗、留存和 RFM。

数据是 2017-11-25 到 12-03 的，9 天，正好是双 12 预热期。原始数据有 987,994 个用户，1 亿多条记录，我电脑跑不动全量，就固定种子 42 抽了大概 100 万行，最后样本是 527,696 人。下面报告里的数都是这个样本算出来的，不是全量。

数据来源：阿里云天池 649，https://tianchi.aliyun.com/dataset/649 ，CC BY-NC-SA 4.0，只能学习用。

数据只有 5 列：user_id、item_id、category_id、behavior_type（pv 浏览 / cart 加购 / fav 收藏 / buy 购买）、timestamp。没有价格那一列，所以做不了金额，也做不了 RFM 里的 M，我是拿购买次数代替的。类目也只有 ID，没有名字，Top 类目只能写 ID。

跑完大概是这样：

浏览 493,204 人，加购 51,289 人，10.40%；收藏 26,055 人，50.80%；购买 19,686 人，75.56%。整体浏览到购买是 3.99%，流失最大的一步在浏览到加购。

次日留存平均 15.4% 左右，12 月 1 日最高，17.25%。

RFM 分出来高价值用户 8,151 人，占 1.54%，其中快流失的还有 6,586 人，占 1.25%。这部分我觉得最值得看，就是要维护的那批人。

怎么跑，在项目根目录：

```bash
pip install -r requirements.txt
python notebooks/01_data_cleaning_eda.py
python notebooks/02_funnel_rfm_analysis.py
```

01 是清洗和 EDA，处理缺失值、重复值，时间戳转成日期，顺便看了每天的 PV/UV、行为分布、类目 Top 和小时趋势。02 是漏斗、留存和 RFM。

跑出来的结果都在 `outputs/` 里，csv 和图都在。详细结论在 [REPORT.md](REPORT.md)，里面有 funnel_summary、retention_summary、rfm_users 这些表。

用的就是 Python + Pandas + NumPy，画图用 Matplotlib，Seaborn 偶尔用一下。

这个项目不足的地方我自己也清楚：数据只有 9 天，看不到长期复购；没有金额；类目没名字。报告里都写了，没藏着。
