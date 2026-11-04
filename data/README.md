# 数据下载说明（淘宝用户行为）

数据不放在仓库里，请按下面步骤自己下载。

## 数据集

淘宝用户行为数据集（User Behavior Data from Taobao for Recommendation），阿里巴巴提供

- 链接：<https://tianchi.aliyun.com/dataset/649>
- 规模：987,994 名用户，100,150,807 条行为记录，2017-11-25 至 2017-12-03
- 文件：`UserBehavior.csv`（约 1 亿行，解压后约 3-4 GB，注意磁盘空间）
- 字段：`user_id`、`item_id`、`category_id`、`behavior_type`（pv / buy / cart / fav）、`timestamp`（秒级时间戳）
- 许可：CC BY-NC-SA 4.0（非商业、学习用途）

## 下载方法

1. 打开上面的天池链接，用淘宝账号登录（没有账号先注册一个）。
2. 点下载，得到压缩包，解压后把 `UserBehavior.csv` 放到本目录 `data/raw/`。
3. 文件很大，下载可能需要一段时间，不要中途关浏览器。

## 抽样说明（重要）

原始文件约 1 亿行，直接全量读入很慢、内存也不够。
脚本 `notebooks/01_data_cleaning_eda.py` 会先读入、清洗，再用固定随机种子（42）
抽样约 1,000,000 行做后续分析。抽样后的结果要写明是“抽样约 100 万行”，
不要写成全量 1 亿行的数字。

如果你的电脑内存较小（8 GB 以下），可以先只取前 1,000,000 行练手：
在脚本里把抽样数改小即可，改完在报告里写明改后的行数。

## 下载完检查

`data/raw/` 里应该有这个文件：

```text
UserBehavior.csv
```

然后回到项目根目录运行：

```bash
python notebooks/01_data_cleaning_eda.py
```

脚本会先打印原始行数、清洗掉多少超范围时间戳、抽样后多少行，
再输出每日 PV/UV、行为分布、类目 Top 和小时趋势图。
